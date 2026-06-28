from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Signal


@dataclass
class VirtualTradeConfig:
    """Paper-trading rules for INSIGHT virtual bot.

    The bot never places real Binance orders. It only simulates isolated-futures
    trades locally using live prices from the dashboard.
    """

    initial_wallet_usdt: float = 1000.0
    margin_usdt: float = 20.0
    leverage: float = 10.0
    taker_fee_rate: float = 0.0005  # conservative regular taker fee assumption
    min_net_profit_usdt: float = 3.0
    max_net_loss_usdt: float = 3.0
    max_open_trades: int = 3
    allow_multiple_same_symbol: bool = False
    trade_log_path: str = "signals/virtual_trades.csv"
    auto_extend_tp_to_min_profit: bool = True
    max_auto_tp_move_pct: float = 4.0

    @property
    def notional_usdt(self) -> float:
        return self.margin_usdt * self.leverage


@dataclass
class VirtualTrade:
    trade_id: int
    symbol: str
    direction: str  # long / short
    opened_at: datetime
    entry_price: float
    quantity: float
    margin_usdt: float
    leverage: float
    notional_usdt: float
    take_profit: float
    stop_loss: float
    technical_invalidation: float
    expected_net_profit: float
    max_net_loss: float
    entry_fee: float
    signal_score: int
    buy_score: int
    sell_score: int
    bias_label: str
    status: str = "OPEN"
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_fee: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    last_price: Optional[float] = None
    unrealized_gross: float = 0.0
    unrealized_net_est: float = 0.0

    def side_label(self) -> str:
        return "LONG" if self.direction == "long" else "SHORT"


@dataclass
class TradeDecision:
    opened: bool
    message: str
    trade: Optional[VirtualTrade] = None


