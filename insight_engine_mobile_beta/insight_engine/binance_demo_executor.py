from __future__ import annotations

import csv
import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, Optional, Tuple

from .virtual_trader import VirtualTrade


@dataclass
class DemoExecutionConfig:
    """Binance USD-M Futures TESTNET execution settings.

    This adapter is intentionally demo/testnet-only. It mirrors local paper-bot
    entries and exits to Binance Futures testnet when the user explicitly turns
    on API Demo mode in the GUI.
    """

    enabled: bool = False
    base_url: str = "https://demo-fapi.binance.com"
    api_key_env: str = "BINANCE_DEMO_API_KEY"
    api_secret_env: str = "BINANCE_DEMO_API_SECRET"
    recv_window: int = 5000
    log_path: str = "signals/api_demo_orders.csv"
    place_market_entries: bool = True
    close_with_reduce_only: bool = True
    # Keep the first prototype conservative. Leverage/margin can be set manually
    # in the testnet UI or in a later version once symbol-specific rules are stable.
    auto_set_leverage: bool = False


@dataclass
class DemoExecutionResult:
    ok: bool
    message: str
    payload: Optional[dict] = None


class BinanceFuturesDemoExecutor:
    """Small synchronous Binance Futures testnet execution bridge.

    v0.1 deliberately mirrors only MARKET open and reduce-only MARKET close.
    The local INSIGHT paper bot remains the source of TP/SL decisions, so the
    exchange testnet order is a demo execution proof-of-connection rather than
    a standalone risk manager.
    """

    def __init__(self, config: Optional[DemoExecutionConfig] = None):
        self.config = config or DemoExecutionConfig()
        self.api_key = os.environ.get(self.config.api_key_env, "").strip()
        self.api_secret = os.environ.get(self.config.api_secret_env, "").strip()
        self.symbol_rules: Dict[str, dict] = {}
        self.demo_order_by_trade_id: Dict[int, dict] = {}

    @property
    def has_keys(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def summary_text(self) -> str:
        state = "ON" if self.config.enabled else "OFF"
        keys = "keys loaded" if self.has_keys else "keys missing"
        mirrored = len(self.demo_order_by_trade_id)
        return f"API Demo {state} | Binance Futures Testnet | {keys} | mirrored trades {mirrored}"

    def test_connection(self) -> DemoExecutionResult:
        """Check public testnet time and, if keys exist, account endpoint."""
        try:
            server_time = self._public_request("GET", "/fapi/v1/time")
        except Exception as exc:
            return DemoExecutionResult(False, f"API Demo test failed: public testnet not reachable ({exc}).")

        if not self.has_keys:
            return DemoExecutionResult(
                False,
                "API Demo public testnet reachable, but keys are missing. Set BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET.",
                server_time,
            )

        try:
            account = self._signed_request("GET", "/fapi/v2/account", {})
            wallet = account.get("totalWalletBalance") or account.get("totalMarginBalance") or "--"
            return DemoExecutionResult(True, f"API Demo connected to Binance Futures Testnet. Demo wallet balance: {wallet} USDT.", account)
        except Exception as exc:
            return DemoExecutionResult(False, f"API Demo key/account test failed: {exc}", server_time)

    def open_from_virtual_trade(self, trade: VirtualTrade) -> DemoExecutionResult:
        if not self.config.enabled:
            return DemoExecutionResult(False, "API Demo is OFF; local paper trade was not mirrored.")
        if not self.has_keys:
            return DemoExecutionResult(False, "API Demo skipped: missing BINANCE_DEMO_API_KEY / BINANCE_DEMO_API_SECRET.")
        if trade.trade_id in self.demo_order_by_trade_id:
            return DemoExecutionResult(False, f"API Demo skipped: trade #{trade.trade_id} already has a mirrored testnet order.")
        if not self.config.place_market_entries:
            return DemoExecutionResult(False, "API Demo skipped: market entries disabled in config.")

        try:
            qty = self._format_quantity(trade.symbol, trade.quantity)
            if float(qty) <= 0:
                return DemoExecutionResult(False, f"API Demo skipped: formatted quantity is zero for {trade.symbol}.")
            side = "BUY" if trade.direction == "long" else "SELL"
            params = {
                "symbol": trade.symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty,
                "newClientOrderId": self._client_id("OPEN", trade.trade_id),
            }
            order = self._signed_request("POST", "/fapi/v1/order", params)
            self.demo_order_by_trade_id[trade.trade_id] = {
                "symbol": trade.symbol,
                "direction": trade.direction,
                "quantity": qty,
                "open_order": order,
            }
            self._append_order_row(trade.trade_id, trade.symbol, "OPEN", side, qty, order)
            order_id = order.get("orderId", "--")
            avg_price = order.get("avgPrice") or order.get("price") or "market"
            return DemoExecutionResult(True, f"API Demo OPEN mirrored #{trade.trade_id} {trade.symbol} {side} qty {qty} | testnet order {order_id} @ {avg_price}.", order)
        except Exception as exc:
            return DemoExecutionResult(False, f"API Demo OPEN failed for #{trade.trade_id} {trade.symbol}: {exc}")

    def close_virtual_trade(self, trade: VirtualTrade, reason: str = "LOCAL CLOSE") -> DemoExecutionResult:
        if not self.config.enabled:
            return DemoExecutionResult(False, "API Demo is OFF; no mirrored testnet close sent.")
        ref = self.demo_order_by_trade_id.get(trade.trade_id)
        if not ref:
            return DemoExecutionResult(False, f"API Demo close skipped: no mirrored testnet order found for trade #{trade.trade_id}.")
        if not self.has_keys:
            return DemoExecutionResult(False, "API Demo close skipped: keys missing.")

        try:
            side = "SELL" if ref.get("direction") == "long" else "BUY"
            qty = ref.get("quantity") or self._format_quantity(trade.symbol, trade.quantity)
            params = {
                "symbol": trade.symbol,
                "side": side,
                "type": "MARKET",
                "quantity": qty,
                "newClientOrderId": self._client_id("CLOSE", trade.trade_id),
            }
            if self.config.close_with_reduce_only:
                params["reduceOnly"] = "true"
            order = self._signed_request("POST", "/fapi/v1/order", params)
            ref["close_order"] = order
            ref["closed_reason"] = reason
            self._append_order_row(trade.trade_id, trade.symbol, f"CLOSE:{reason}", side, qty, order)
            order_id = order.get("orderId", "--")
            self.demo_order_by_trade_id.pop(trade.trade_id, None)
            return DemoExecutionResult(True, f"API Demo CLOSE mirrored #{trade.trade_id} {trade.symbol} {side} qty {qty} | testnet order {order_id}.", order)
        except Exception as exc:
            return DemoExecutionResult(False, f"API Demo CLOSE failed for #{trade.trade_id} {trade.symbol}: {exc}")

    def _client_id(self, action: str, trade_id: int) -> str:
        # Keep it short for Binance clientOrderId constraints.
        return f"INS{action[:1]}{trade_id}{int(time.time()) % 1000000}"

    def _format_quantity(self, symbol: str, qty: float) -> str:
        rules = self._get_symbol_rules(symbol)
        step = rules.get("stepSize") or "0.001"
        min_qty = Decimal(str(rules.get("minQty") or step))
        step_dec = Decimal(str(step))
        qty_dec = Decimal(str(qty))
        if step_dec <= 0:
            return str(qty)
        rounded = (qty_dec / step_dec).to_integral_value(rounding=ROUND_DOWN) * step_dec
        if rounded < min_qty:
            rounded = min_qty
        # Normalize without scientific notation.
        return format(rounded.normalize(), "f")

    def _get_symbol_rules(self, symbol: str) -> dict:
        if symbol in self.symbol_rules:
            return self.symbol_rules[symbol]
        data = self._public_request("GET", "/fapi/v1/exchangeInfo", {"symbol": symbol})
        row = (data.get("symbols") or [{}])[0]
        rules = {}
        for f in row.get("filters", []):
            if f.get("filterType") in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
                # Prefer MARKET_LOT_SIZE if present because this bridge sends MARKET orders.
                if f.get("filterType") == "MARKET_LOT_SIZE" or not rules:
                    rules = {
                        "minQty": f.get("minQty"),
                        "maxQty": f.get("maxQty"),
                        "stepSize": f.get("stepSize"),
                    }
        self.symbol_rules[symbol] = rules or {"minQty": "0.001", "stepSize": "0.001"}
        return self.symbol_rules[symbol]

    def _public_request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        url = self.config.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method.upper())
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _signed_request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        if not self.has_keys:
            raise RuntimeError("missing API key/secret")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = self.config.recv_window
        query = urllib.parse.urlencode(params)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        query = query + "&signature=" + signature

        url = self.config.base_url.rstrip("/") + path
        data = None
        headers = {"X-MBX-APIKEY": self.api_key}
        method = method.upper()
        if method in {"POST", "DELETE", "PUT"}:
            data = query.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            url += "?" + query

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc

    def _append_order_row(self, trade_id: int, symbol: str, action: str, side: str, qty: str, payload: dict) -> None:
        path = Path(self.config.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(["timestamp_utc", "trade_id", "symbol", "action", "side", "quantity", "order_id", "status", "raw"])
            writer.writerow([
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                trade_id,
                symbol,
                action,
                side,
                qty,
                payload.get("orderId"),
                payload.get("status"),
                json.dumps(payload, separators=(",", ":")),
            ])
