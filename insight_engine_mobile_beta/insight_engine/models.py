from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple
from collections import deque


@dataclass
class Candle:
    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def direction(self) -> str:
        if self.close > self.open:
            return "bull"
        if self.close < self.open:
            return "bear"
        return "doji"

    @property
    def close_time(self) -> datetime:
        return datetime.fromtimestamp(self.close_time_ms / 1000, tz=timezone.utc)


@dataclass
class TradePrint:
    symbol: str
    event_time_ms: int
    price: float
    qty: float
    is_buyer_maker: bool

    @property
    def taker_side(self) -> str:
        # Binance aggTrade: m=True means buyer is maker, so sell aggressor hit bid.
        return "sell" if self.is_buyer_maker else "buy"

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass
class BookSnapshot:
    symbol: str
    event_time_ms: int
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]

    @property
    def bid_notional(self) -> float:
        return sum(p * q for p, q in self.bids)

    @property
    def ask_notional(self) -> float:
        return sum(p * q for p, q in self.asks)

    @property
    def imbalance(self) -> float:
        total = self.bid_notional + self.ask_notional
        if total <= 0:
            return 0.0
        return (self.bid_notional - self.ask_notional) / total


@dataclass
class SymbolState:
    symbol: str
    candles: Deque[Candle] = field(default_factory=lambda: deque(maxlen=300))
    trades: Deque[TradePrint] = field(default_factory=lambda: deque(maxlen=2000))
    books: Deque[BookSnapshot] = field(default_factory=lambda: deque(maxlen=20))
    last_price: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None

    def add_candle(self, candle: Candle) -> None:
        if self.candles and self.candles[-1].open_time_ms == candle.open_time_ms:
            self.candles[-1] = candle
        else:
            self.candles.append(candle)
        self.last_price = candle.close

    def add_trade(self, trade: TradePrint) -> None:
        self.trades.append(trade)
        self.last_price = trade.price

    def add_book(self, book: BookSnapshot) -> None:
        self.books.append(book)


@dataclass
class SetupFeatures:
    symbol: str
    timestamp: datetime
    direction: str  # long / short / neutral
    session_tags: List[str]
    market_structure: str = "unknown"
    equal_highs: bool = False
    equal_lows: bool = False
    equal_high_level: Optional[float] = None
    equal_low_level: Optional[float] = None
    bullish_sweep: bool = False
    bearish_sweep: bool = False
    bullish_fvg: bool = False
    bearish_fvg: bool = False
    bullish_fvg_zone: Optional[Tuple[float, float]] = None
    bearish_fvg_zone: Optional[Tuple[float, float]] = None
    bullish_reclaim: bool = False
    bearish_reclaim: bool = False
    candle_trigger: Optional[str] = None
    volume_impulse: float = 0.0
    buy_sell_delta: float = 0.0
    book_imbalance: float = 0.0
    btc_alignment: Optional[str] = None
    recent_momentum_pct: float = 0.0
    last_candle_direction: str = "doji"
    last_close_position: float = 0.5  # 0=near low, 1=near high
    takeoff_signal: Optional[str] = None
    fall_signal: Optional[str] = None
    takeoff_score: int = 0
    fall_score: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def candle_urgency_label(self) -> str:
        if self.takeoff_score >= 25 and self.takeoff_score >= self.fall_score + 8:
            return f"TAKEOFF {self.takeoff_score}"
        if self.fall_score >= 25 and self.fall_score >= self.takeoff_score + 8:
            return f"FALL {self.fall_score}"
        if self.takeoff_score >= 25 and self.fall_score >= 25:
            return f"CONFLICT {self.takeoff_score}/{self.fall_score}"
        return "--"


@dataclass
class ScoreSnapshot:
    symbol: str
    timestamp: datetime
    price: Optional[float]
    buy_score: int
    sell_score: int
    bias_label: str
    alert_direction: Optional[str]
    features: SetupFeatures
    factor_rows: List[Tuple[str, int, int, str]] = field(default_factory=list)
    entry_zone: Optional[Tuple[float, float]] = None
    invalidation: Optional[float] = None
    targets: List[float] = field(default_factory=list)

    @property
    def dominant_score(self) -> int:
        return max(self.buy_score, self.sell_score)

    @property
    def direction(self) -> str:
        if self.alert_direction:
            return self.alert_direction
        if self.buy_score > self.sell_score + 8:
            return "long"
        if self.sell_score > self.buy_score + 8:
            return "short"
        return "neutral"


@dataclass
class Signal:
    symbol: str
    timestamp: datetime
    direction: str
    score: int
    confidence_label: str
    entry_zone: Tuple[float, float]
    invalidation: float
    targets: List[float]
    features: SetupFeatures
    # Frozen at the exact moment the alert was generated.
    buy_score: int = 0
    sell_score: int = 0
    bias_label: str = ""
    factor_rows: List[Tuple[str, int, int, str]] = field(default_factory=list)
    price: Optional[float] = None

    def one_line(self) -> str:
        ez = f"{self.entry_zone[0]:.6g}-{self.entry_zone[1]:.6g}"
        tg = ", ".join(f"{x:.6g}" for x in self.targets)
        return (
            f"{self.symbol} | {self.direction.upper()} | score={self.score} ({self.confidence_label}) | "
            f"entry={ez} | invalidation={self.invalidation:.6g} | targets=[{tg}]"
        )
