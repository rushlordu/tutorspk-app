from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class EngineConfig:
    interval: str = "1m"
    candle_buffer: int = 300
    deep_limit: int = 15
    min_signal_score: int = 75
    signal_cooldown_seconds: int = 600
    equal_level_tolerance_pct: float = 0.0015  # 0.15%
    sweep_tolerance_pct: float = 0.0005
    volume_impulse_threshold: float = 1.8
    trade_flow_window: int = 120
    websocket_chunk_size: int = 180
    # Set INSIGHT_PROVIDER=bybit on Render if Binance blocks cloud traffic.
    market_provider: str = "binance"  # binance / bybit
    market_ws_base: str = "wss://fstream.binance.com/market/stream?streams="
    public_ws_base: str = "wss://fstream.binance.com/public/stream?streams="
    rest_base: str = "https://fapi.binance.com"
    bybit_rest_base: str = "https://api.bybit.com"
    bybit_ws_base: str = "wss://stream.bybit.com/v5/public/linear"
    include_depth_for_deep_symbols: bool = True
    depth_levels: int = 20
    depth_speed_ms: int = 500
    historical_klines_limit: int = 120
    watch_sessions: bool = True
    quote_asset: str = "USDT"
    # Stable/high-cap core shown first. ZEC is deliberately not in this fixed core.
    core_symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"
    ])
    # Dynamic scanner will avoid these unless the user manually types them in the top bar.
    dynamic_exclude_symbols: List[str] = field(default_factory=lambda: [
        "USDCUSDT", "BUSDUSDT", "FDUSDUSDT", "TUSDUSDT", "USDEUSDT"
    ])
    shortlist_refresh_minutes: int = 15
    min_dynamic_quote_volume: float = 50_000_000.0
    default_symbols: List[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "TRXUSDT", "LINKUSDT", "BCHUSDT",
        "AVAXUSDT", "LTCUSDT", "SUIUSDT", "DOTUSDT", "NEARUSDT"
    ])
