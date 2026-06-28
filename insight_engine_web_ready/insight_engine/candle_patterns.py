from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple
from .models import Candle


def _avg(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _close_position(c: Candle) -> float:
    # 0 = closed at candle low, 1 = closed at candle high.
    return (c.close - c.low) / max(c.high - c.low, 1e-12)


def _body_ratio(c: Candle) -> float:
    return c.body / max(c.range, 1e-12)


def _volume_ratio(cur: Candle, prior: Sequence[Candle]) -> float:
    avg_vol = _avg([c.volume for c in prior])
    return cur.volume / avg_vol if avg_vol > 0 else 0.0


def _range_ratio(cur: Candle, prior: Sequence[Candle]) -> float:
    avg_range = _avg([c.range for c in prior])
    return cur.range / avg_range if avg_range > 0 else 0.0


def detect_candle_trigger(candles: Sequence[Candle]) -> Optional[str]:
    """Candlestick Trading Bible style triggers.

    Candles are triggers, not standalone decisions. This function only labels the
    latest closed candle pattern.
    """
    closed = [c for c in candles if c.is_closed]
    if len(closed) < 3:
        return None

    prev = closed[-2]
    cur = closed[-1]

    # Pin bars: long wick with small body, close in rejection direction.
    body_ratio = cur.body / cur.range
    lower_wick_ratio = cur.lower_wick / cur.range
    upper_wick_ratio = cur.upper_wick / cur.range

    if body_ratio <= 0.35 and lower_wick_ratio >= 0.55 and cur.close > cur.open:
        return "bullish pin bar"
    if body_ratio <= 0.35 and upper_wick_ratio >= 0.55 and cur.close < cur.open:
        return "bearish pin bar"

    # Engulfing.
    prev_body_low = min(prev.open, prev.close)
    prev_body_high = max(prev.open, prev.close)
    cur_body_low = min(cur.open, cur.close)
    cur_body_high = max(cur.open, cur.close)

    if prev.direction == "bear" and cur.direction == "bull" and cur_body_low <= prev_body_low and cur_body_high >= prev_body_high:
        return "bullish engulfing"
    if prev.direction == "bull" and cur.direction == "bear" and cur_body_low <= prev_body_low and cur_body_high >= prev_body_high:
        return "bearish engulfing"

    # Inside bar.
    if cur.high < prev.high and cur.low > prev.low:
        return "inside bar"

    # Strong close candle.
    if cur.direction == "bull" and cur.close >= cur.low + 0.75 * cur.range:
        return "strong bullish close"
    if cur.direction == "bear" and cur.close <= cur.low + 0.25 * cur.range:
        return "strong bearish close"

    return None


def detect_takeoff_fall_signs(candles: Sequence[Candle], lookback: int = 24) -> Dict[str, object]:
    """Detect urgent candle-chart signs: take-off vs immediate fall.

    This is intentionally separate from the classic trigger detector above.
    It looks for *behavioural* candle conditions that often precede fast moves:

    - ignition candle with large body and close near high/low
    - clean breakout/breakdown of recent candle structure
    - compression box breakout/breakdown
    - two/three-candle acceleration
    - rejection wick at recent high/low after a run

    Output scores are 0-100 pressure values. They are not standalone trade
    commands; the main engine still combines them with flow, book, volume,
    BTC alignment and risk rules.
    """
    closed = [c for c in candles if c.is_closed]
    out: Dict[str, object] = {
        "takeoff_signal": None,
        "fall_signal": None,
        "takeoff_score": 0,
        "fall_score": 0,
        "takeoff_reasons": [],
        "fall_reasons": [],
    }
    if len(closed) < 8:
        return out

    cur = closed[-1]
    prev = closed[-2]
    prior = closed[-(lookback + 1):-1] if len(closed) > lookback else closed[:-1]
    if len(prior) < 5:
        return out

    recent_high = max(c.high for c in prior)
    recent_low = min(c.low for c in prior)
    range_ratio = _range_ratio(cur, prior)
    volume_ratio = _volume_ratio(cur, prior)
    body_ratio = _body_ratio(cur)
    close_pos = _close_position(cur)
    prev_close_pos = _close_position(prev)

    takeoff_score = 0
    fall_score = 0
    takeoff_reasons: List[str] = []
    fall_reasons: List[str] = []

    def add_takeoff(points: int, reason: str) -> None:
        nonlocal takeoff_score
        takeoff_score += points
        takeoff_reasons.append(reason)

    def add_fall(points: int, reason: str) -> None:
        nonlocal fall_score
        fall_score += points
        fall_reasons.append(reason)

    # 1) Ignition / marubozu-style candle.
    bullish_ignition = cur.direction == "bull" and body_ratio >= 0.58 and close_pos >= 0.72 and range_ratio >= 1.15
    bearish_ignition = cur.direction == "bear" and body_ratio >= 0.58 and close_pos <= 0.28 and range_ratio >= 1.15
    if bullish_ignition:
        add_takeoff(24, f"bullish ignition candle; body {body_ratio:.2f}, range {range_ratio:.2f}x avg, close pos {close_pos:.2f}")
    if bearish_ignition:
        add_fall(24, f"bearish ignition candle; body {body_ratio:.2f}, range {range_ratio:.2f}x avg, close pos {close_pos:.2f}")

    # 2) Structure break on close. Wick-only breaks are ignored; close must confirm.
    if cur.close > recent_high and cur.direction == "bull":
        add_takeoff(24, "closed above recent candle highs; breakout confirmed by close")
    if cur.close < recent_low and cur.direction == "bear":
        add_fall(24, "closed below recent candle lows; breakdown confirmed by close")

    # 3) Compression breakout/breakdown: quiet candles then one decisive candle.
    box = closed[-7:-1]
    if len(box) >= 5:
        box_high = max(c.high for c in box)
        box_low = min(c.low for c in box)
        box_avg_range = _avg([c.range for c in box])
        prior_avg_range = _avg([c.range for c in prior])
        compression = prior_avg_range > 0 and box_avg_range <= prior_avg_range * 0.78
        if compression and cur.close > box_high and bullish_ignition:
            add_takeoff(18, "compression box broke upward after narrow candles")
        if compression and cur.close < box_low and bearish_ignition:
            add_fall(18, "compression box broke downward after narrow candles")

    # 4) Two-candle acceleration. Useful on 1m/3m for fast take-off or dump.
    two_up = (
        prev.direction == "bull"
        and cur.direction == "bull"
        and cur.close > prev.high
        and close_pos >= 0.68
        and prev_close_pos >= 0.58
        and _body_ratio(prev) >= 0.42
    )
    two_down = (
        prev.direction == "bear"
        and cur.direction == "bear"
        and cur.close < prev.low
        and close_pos <= 0.32
        and prev_close_pos <= 0.42
        and _body_ratio(prev) >= 0.42
    )
    if two_up:
        add_takeoff(14, "two-candle upside acceleration; latest close broke previous high")
    if two_down:
        add_fall(14, "two-candle downside acceleration; latest close broke previous low")

    # 5) Three-candle continuation pressure.
    last3 = closed[-3:]
    if len(last3) == 3:
        three_up = all(c.direction == "bull" for c in last3) and last3[0].close < last3[1].close < last3[2].close
        three_down = all(c.direction == "bear" for c in last3) and last3[0].close > last3[1].close > last3[2].close
        if three_up and _avg([_close_position(c) for c in last3]) >= 0.66:
            add_takeoff(14, "three bullish continuation candles with firm closes")
        if three_down and _avg([_close_position(c) for c in last3]) <= 0.34:
            add_fall(14, "three bearish continuation candles with weak closes")

    # 6) Exhaustion/rejection around recent extremes. This detects immediate reversal risk.
    prior5 = closed[-6:-1]
    run_base = prior5[0].close if prior5 else prev.close
    prior_run_pct = (prev.close - run_base) / max(run_base, 1e-12) * 100.0
    at_high = cur.high >= recent_high * 0.999
    at_low = cur.low <= recent_low * 1.001
    upper_rejection = cur.upper_wick / cur.range >= 0.50 and close_pos <= 0.38
    lower_rejection = cur.lower_wick / cur.range >= 0.50 and close_pos >= 0.62
    if at_high and upper_rejection and prior_run_pct >= 0.15:
        add_fall(20, f"upper-wick rejection at recent high after {prior_run_pct:+.2f}% short run")
    if at_low and lower_rejection and prior_run_pct <= -0.15:
        add_takeoff(20, f"lower-wick rejection at recent low after {prior_run_pct:+.2f}% short run")

    # 7) Volume confirms urgency. If there is no volume, range expansion can still score.
    if takeoff_score > 0:
        if volume_ratio >= 1.8:
            add_takeoff(10, f"volume expansion confirms urgency: {volume_ratio:.2f}x avg")
        elif volume_ratio >= 1.25:
            add_takeoff(6, f"volume above average: {volume_ratio:.2f}x")
        elif range_ratio >= 1.8:
            add_takeoff(5, "range expansion is strong even without volume confirmation")
    if fall_score > 0:
        if volume_ratio >= 1.8:
            add_fall(10, f"volume expansion confirms urgency: {volume_ratio:.2f}x avg")
        elif volume_ratio >= 1.25:
            add_fall(6, f"volume above average: {volume_ratio:.2f}x")
        elif range_ratio >= 1.8:
            add_fall(5, "range expansion is strong even without volume confirmation")

    # Penalize ambiguous candle shapes: big opposing wick on an otherwise directional candle.
    if takeoff_score > 0 and cur.upper_wick / cur.range >= 0.42:
        takeoff_score -= 8
        takeoff_reasons.append("warning: upper wick shows selling into the move")
    if fall_score > 0 and cur.lower_wick / cur.range >= 0.42:
        fall_score -= 8
        fall_reasons.append("warning: lower wick shows buying into the drop")

    takeoff_score = max(0, min(100, int(takeoff_score)))
    fall_score = max(0, min(100, int(fall_score)))

    # If both fire, keep both but downgrade the weaker side so the GUI shows conflict.
    if takeoff_score and fall_score:
        if takeoff_score >= fall_score + 12:
            fall_score = max(0, fall_score - 12)
        elif fall_score >= takeoff_score + 12:
            takeoff_score = max(0, takeoff_score - 12)

    if takeoff_score >= 25:
        label = "TAKE-OFF NOW" if takeoff_score >= 60 else "TAKE-OFF WATCH"
        out["takeoff_signal"] = f"{label} {takeoff_score}/100 — " + "; ".join(takeoff_reasons[:3])
    if fall_score >= 25:
        label = "FALL NOW" if fall_score >= 60 else "FALL WATCH"
        out["fall_signal"] = f"{label} {fall_score}/100 — " + "; ".join(fall_reasons[:3])

    out["takeoff_score"] = takeoff_score
    out["fall_score"] = fall_score
    out["takeoff_reasons"] = takeoff_reasons
    out["fall_reasons"] = fall_reasons
    return out
