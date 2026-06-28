from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple
from .models import Candle


def _near(a: float, b: float, tol_pct: float) -> bool:
    mid = max((abs(a) + abs(b)) / 2, 1e-12)
    return abs(a - b) / mid <= tol_pct


def recent_equal_highs_lows(candles: Sequence[Candle], lookback: int = 40, tolerance_pct: float = 0.0015) -> Dict[str, Optional[float]]:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < 10:
        return {"equal_high": None, "equal_low": None}
    recent = closed[-lookback:]

    highs = sorted([c.high for c in recent], reverse=True)
    lows = sorted([c.low for c in recent])

    equal_high = None
    equal_low = None

    # Find clusters: at least 2 touches near a level. Keep conservative.
    for i, h in enumerate(highs[:10]):
        touches = [x for x in highs if _near(x, h, tolerance_pct)]
        if len(touches) >= 2:
            equal_high = sum(touches) / len(touches)
            break

    for i, l in enumerate(lows[:10]):
        touches = [x for x in lows if _near(x, l, tolerance_pct)]
        if len(touches) >= 2:
            equal_low = sum(touches) / len(touches)
            break

    return {"equal_high": equal_high, "equal_low": equal_low}


def detect_liquidity_sweep(candles: Sequence[Candle], equal_high: Optional[float], equal_low: Optional[float]) -> Dict[str, bool]:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < 3:
        return {"bullish_sweep": False, "bearish_sweep": False, "bullish_reclaim": False, "bearish_reclaim": False}
    cur = closed[-1]
    prev = closed[-2]

    bullish_sweep = bool(equal_low and cur.low < equal_low and cur.close > equal_low)
    bearish_sweep = bool(equal_high and cur.high > equal_high and cur.close < equal_high)

    # Reclaim after prior candle swept and current candle closes back through level.
    bullish_reclaim = bool(equal_low and prev.low < equal_low and cur.close > equal_low)
    bearish_reclaim = bool(equal_high and prev.high > equal_high and cur.close < equal_high)

    return {
        "bullish_sweep": bullish_sweep,
        "bearish_sweep": bearish_sweep,
        "bullish_reclaim": bullish_reclaim,
        "bearish_reclaim": bearish_reclaim,
    }


def detect_fvg(candles: Sequence[Candle], lookback_windows: int = 8) -> Dict[str, Optional[Tuple[float, float]]]:
    """Recent three-candle Fair Value Gap detector.

    Earlier versions only checked the latest three candles, so a valid imbalance
    disappeared from the score after one candle. This scans the recent windows
    and returns the most recent bullish/bearish FVG zone.
    """
    closed = [c for c in candles if c.is_closed]
    if len(closed) < 3:
        return {"bullish_fvg": None, "bearish_fvg": None}

    bullish = None
    bearish = None
    recent = closed[-(lookback_windows + 2):]
    for i in range(2, len(recent)):
        c1, c2, c3 = recent[i - 2], recent[i - 1], recent[i]
        if c1.high < c3.low and c2.direction == "bull":
            bullish = (c1.high, c3.low)
        if c1.low > c3.high and c2.direction == "bear":
            bearish = (c3.high, c1.low)
    return {"bullish_fvg": bullish, "bearish_fvg": bearish}


def market_structure(candles: Sequence[Candle], lookback: int = 20) -> str:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < lookback:
        return "unknown"
    recent = closed[-lookback:]
    mid = len(recent) // 2
    first = recent[:mid]
    second = recent[mid:]
    first_high = max(c.high for c in first)
    first_low = min(c.low for c in first)
    second_high = max(c.high for c in second)
    second_low = min(c.low for c in second)

    if second_high > first_high and second_low > first_low:
        return "bullish"
    if second_high < first_high and second_low < first_low:
        return "bearish"
    return "range"


def support_resistance_targets(candles: Sequence[Candle], direction: str, lookback: int = 60) -> List[float]:
    closed = [c for c in candles if c.is_closed]
    if len(closed) < 5:
        return []
    recent = closed[-lookback:]
    cur = closed[-1].close
    highs = sorted({round(c.high, 8) for c in recent})
    lows = sorted({round(c.low, 8) for c in recent})
    if direction == "long":
        targets = [h for h in highs if h > cur]
        return targets[:3]
    if direction == "short":
        targets = [l for l in reversed(lows) if l < cur]
        return targets[:3]
    return []
