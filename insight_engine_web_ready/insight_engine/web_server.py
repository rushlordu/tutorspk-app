from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import binance_ws, bybit_ws
from .config import EngineConfig
from .engine import InsightEngine
from .flow import latest_book_imbalance, trade_flow_delta, volume_impulse
from .models import Candle, ScoreSnapshot, Signal, TradePrint
from .rest import get_core_plus_dynamic_symbols, normalize_symbol, preload_historical_klines
from .signal_store import append_signal


def score_bar(score: int, width: int = 10) -> str:
    score = max(0, min(100, int(score)))
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _clock() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_symbols(default: str) -> List[str]:
    raw = os.getenv("INSIGHT_SYMBOLS", default)
    return [normalize_symbol(x) for x in raw.split(",") if normalize_symbol(x)]


class InsightWebService:
    """Async background service used by the browser dashboard.

    This is intentionally web-safe: no Tkinter, no desktop windows, and no real Binance orders.
    It streams Binance public market data and exposes compact JSON snapshots for the frontend.
    """

    def __init__(self) -> None:
        self.config = EngineConfig(
            interval=os.getenv("INSIGHT_INTERVAL", "1m"),
            deep_limit=_env_int("INSIGHT_DEEP_LIMIT", 15),
            min_signal_score=_env_int("INSIGHT_MIN_SCORE", 70),
            market_provider=os.getenv("INSIGHT_PROVIDER", "binance").lower().strip(),
        )
        self.auto_symbols = _env_int("INSIGHT_AUTO_SYMBOLS", 15)
        self.manual_symbols = _env_symbols("BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT")
        self.engine: Optional[InsightEngine] = None
        self.task: Optional[asyncio.Task] = None
        self.stop_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.rows: Dict[str, dict] = {}
        self.signals: List[dict] = []
        self.status = "Stopped"
        self.running_symbols: List[str] = []
        self.reasons: Dict[str, str] = {}
        self.last_error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.last_snapshot_at: Optional[str] = None

    async def start(self) -> None:
        async with self.lock:
            if self.task and not self.task.done():
                return
            self.stop_event = asyncio.Event()
            self.task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.status = "Stopped"

    async def state(self) -> dict:
        async with self.lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "last_snapshot_at": self.last_snapshot_at,
                "last_error": self.last_error,
                "symbols": self.running_symbols,
                "reasons": self.reasons,
                "rows": list(self.rows.values()),
                "signals": self.signals[-50:][::-1],
                "settings": {
                    "interval": self.config.interval,
                    "deep_limit": self.config.deep_limit,
                    "min_signal_score": self.config.min_signal_score,
                    "auto_symbols": self.auto_symbols,
                    "provider": self.config.market_provider,
                },
            }

    async def _run_loop(self) -> None:
        try:
            self.status = "Starting"
            self.last_error = None
            self.started_at = datetime.now(timezone.utc).isoformat()

            if self.auto_symbols > 0:
                limit = min(15, max(1, int(self.auto_symbols)))
                symbols, reasons = await get_core_plus_dynamic_symbols(
                    self.config,
                    limit=limit,
                    manual_symbols=self.manual_symbols,
                )
            else:
                symbols = self.manual_symbols[:15]
                reasons = {s: "manual symbol" for s in symbols}

            engine = InsightEngine(symbols, self.config)
            history = await preload_historical_klines(
                self.config,
                engine.symbols,
                self.config.interval,
                self.config.historical_klines_limit,
            )
            for candles in history.values():
                for candle in candles:
                    engine.update_state(candle)

            provider = (self.config.market_provider or "binance").lower()
            wsmod = bybit_ws if provider == "bybit" else binance_ws
            streams = []
            streams += wsmod.kline_streams(engine.symbols, self.config.interval)
            streams += wsmod.agg_trade_streams(engine.symbols)
            streams += wsmod.mark_price_streams(engine.symbols)
            depth_symbols = engine.symbols[: max(3, min(self.config.deep_limit, 15))]
            streams += wsmod.partial_depth_streams(depth_symbols, self.config.depth_levels, self.config.depth_speed_ms)
            ws_base = self.config.bybit_ws_base if provider == "bybit" else self.config.market_ws_base

            async with self.lock:
                self.engine = engine
                self.running_symbols = engine.symbols
                self.reasons = reasons
                self.rows = self._snapshot_rows(engine, engine.score_all())
                self.status = f"Live: {len(engine.symbols)} symbols, {len(streams)} streams | provider {provider}"
                self.last_snapshot_at = datetime.now(timezone.utc).isoformat()

            last_snapshot = 0.0
            last_alert_ts: Dict[str, float] = {}
            seen_signal_keys: set[str] = set()

            async for event in wsmod.stream_events(streams, ws_base, self.config.websocket_chunk_size):
                if self.stop_event.is_set():
                    break

                engine.update_state(event)
                now = time.time()

                if now - last_snapshot >= 1.0:
                    last_snapshot = now
                    snaps = engine.score_all()
                    rows = self._snapshot_rows(engine, snaps)
                    new_signals = self._collect_signals(engine, snaps, last_alert_ts, seen_signal_keys, now)

                    async with self.lock:
                        self.rows = rows
                        self.last_snapshot_at = datetime.now(timezone.utc).isoformat()
                        if new_signals:
                            self.signals.extend(new_signals)
                            self.signals = self.signals[-200:]

        except asyncio.CancelledError:
            self.status = "Stopped"
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.status = f"Error: {exc}"

    def _snapshot_rows(self, engine: InsightEngine, snapshots: Dict[str, ScoreSnapshot]) -> Dict[str, dict]:
        rows: Dict[str, dict] = {}
        ordered_symbols = list(engine.symbols)
        for sym in ordered_symbols:
            st = engine.states.get(sym)
            if not st:
                continue
            closed = [c for c in st.candles if c.is_closed]
            snap = snapshots.get(sym)
            last_price = st.last_price or (closed[-1].close if closed else None)
            move = None
            if len(closed) >= 2:
                prev = closed[-2].close
                cur = closed[-1].close
                move = (cur - prev) / max(prev, 1e-12) * 100
            buy = snap.buy_score if snap else 0
            sell = snap.sell_score if snap else 0
            bias = snap.bias_label if snap else "LOADING"
            rows[sym] = {
                "symbol": sym,
                "coin": sym.replace("USDT", ""),
                "price": last_price,
                "move_pct": move,
                "buy": buy,
                "buybar": score_bar(buy),
                "sell": sell,
                "sellbar": score_bar(sell),
                "bias": bias,
                "urgency": snap.features.candle_urgency_label if snap else "--",
                "flow": trade_flow_delta(st.trades, engine.config.trade_flow_window),
                "book": latest_book_imbalance(st.books),
                "vol": volume_impulse(st.candles) if len(st.candles) > 5 else None,
                "candles": len(closed),
                "last": _clock(),
            }
        return rows

    def _collect_signals(
        self,
        engine: InsightEngine,
        snapshots: Dict[str, ScoreSnapshot],
        last_alert_ts: Dict[str, float],
        seen_signal_keys: set[str],
        now: float,
    ) -> List[dict]:
        out: List[dict] = []
        for sym, snap in snapshots.items():
            if not snap.alert_direction:
                continue
            score = snap.buy_score if snap.alert_direction == "long" else snap.sell_score
            if score < self.config.min_signal_score:
                continue
            cooldown_key = f"{sym}:{snap.alert_direction}"
            if now - last_alert_ts.get(cooldown_key, 0) < self.config.signal_cooldown_seconds:
                continue
            sig = engine.signal_from_snapshot(snap)
            if not sig:
                continue
            key = f"{sig.symbol}:{sig.timestamp.isoformat()}:{sig.direction}"
            if key in seen_signal_keys:
                continue
            seen_signal_keys.add(key)
            last_alert_ts[cooldown_key] = now
            try:
                append_signal(sig)
            except Exception:
                pass
            out.append(self._signal_to_dict(sig))
        return out

    @staticmethod
    def _signal_to_dict(sig: Signal) -> dict:
        return {
            "time": sig.timestamp.isoformat(),
            "symbol": sig.symbol,
            "direction": sig.direction.upper(),
            "score": sig.score,
            "confidence": sig.confidence_label,
            "price": sig.price,
            "entry_zone": list(sig.entry_zone),
            "invalidation": sig.invalidation,
            "targets": sig.targets,
            "bias": sig.bias_label,
            "notes": sig.features.notes[:8],
            "line": sig.one_line(),
        }
