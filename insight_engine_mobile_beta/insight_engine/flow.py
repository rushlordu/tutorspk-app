from __future__ import annotations

from typing import Sequence
from .models import TradePrint, BookSnapshot, Candle


def trade_flow_delta(trades: Sequence[TradePrint], window: int = 120) -> float:
    recent = list(trades)[-window:]
    buy = sum(t.notional for t in recent if t.taker_side == "buy")
    sell = sum(t.notional for t in recent if t.taker_side == "sell")
    total = buy + sell
    if total <= 0:
        return 0.0
    return (buy - sell) / total


def latest_book_imbalance(books: Sequence[BookSnapshot]) -> float:
    if not books:
        return 0.0
    return books[-1].imbalance


def volume_impulse(candles: Sequence[Candle], lookback: int = 30) -> float:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < lookback + 1:
        return 0.0
    cur = closed[-1].volume
    avg = sum(c.volume for c in closed[-lookback-1:-1]) / lookback
    if avg <= 0:
        return 0.0
    return cur / avg
