from __future__ import annotations

from typing import List, Tuple
from .models import SetupFeatures


FactorRows = List[Tuple[str, int, int, str]]


def score_both(features: SetupFeatures) -> Tuple[int, int, str, str, FactorRows]:
    """Return buy score, sell score, bias label, alert direction, and factor rows.

    The GUI uses the two separate scores as live pressure bars. Alerts are only
    produced when one side is strong and the opposite side is not conflicting.
    """
    buy = 0
    sell = 0
    rows: FactorRows = []

    def add(name: str, b: int, s: int, reason: str) -> None:
        nonlocal buy, sell, rows
        buy += b
        sell += s
        rows.append((name, b, s, reason))

    # 1. Market structure.
    if features.market_structure == "bullish":
        add("Market structure", 10, 0, "recent highs/lows are shifting upward")
    elif features.market_structure == "bearish":
        add("Market structure", 0, 10, "recent highs/lows are shifting downward")
    elif features.market_structure == "range":
        add("Market structure", 3, 3, "range market; wait for sweep or breakout")

    # 2. Live momentum / candle pressure.
    # This is lighter than SMC/candlestick factors but makes the live bars useful
    # even before a perfect liquidity sweep or FVG forms.
    mom = features.recent_momentum_pct
    if mom >= 0.35:
        add("Short-term momentum", 10, 0, f"3-candle price pressure is bullish: {mom:+.2f}%")
    elif mom >= 0.15:
        add("Short-term momentum", 6, 0, f"mild bullish pressure: {mom:+.2f}%")
    elif mom <= -0.35:
        add("Short-term momentum", 0, 10, f"3-candle price pressure is bearish: {mom:+.2f}%")
    elif mom <= -0.15:
        add("Short-term momentum", 0, 6, f"mild bearish pressure: {mom:+.2f}%")

    if features.last_candle_direction == "bull" and features.last_close_position >= 0.65:
        add("Latest candle close", 5, 0, f"latest candle closed high in its range ({features.last_close_position:.2f})")
    elif features.last_candle_direction == "bear" and features.last_close_position <= 0.35:
        add("Latest candle close", 0, 5, f"latest candle closed low in its range ({features.last_close_position:.2f})")

    # 3. Liquidity / smart money.
    if features.bullish_sweep:
        add("Liquidity sweep", 22, 0, "equal lows swept and candle closed back above the level")
    if features.bearish_sweep:
        add("Liquidity sweep", 0, 22, "equal highs swept and candle closed back below the level")
    if features.bullish_reclaim:
        add("Level reclaim", 12, 0, "price reclaimed a swept low")
    if features.bearish_reclaim:
        add("Level reclaim", 0, 12, "price rejected/reclaimed below a swept high")

    # 3. FVG / imbalance.
    if features.bullish_fvg:
        add("Fair Value Gap", 12, 0, "bullish imbalance detected on latest 3-candle structure")
    if features.bearish_fvg:
        add("Fair Value Gap", 0, 12, "bearish imbalance detected on latest 3-candle structure")

    # 4. Candle trigger from Candlestick Bible style logic.
    trig = features.candle_trigger or ""
    if "bullish" in trig:
        add("Candlestick trigger", 14, 0, trig)
    elif "bearish" in trig:
        add("Candlestick trigger", 0, 14, trig)
    elif trig == "inside bar":
        add("Candlestick trigger", 4, 4, "inside bar; compression, wait for break")

    # 5. Candle urgency map: take-off / immediate fall from raw candle behaviour.
    # This catches fast ignition, clean breakout/breakdown, compression release,
    # and rejection-at-extreme candles. It is still combined with flow/book/BTC.
    if features.takeoff_score >= 60 and features.takeoff_score >= features.fall_score + 8:
        add("Candle urgency", 22, 0, features.takeoff_signal or f"take-off pressure {features.takeoff_score}/100")
    elif features.takeoff_score >= 45 and features.takeoff_score >= features.fall_score + 8:
        add("Candle urgency", 18, 0, features.takeoff_signal or f"take-off pressure {features.takeoff_score}/100")
    elif features.takeoff_score >= 25 and features.takeoff_score >= features.fall_score + 8:
        add("Candle urgency", 11, 0, features.takeoff_signal or f"take-off watch {features.takeoff_score}/100")

    if features.fall_score >= 60 and features.fall_score >= features.takeoff_score + 8:
        add("Candle urgency", 0, 22, features.fall_signal or f"immediate fall pressure {features.fall_score}/100")
    elif features.fall_score >= 45 and features.fall_score >= features.takeoff_score + 8:
        add("Candle urgency", 0, 18, features.fall_signal or f"immediate fall pressure {features.fall_score}/100")
    elif features.fall_score >= 25 and features.fall_score >= features.takeoff_score + 8:
        add("Candle urgency", 0, 11, features.fall_signal or f"fall watch {features.fall_score}/100")

    if features.takeoff_score >= 25 and features.fall_score >= 25 and abs(features.takeoff_score - features.fall_score) < 8:
        add("Candle urgency", 5, 5, f"conflicting candle urgency: take-off {features.takeoff_score}, fall {features.fall_score}")

    # 6. Volume impulse.
    if features.volume_impulse >= 2.2:
        add("Volume impulse", 6, 6, f"very high activity: {features.volume_impulse:.2f}x average")
        if features.recent_momentum_pct > 0.15:
            add("Directional volume", 6, 0, "high volume is supporting upside pressure")
        elif features.recent_momentum_pct < -0.15:
            add("Directional volume", 0, 6, "high volume is supporting downside pressure")
    elif features.volume_impulse >= 1.5:
        add("Volume impulse", 4, 4, f"above-average activity: {features.volume_impulse:.2f}x")
        if features.recent_momentum_pct > 0.15:
            add("Directional volume", 4, 0, "volume is supporting upside pressure")
        elif features.recent_momentum_pct < -0.15:
            add("Directional volume", 0, 4, "volume is supporting downside pressure")

    # 7. Aggressive trade flow.
    if features.buy_sell_delta > 0.35:
        add("Trade flow", 14, 0, f"aggressive market buying: delta {features.buy_sell_delta:+.2f}")
    elif features.buy_sell_delta > 0.15:
        add("Trade flow", 9, 0, f"buyers stronger: delta {features.buy_sell_delta:+.2f}")
    elif features.buy_sell_delta < -0.35:
        add("Trade flow", 0, 14, f"aggressive market selling: delta {features.buy_sell_delta:+.2f}")
    elif features.buy_sell_delta < -0.15:
        add("Trade flow", 0, 9, f"sellers stronger: delta {features.buy_sell_delta:+.2f}")

    # 8. Order book imbalance.
    if features.book_imbalance > 0.20:
        add("Order book", 11, 0, f"bid side clearly stronger: imbalance {features.book_imbalance:+.2f}")
    elif features.book_imbalance > 0.08:
        add("Order book", 7, 0, f"bid side slightly stronger: imbalance {features.book_imbalance:+.2f}")
    elif features.book_imbalance < -0.20:
        add("Order book", 0, 11, f"ask side clearly stronger: imbalance {features.book_imbalance:+.2f}")
    elif features.book_imbalance < -0.08:
        add("Order book", 0, 7, f"ask side slightly stronger: imbalance {features.book_imbalance:+.2f}")

    # 9. Session timing. It supports volatility, not direction.
    hot_sessions = {"London open window", "New York pre/open window", "9:30 NY equity open", "Daily open", "Weekly open"}
    if any(s in hot_sessions for s in features.session_tags):
        add("Session timing", 3, 3, "active volatility window")

    # 10. BTC alignment. This is important for altcoins.
    if features.btc_alignment == "long":
        add("BTC alignment", 8, 0, "BTC short-term context supports longs")
    elif features.btc_alignment == "short":
        add("BTC alignment", 0, 8, "BTC short-term context supports shorts")
    elif features.btc_alignment == "neutral":
        add("BTC alignment", 2, 2, "BTC context is neutral/ranging")

    buy = max(0, min(100, int(buy)))
    sell = max(0, min(100, int(sell)))

    alert_direction = ""
    if buy >= 85 and sell < 60:
        bias = "STRONG BUY"
        alert_direction = "long"
    elif sell >= 85 and buy < 60:
        bias = "STRONG SELL"
        alert_direction = "short"
    elif buy >= 75 and sell < 60:
        bias = "BUY SIGNAL"
        alert_direction = "long"
    elif sell >= 75 and buy < 60:
        bias = "SELL SIGNAL"
        alert_direction = "short"
    elif buy >= 60 and sell >= 60:
        bias = "CONFLICT / AVOID"
    elif buy >= 65 and buy > sell + 8:
        bias = "LONG WATCH"
    elif sell >= 65 and sell > buy + 8:
        bias = "SHORT WATCH"
    elif buy >= 50 and buy > sell:
        bias = "WEAK LONG"
    elif sell >= 50 and sell > buy:
        bias = "WEAK SHORT"
    else:
        bias = "NO TRADE"

    features.notes = [f"{name}: {reason} (+Buy {b}, +Sell {s})" for name, b, s, reason in rows if b or s]
    return buy, sell, bias, alert_direction, rows


def score_setup(features: SetupFeatures) -> Tuple[int, str, str]:
    """Backward-compatible score function for console/signal use."""
    buy, sell, bias, alert_direction, _rows = score_both(features)
    if alert_direction == "long" or (buy > sell + 8 and buy >= 50):
        direction = "long"
        score = buy
    elif alert_direction == "short" or (sell > buy + 8 and sell >= 50):
        direction = "short"
        score = sell
    else:
        direction = "neutral"
        score = max(buy, sell)

    if "STRONG" in bias:
        label = "strong-signal"
    elif "SIGNAL" in bias:
        label = "signal"
    elif "WATCH" in bias:
        label = "watch"
    elif "CONFLICT" in bias:
        label = "conflict"
    else:
        label = "ignore"
    return score, label, direction