class VirtualTrader:
    def __init__(self, config: Optional[VirtualTradeConfig] = None):
        self.config = config or VirtualTradeConfig()
        self.cash_balance: float = self.config.initial_wallet_usdt
        self.total_fees: float = 0.0
        self.closed_net_pnl: float = 0.0
        self.trades: List[VirtualTrade] = []
        self.next_trade_id: int = 1

    @property
    def open_trades(self) -> List[VirtualTrade]:
        return [t for t in self.trades if t.status == "OPEN"]

    @property
    def closed_trades(self) -> List[VirtualTrade]:
        return [t for t in self.trades if t.status != "OPEN"]

    @property
    def locked_margin(self) -> float:
        return sum(t.margin_usdt for t in self.open_trades)

    @property
    def equity(self) -> float:
        # Cash excludes locked margin while a trade is open. Add locked margin
        # and estimated net unrealized PnL to show trader's current paper equity.
        return self.cash_balance + self.locked_margin + sum(t.unrealized_net_est for t in self.open_trades)

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.net_pnl > 0)
        return wins / len(closed) * 100.0

    def try_open_from_signal(self, signal: Signal, current_price: Optional[float] = None) -> TradeDecision:
        cfg = self.config
        if len(self.open_trades) >= cfg.max_open_trades:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: max open trades reached ({cfg.max_open_trades}).")
        if not cfg.allow_multiple_same_symbol and any(t.symbol == signal.symbol for t in self.open_trades):
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: trade already open for this coin.")
        if signal.direction not in {"long", "short"}:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: signal direction is not tradable.")
        if not signal.targets:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: no TP target in signal.")

        entry = current_price or signal.price or ((signal.entry_zone[0] + signal.entry_zone[1]) / 2.0)
        if entry <= 0:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: invalid entry price.")

        notional = cfg.notional_usdt
        qty = notional / entry
        entry_fee = notional * cfg.taker_fee_rate

        # Pick the first target that clears the user's >1 USDT net-profit rule.
        # Older builds used only nearest TP1, so many otherwise valid signals
        # were skipped even when TP2/TP3 could meet the paper-bot rule.
        tp, expected_net = self._pick_take_profit_for_min_net(
            signal.direction, entry, signal.targets, qty, entry_fee, cfg.min_net_profit_usdt
        )
        tp_source = "signal TP"
        if tp is None:
            best_tp = self._pick_take_profit(signal.direction, entry, signal.targets)
            best_net = 0.0
            if best_tp is not None:
                exit_fee = (qty * best_tp) * cfg.taker_fee_rate
                best_net = self._gross_pnl(signal.direction, entry, best_tp, qty) - entry_fee - exit_fee

            # v3.7: in paper mode, allow a calculated minimum-profit TP when the
            # technical TP is too close. This is still virtual-only and is marked
            # in the feed as BOT-MIN-TP. It prevents the bot from appearing dead
            # when the user's +1 USDT requirement is stricter than the chart TP.
            if cfg.auto_extend_tp_to_min_profit:
                required_tp = self._required_tp_for_min_net(signal.direction, entry, qty, entry_fee, cfg.min_net_profit_usdt + 0.01)
                if required_tp and required_tp > 0:
                    move_pct = abs(required_tp - entry) / max(entry, 1e-12) * 100.0
                    if move_pct <= cfg.max_auto_tp_move_pct:
                        tp = required_tp
                        exit_fee = (qty * tp) * cfg.taker_fee_rate
                        expected_net = self._gross_pnl(signal.direction, entry, tp, qty) - entry_fee - exit_fee
                        tp_source = f"BOT-MIN-TP {move_pct:.2f}%"
                    else:
                        return TradeDecision(
                            False,
                            f"Virtual bot skipped {signal.symbol}: +{cfg.min_net_profit_usdt:.2f} USDT needs {move_pct:.2f}% move, above bot cap {cfg.max_auto_tp_move_pct:.1f}%. Best signal TP net {best_net:.2f} USDT.",
                        )

            if tp is None:
                return TradeDecision(
                    False,
                    f"Virtual bot skipped {signal.symbol}: no TP target clears net +{cfg.min_net_profit_usdt:.2f} USDT after fees. Best available net was {best_net:.2f} USDT.",
                )

        # SL is capped to approximately max_net_loss_usdt including entry + exit fee.
        exit_fee_sl_est = notional * cfg.taker_fee_rate
        loss_budget_for_price_move = cfg.max_net_loss_usdt - entry_fee - exit_fee_sl_est
        if loss_budget_for_price_move <= 0:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: fee estimate is too large for selected max loss.")
        sl_move = loss_budget_for_price_move / max(qty, 1e-12)
        cap_sl = entry - sl_move if signal.direction == "long" else entry + sl_move

        # If the technical invalidation is closer than the max-loss SL, use the
        # stricter technical level. If it is farther, cap loss at the 5 USDT rule.
        tech = signal.invalidation
        if signal.direction == "long":
            if tech < entry:
                stop = max(tech, cap_sl)
            else:
                stop = cap_sl
        else:
            if tech > entry:
                stop = min(tech, cap_sl)
            else:
                stop = cap_sl

        if (signal.direction == "long" and stop >= entry) or (signal.direction == "short" and stop <= entry):
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: calculated SL is invalid.")

        cash_needed = cfg.margin_usdt + entry_fee
        if self.cash_balance < cash_needed:
            return TradeDecision(False, f"Virtual bot skipped {signal.symbol}: paper wallet has insufficient free cash.")

        self.cash_balance -= cash_needed
        self.total_fees += entry_fee
        trade = VirtualTrade(
            trade_id=self.next_trade_id,
            symbol=signal.symbol,
            direction=signal.direction,
            opened_at=datetime.now(timezone.utc),
            entry_price=entry,
            quantity=qty,
            margin_usdt=cfg.margin_usdt,
            leverage=cfg.leverage,
            notional_usdt=notional,
            take_profit=tp,
            stop_loss=stop,
            technical_invalidation=tech,
            expected_net_profit=expected_net,
            max_net_loss=cfg.max_net_loss_usdt,
            entry_fee=entry_fee,
            signal_score=signal.score,
            buy_score=signal.buy_score,
            sell_score=signal.sell_score,
            bias_label=signal.bias_label,
            last_price=entry,
        )
        self.next_trade_id += 1
        self.trades.append(trade)
        self._append_trade_row(trade, "OPEN")
        return TradeDecision(
            True,
            f"Virtual bot OPENED {trade.symbol} {trade.side_label()} | entry {entry:.8g} | TP {tp:.8g} ({tp_source}) | SL {stop:.8g} | expected net +{expected_net:.2f} USDT | max loss {cfg.max_net_loss_usdt:.2f} USDT.",
            trade,
        )

    def update_prices(self, prices: Dict[str, float]) -> List[VirtualTrade]:
        closed_now: List[VirtualTrade] = []
        for trade in list(self.open_trades):
            price = prices.get(trade.symbol)
            if price is None or price <= 0:
                continue
            trade.last_price = price
            trade.unrealized_gross = self._gross_pnl(trade.direction, trade.entry_price, price, trade.quantity)
            exit_fee_est = (trade.quantity * price) * self.config.taker_fee_rate
            trade.unrealized_net_est = trade.unrealized_gross - trade.entry_fee - exit_fee_est

            if trade.direction == "long":
                if price >= trade.take_profit:
                    self._close_trade(trade, trade.take_profit, "TP HIT")
                    closed_now.append(trade)
                elif price <= trade.stop_loss:
                    self._close_trade(trade, trade.stop_loss, "SL HIT")
                    closed_now.append(trade)
            else:
                if price <= trade.take_profit:
                    self._close_trade(trade, trade.take_profit, "TP HIT")
                    closed_now.append(trade)
                elif price >= trade.stop_loss:
                    self._close_trade(trade, trade.stop_loss, "SL HIT")
                    closed_now.append(trade)
        return closed_now

    def manual_close_trade(self, trade_id: int, current_price: Optional[float] = None) -> TradeDecision:
        """Manually close an OPEN paper trade at the latest available price.

        This is still virtual/paper-only. No real Binance order is sent.
        """
        trade = next((t for t in self.trades if t.trade_id == trade_id), None)
        if trade is None:
            return TradeDecision(False, f"Manual close failed: trade #{trade_id} was not found.")
        if trade.status != "OPEN":
            return TradeDecision(False, f"Manual close skipped: trade #{trade_id} is already closed ({trade.exit_reason or trade.status}).")

        exit_price = current_price or trade.last_price or trade.entry_price
        if exit_price is None or exit_price <= 0:
            return TradeDecision(False, f"Manual close failed for trade #{trade_id}: no valid live price available.")

        self._close_trade(trade, exit_price, "MANUAL CLOSE")
        return TradeDecision(
            True,
            f"Manual paper CLOSE #{trade.trade_id} {trade.symbol} {trade.side_label()} | "
            f"exit {exit_price:.8g} | net PnL {trade.net_pnl:+.2f} USDT | equity {self.equity:.2f}",
            trade,
        )

    def _close_trade(self, trade: VirtualTrade, exit_price: float, reason: str) -> None:
        if trade.status != "OPEN":
            return
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(timezone.utc)
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.gross_pnl = self._gross_pnl(trade.direction, trade.entry_price, exit_price, trade.quantity)
        trade.exit_fee = (trade.quantity * exit_price) * self.config.taker_fee_rate
        trade.net_pnl = trade.gross_pnl - trade.entry_fee - trade.exit_fee
        trade.unrealized_gross = trade.gross_pnl
        trade.unrealized_net_est = trade.net_pnl
        self.cash_balance += trade.margin_usdt + trade.gross_pnl - trade.exit_fee
        self.total_fees += trade.exit_fee
        self.closed_net_pnl += trade.net_pnl
        self._append_trade_row(trade, reason)

    def _pick_take_profit(self, direction: str, entry: float, targets: List[float]) -> Optional[float]:
        if direction == "long":
            valid = [x for x in targets if x > entry]
            return min(valid) if valid else None
        valid = [x for x in targets if x < entry]
        return max(valid) if valid else None

    def _pick_take_profit_for_min_net(
        self, direction: str, entry: float, targets: List[float], qty: float, entry_fee: float, min_net: float
    ) -> Tuple[Optional[float], float]:
        if direction == "long":
            valid = sorted([x for x in targets if x > entry])
        else:
            valid = sorted([x for x in targets if x < entry], reverse=True)
        best_net = -10**9
        best_tp = None
        for tp in valid:
            exit_fee = (qty * tp) * self.config.taker_fee_rate
            net = self._gross_pnl(direction, entry, tp, qty) - entry_fee - exit_fee
            if net > best_net:
                best_net = net
                best_tp = tp
            if net > min_net:
                return tp, net
        return None, (best_net if best_tp is not None else 0.0)

    def _required_tp_for_min_net(self, direction: str, entry: float, qty: float, entry_fee: float, min_net: float) -> Optional[float]:
        fee = self.config.taker_fee_rate
        if qty <= 0:
            return None
        if direction == "long":
            denom = qty * max(1.0 - fee, 1e-12)
            return (min_net + qty * entry + entry_fee) / denom
        if direction == "short":
            denom = qty * (1.0 + fee)
            required = (qty * entry - entry_fee - min_net) / denom
            return required if required > 0 else None
        return None

    def _gross_pnl(self, direction: str, entry: float, exit_price: float, qty: float) -> float:
        if direction == "long":
            return (exit_price - entry) * qty
        return (entry - exit_price) * qty

    def summary_text(self) -> str:
        return (
            f"Paper Bot ON | Wallet {self.config.initial_wallet_usdt:.0f} USDT | "
            f"Cash {self.cash_balance:.2f} | Equity {self.equity:.2f} | "
            f"Open {len(self.open_trades)}/{self.config.max_open_trades} | Closed {len(self.closed_trades)} | "
            f"Closed PnL {self.closed_net_pnl:+.2f} | Fees {self.total_fees:.2f} | Win {self.win_rate:.0f}% | "
            f"Rule: {self.config.margin_usdt:.0f} USDT x{self.config.leverage:.0f}, TP net > {self.config.min_net_profit_usdt:.0f}, SL {self.config.max_net_loss_usdt:.0f}, auto TP cap {self.config.max_auto_tp_move_pct:.0f}%"
        )

    def recent_rows(self, limit: int = 8) -> List[Tuple[str, ...]]:
        rows = []
        for t in list(reversed(self.trades))[:limit]:
            pnl = t.unrealized_net_est if t.status == "OPEN" else t.net_pnl
            status = t.status if t.status == "OPEN" else (t.exit_reason or "CLOSED")
            action = "CLOSE" if t.status == "OPEN" else ""
            rows.append((
                str(t.trade_id),
                t.symbol.replace("USDT", ""),
                t.side_label(),
                f"{t.entry_price:.6g}",
                f"{t.take_profit:.6g}",
                f"{t.stop_loss:.6g}",
                f"{pnl:+.2f}",
                status,
                action,
            ))
        return rows

    def _append_trade_row(self, trade: VirtualTrade, event: str) -> None:
        path = Path(self.config.trade_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists() and path.stat().st_size > 0
        fieldnames = [
            "event_time_utc", "event", "trade_id", "symbol", "direction", "status",
            "entry_price", "exit_price", "tp", "sl", "technical_invalidation",
            "margin_usdt", "leverage", "notional_usdt", "qty", "signal_score",
            "buy_score", "sell_score", "expected_net_profit", "gross_pnl", "net_pnl",
            "entry_fee", "exit_fee", "exit_reason",
        ]
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            writer.writerow({
                "event_time_utc": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "direction": trade.direction,
                "status": trade.status,
                "entry_price": f"{trade.entry_price:.8g}",
                "exit_price": "" if trade.exit_price is None else f"{trade.exit_price:.8g}",
                "tp": f"{trade.take_profit:.8g}",
                "sl": f"{trade.stop_loss:.8g}",
                "technical_invalidation": f"{trade.technical_invalidation:.8g}",
                "margin_usdt": f"{trade.margin_usdt:.2f}",
                "leverage": f"{trade.leverage:.2f}",
                "notional_usdt": f"{trade.notional_usdt:.2f}",
                "qty": f"{trade.quantity:.12g}",
                "signal_score": trade.signal_score,
                "buy_score": trade.buy_score,
                "sell_score": trade.sell_score,
                "expected_net_profit": f"{trade.expected_net_profit:.4f}",
                "gross_pnl": f"{trade.gross_pnl:.4f}",
                "net_pnl": f"{trade.net_pnl:.4f}",
                "entry_fee": f"{trade.entry_fee:.4f}",
                "exit_fee": f"{trade.exit_fee:.4f}",
                "exit_reason": trade.exit_reason or "",
            })
