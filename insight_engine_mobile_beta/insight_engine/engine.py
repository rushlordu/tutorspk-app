from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .binance_ws import agg_trade_streams, kline_streams, mark_price_streams, stream_events
from .candle_patterns import detect_candle_trigger, detect_takeoff_fall_signs
from .config import EngineConfig
from .flow import latest_book_imbalance, trade_flow_delta, volume_impulse
from .models import BookSnapshot, Candle, ScoreSnapshot, Signal, SetupFeatures, SymbolState, TradePrint
from .scoring import score_both, score_setup
from .sessions import get_session_tags
from .smart_money import (
    detect_fvg,
    detect_liquidity_sweep,
    market_structure,
    recent_equal_highs_lows,
    support_resistance_targets,
)


class InsightEngine:
    def __init__(self, symbols: List[str], config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.symbols = []
        for s in symbols:
            s = s.upper().strip()
            if s and s not in self.symbols:
                self.symbols.append(s)
        if "BTCUSDT" not in self.symbols:
            self.symbols.insert(0, "BTCUSDT")
        self.states: Dict[str, SymbolState] = {s: SymbolState(symbol=s) for s in self.symbols}
        self.latest_signals: Dict[str, Signal] = {}

    def update_state(self, event: object) -> None:
        if isinstance(event, Candle):
            self.states.setdefault(event.symbol, SymbolState(event.symbol)).add_candle(event)
        elif isinstance(event, TradePrint):
            self.states.setdefault(event.symbol, SymbolState(event.symbol)).add_trade(event)
        elif isinstance(event, BookSnapshot):
            self.states.setdefault(event.symbol, SymbolState(event.symbol)).add_book(event)
        elif isinstance(event, dict) and event.get("type") == "mark":
            sym = event.get("symbol")
            if sym:
                st = self.states.setdefault(sym, SymbolState(sym))
                st.funding_rate = event.get("funding_rate")
                st.last_price = event.get("mark_price") or st.last_price

    def shortlist_symbols(self) -> List[str]:
        """Light ranking: movement + volume impulse + trade flow.

        In v3 the GUI shows all 15 top symbols, but depth/heavy work can still
        prioritize the most active coins if needed.
        """
        scored: List[Tuple[float, str]] = []
        for sym, st in self.states.items():
            closed = [c for c in st.candles if c.is_closed]
            if len(closed) < 35:
                continue
            last = closed[-1]
            prev = closed[-10]
            move = abs(last.close - prev.close) / max(prev.close, 1e-12)
            vol = volume_impulse(st.candles, 30)
            flow = abs(trade_flow_delta(st.trades, self.config.trade_flow_window))
            rank = move * 100 + vol + flow
            scored.append((rank, sym))
        scored.sort(reverse=True)
        picks = [sym for _, sym in scored[: self.config.deep_limit]]
        if "BTCUSDT" not in picks and "BTCUSDT" in self.states:
            picks.append("BTCUSDT")
        return picks

    def btc_alignment(self) -> Optional[str]:
        st = self.states.get("BTCUSDT")
        if not st or len(st.candles) < 35:
            return None
        ms = market_structure(st.candles, lookback=20)
        delta = trade_flow_delta(st.trades, self.config.trade_flow_window)
        if ms == "bullish" and delta > -0.05:
            return "long"
        if ms == "bearish" and delta < 0.05:
            return "short"
        return "neutral"

    def build_features(self, sym: str) -> Optional[SetupFeatures]:
        st = self.states.get(sym)
        if not st:
            return None
        closed = [c for c in st.candles if c.is_closed]
        if len(closed) < 40:
            return None

        levels = recent_equal_highs_lows(closed, tolerance_pct=self.config.equal_level_tolerance_pct)
        sweeps = detect_liquidity_sweep(closed, levels["equal_high"], levels["equal_low"])
        fvgs = detect_fvg(closed)
        trig = detect_candle_trigger(closed)
        urgency = detect_takeoff_fall_signs(closed)
        v_impulse = volume_impulse(closed)
        delta = trade_flow_delta(st.trades, self.config.trade_flow_window)
        imb = latest_book_imbalance(st.books)
        ts = closed[-1].close_time
        last = closed[-1]
        # Short-term pressure used for live score bars. This is deliberately
        # smaller than candle/SMC factors, but it prevents the board from
        # staying dead when the market is moving without a perfect sweep/FVG.
        mom_base = closed[-4].close if len(closed) >= 4 else closed[-2].close
        recent_momentum_pct = (last.close - mom_base) / max(mom_base, 1e-12) * 100.0
        last_close_position = (last.close - last.low) / max(last.high - last.low, 1e-12)

        return SetupFeatures(
            symbol=sym,
            timestamp=ts,
            direction="neutral",
            session_tags=get_session_tags(ts),
            market_structure=market_structure(closed, lookback=20),
            equal_highs=levels["equal_high"] is not None,
            equal_lows=levels["equal_low"] is not None,
            equal_high_level=levels["equal_high"],
            equal_low_level=levels["equal_low"],
            bullish_sweep=sweeps["bullish_sweep"],
            bearish_sweep=sweeps["bearish_sweep"],
            bullish_reclaim=sweeps["bullish_reclaim"],
            bearish_reclaim=sweeps["bearish_reclaim"],
            bullish_fvg=fvgs["bullish_fvg"] is not None,
            bearish_fvg=fvgs["bearish_fvg"] is not None,
            bullish_fvg_zone=fvgs["bullish_fvg"],
            bearish_fvg_zone=fvgs["bearish_fvg"],
            candle_trigger=trig,
            volume_impulse=v_impulse,
            buy_sell_delta=delta,
            book_imbalance=imb,
            btc_alignment=self.btc_alignment(),
            recent_momentum_pct=recent_momentum_pct,
            last_candle_direction=last.direction,
            last_close_position=last_close_position,
            takeoff_signal=urgency.get("takeoff_signal"),
            fall_signal=urgency.get("fall_signal"),
            takeoff_score=int(urgency.get("takeoff_score") or 0),
            fall_score=int(urgency.get("fall_score") or 0),
        )

    def score_snapshot(self, sym: str) -> Optional[ScoreSnapshot]:
        st = self.states.get(sym)
        if not st:
            return None
        features = self.build_features(sym)
        if not features:
            last_price = st.last_price
            if last_price is None:
                closed = [c for c in st.candles if c.is_closed]
                last_price = closed[-1].close if closed else None
            return None

        buy, sell, bias, alert_direction, factor_rows = score_both(features)

        # Let the GUI's Alert score control actual alerts. The original fixed
        # 75-point boundary made the spinner ineffective. We still require the
        # opposite side to stay below conflict level.
        threshold = int(self.config.min_signal_score)
        if not alert_direction:
            if buy >= threshold and sell < 60 and buy >= sell + 10:
                alert_direction = "long"
                bias = "BUY SIGNAL" if buy < 85 else "STRONG BUY"
            elif sell >= threshold and buy < 60 and sell >= buy + 10:
                alert_direction = "short"
                bias = "SELL SIGNAL" if sell < 85 else "STRONG SELL"

        features.direction = "long" if buy > sell + 8 else "short" if sell > buy + 8 else "neutral"
        closed = [c for c in st.candles if c.is_closed]
        direction_for_plan = alert_direction or features.direction
        entry_zone = None
        invalidation = None
        targets: List[float] = []
        if direction_for_plan in {"long", "short"} and closed:
            entry_zone, invalidation, targets = self._trade_plan(sym, direction_for_plan, closed)
        return ScoreSnapshot(
            symbol=sym,
            timestamp=features.timestamp,
            price=st.last_price or (closed[-1].close if closed else None),
            buy_score=buy,
            sell_score=sell,
            bias_label=bias,
            alert_direction=alert_direction or None,
            features=features,
            factor_rows=factor_rows,
            entry_zone=entry_zone,
            invalidation=invalidation,
            targets=targets,
        )

    def score_all(self) -> Dict[str, ScoreSnapshot]:
        out: Dict[str, ScoreSnapshot] = {}
        for sym in self.symbols:
            snap = self.score_snapshot(sym)
            if snap:
                out[sym] = snap
        return out

    def analyze_symbol(self, sym: str) -> Optional[Signal]:
        snap = self.score_snapshot(sym)
        if not snap:
            return None
        if not snap.alert_direction:
            return None
        score = snap.buy_score if snap.alert_direction == "long" else snap.sell_score
        if score < self.config.min_signal_score:
            return None
        return self.signal_from_snapshot(snap)

    def signal_from_snapshot(self, snap: ScoreSnapshot) -> Optional[Signal]:
        if not snap.alert_direction or not snap.entry_zone or snap.invalidation is None:
            return None
        score = snap.buy_score if snap.alert_direction == "long" else snap.sell_score
        if score >= 85:
            label = "strong-signal"
        elif score >= 75:
            label = "signal"
        else:
            label = "watch"
        return Signal(
            symbol=snap.symbol,
            timestamp=snap.timestamp,
            direction=snap.alert_direction,
            score=score,
            confidence_label=label,
            entry_zone=snap.entry_zone,
            invalidation=snap.invalidation,
            targets=snap.targets[:3],
            features=snap.features,
            buy_score=snap.buy_score,
            sell_score=snap.sell_score,
            bias_label=snap.bias_label,
            factor_rows=list(snap.factor_rows),
            price=snap.price,
        )

    def _trade_plan(self, sym: str, direction: str, closed: List[Candle]) -> Tuple[Tuple[float, float], float, List[float]]:
        levels = recent_equal_highs_lows(closed, tolerance_pct=self.config.equal_level_tolerance_pct)
        last = closed[-1]
        atr = self._atr(closed, period=14)
        if direction == "long":
            entry_zone = (last.close - 0.25 * atr, last.close + 0.10 * atr)
            invalidation = min(last.low, levels.get("equal_low") or last.low) - 0.20 * atr
        else:
            entry_zone = (last.close - 0.10 * atr, last.close + 0.25 * atr)
            invalidation = max(last.high, levels.get("equal_high") or last.high) + 0.20 * atr

        targets = support_resistance_targets(closed, direction)

        # Always append R-multiple targets as well as nearby S/R levels.
        # This helps the paper bot evaluate the user's +5 USDT net-profit rule
        # without being limited to tiny nearby levels on 1m/5m candles.
        risk = max(abs(last.close - invalidation), 1e-12)
        if direction == "long":
            r_targets = [last.close + risk, last.close + 2 * risk, last.close + 3 * risk]
            all_targets = [t for t in targets + r_targets if t > last.close]
            all_targets = sorted(set(round(t, 10) for t in all_targets))
        else:
            r_targets = [last.close - risk, last.close - 2 * risk, last.close - 3 * risk]
            all_targets = [t for t in targets + r_targets if t < last.close]
            all_targets = sorted(set(round(t, 10) for t in all_targets), reverse=True)
        return entry_zone, invalidation, all_targets[:3]

    def _atr(self, candles: List[Candle], period: int = 14) -> float:
        if len(candles) < period + 1:
            return max(candles[-1].range, 1e-8)
        trs = []
        recent = candles[-period - 1:]
        for prev, cur in zip(recent, recent[1:]):
            tr = max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
            trs.append(tr)
        return sum(trs) / len(trs) if trs else candles[-1].range

    async def run(self):
        # Console/signal-only runner retained for compatibility.
        market_streams = []
        market_streams += kline_streams(self.symbols, self.config.interval)
        market_streams += agg_trade_streams(self.symbols)
        market_streams += mark_price_streams(self.symbols)

        async for event in stream_events(market_streams, self.config.market_ws_base, self.config.websocket_chunk_size):
            self.update_state(event)
            if isinstance(event, Candle) and event.is_closed:
                for sym in self.shortlist_symbols():
                    sig = self.analyze_symbol(sym)
                    if sig:
                        key = f"{sig.symbol}:{sig.timestamp.isoformat()}:{sig.direction}"
                        if key not in self.latest_signals:
                            self.latest_signals[key] = sig
                            yield sig
