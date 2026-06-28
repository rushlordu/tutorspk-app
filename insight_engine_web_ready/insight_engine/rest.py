from __future__ import annotations

import asyncio
import math
import time
from typing import Dict, List, Optional, Tuple

import aiohttp

from .config import EngineConfig
from .models import Candle


async def fetch_json(session: aiohttp.ClientSession, url: str, params: Optional[dict] = None):
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        return await resp.json()


def normalize_symbol(symbol: str, quote_asset: str = "USDT") -> str:
    """Accept BTC or BTCUSDT and return BTCUSDT-style uppercase symbols."""
    s = (symbol or "").upper().strip().replace("/", "").replace("-", "")
    if not s:
        return ""
    if not s.endswith(quote_asset):
        s = f"{s}{quote_asset}"
    return s


async def get_exchange_usdt_perp_symbols(config: EngineConfig) -> List[str]:
    """Return active Binance USD-M USDT perpetual symbols."""
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, f"{config.rest_base}/fapi/v1/exchangeInfo")
    symbols: List[str] = []
    for row in data.get("symbols", []):
        try:
            if (
                row.get("contractType") == "PERPETUAL"
                and row.get("quoteAsset") == config.quote_asset
                and row.get("status") == "TRADING"
            ):
                symbols.append(row["symbol"])
        except Exception:
            continue
    return symbols


async def get_book_tickers(config: EngineConfig) -> Dict[str, dict]:
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, f"{config.rest_base}/fapi/v1/ticker/bookTicker")
    return {r.get("symbol", ""): r for r in data if r.get("symbol")}


async def get_top_usdt_perp_symbols(config: EngineConfig, limit: int = 15) -> List[str]:
    """Return top USDT symbols by quote volume from Binance Futures 24h ticker.

    Kept for backward compatibility. v3.3 GUI uses get_core_plus_dynamic_symbols.
    """
    selected, _reasons = await get_core_plus_dynamic_symbols(config, limit=limit, manual_symbols=[])
    return selected


def _safe_float(value, default: float = 0.0) -> float:
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _stable_unique(symbols: List[str], available: Optional[set[str]] = None) -> List[str]:
    out: List[str] = []
    for s in symbols:
        if not s:
            continue
        s = s.upper().strip()
        if available is not None and s not in available:
            continue
        if s not in out:
            out.append(s)
    return out


async def get_core_plus_dynamic_symbols(
    config: EngineConfig,
    limit: int = 15,
    manual_symbols: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, str]]:
    """Build the v3.3 watchlist: fixed high-cap core + user's typed symbols + dynamic opportunities.

    Priority order:
    1. Fixed high-cap core from config.core_symbols. ZEC is not in this core.
    2. User symbols typed in the top bar are treated as pins/requests and are included if Binance supports them.
    3. Remaining slots are filled by a dynamic tradability/opportunity score using Binance Futures public data.

    Returns (symbols, reason_by_symbol).
    """
    limit = max(1, min(15, int(limit)))
    manual_symbols = manual_symbols or []

    async with aiohttp.ClientSession() as session:
        tickers_task = fetch_json(session, f"{config.rest_base}/fapi/v1/ticker/24hr")
        book_task = fetch_json(session, f"{config.rest_base}/fapi/v1/ticker/bookTicker")
        exch_task = fetch_json(session, f"{config.rest_base}/fapi/v1/exchangeInfo")
        tickers_data, book_data, exch_data = await asyncio.gather(tickers_task, book_task, exch_task)

    available = set()
    for row in exch_data.get("symbols", []):
        if (
            row.get("contractType") == "PERPETUAL"
            and row.get("quoteAsset") == config.quote_asset
            and row.get("status") == "TRADING"
        ):
            available.add(row.get("symbol"))

    ticker_by_symbol = {r.get("symbol", ""): r for r in tickers_data if r.get("symbol") in available}
    book_by_symbol = {r.get("symbol", ""): r for r in book_data if r.get("symbol") in available}

    core = _stable_unique([normalize_symbol(s, config.quote_asset) for s in config.core_symbols], available)
    manual = _stable_unique([normalize_symbol(s, config.quote_asset) for s in manual_symbols], available)

    selected: List[str] = []
    reasons: Dict[str, str] = {}

    for s in core:
        if len(selected) >= limit:
            break
        selected.append(s)
        reasons[s] = "fixed high-cap core"

    for s in manual:
        if len(selected) >= limit:
            break
        if s not in selected:
            selected.append(s)
            reasons[s] = "manual top-bar symbol"

    # Dynamic ranking: liquidity first, then tradable movement, tight spread, and trade activity.
    rows: List[Tuple[float, str, str]] = []
    for sym, row in ticker_by_symbol.items():
        if sym in selected:
            continue
        if sym in set(config.dynamic_exclude_symbols):
            continue
        quote_volume = _safe_float(row.get("quoteVolume"))
        if quote_volume < config.min_dynamic_quote_volume:
            continue
        pct = abs(_safe_float(row.get("priceChangePercent")))
        count = _safe_float(row.get("count"))
        book = book_by_symbol.get(sym, {})
        bid = _safe_float(book.get("bidPrice"))
        ask = _safe_float(book.get("askPrice"))
        spread_pct = 9.99
        if bid > 0 and ask > 0 and ask >= bid:
            spread_pct = (ask - bid) / ((ask + bid) / 2) * 100

        # Keep it trader-friendly: reward volume and movement, but penalize chaotic extreme moves and wide spreads.
        volume_score = min(35.0, math.log10(max(quote_volume, 1.0)) * 4.0)
        activity_score = min(20.0, math.log10(max(count, 1.0)) * 3.0)
        movement_score = min(18.0, pct * 1.8)
        if pct > 25:
            movement_score -= min(12.0, (pct - 25) * 0.5)  # avoid pure mania picks
        spread_score = max(0.0, 18.0 - spread_pct * 300.0)
        stability_bonus = 6.0 if pct <= 12 else 0.0
        score = volume_score + activity_score + movement_score + spread_score + stability_bonus

        reason = (
            f"dynamic pick | 24h vol ${quote_volume/1_000_000:.1f}M, "
            f"24h move {pct:.2f}%, spread {spread_pct:.3f}%"
        )
        rows.append((score, sym, reason))

    rows.sort(reverse=True)
    for _score, sym, reason in rows:
        if len(selected) >= limit:
            break
        if sym not in selected:
            selected.append(sym)
            reasons[sym] = reason

    # Absolute fallback if filters were too strict.
    if len(selected) < limit:
        fallback = sorted(
            (( _safe_float(r.get("quoteVolume")), sym) for sym, r in ticker_by_symbol.items() if sym not in selected),
            reverse=True,
        )
        for _qv, sym in fallback:
            if len(selected) >= limit:
                break
            selected.append(sym)
            reasons[sym] = "fallback high-volume filler"

    return selected[:limit], reasons


async def get_historical_klines(config: EngineConfig, symbol: str, interval: str, limit: int = 120) -> List[Candle]:
    """Fetch recent Binance Futures candles so the dashboard has scores immediately."""
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(
            session,
            f"{config.rest_base}/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
    now_ms = int(time.time() * 1000)
    candles: List[Candle] = []
    for row in data:
        # Binance kline REST format:
        # [openTime, open, high, low, close, volume, closeTime, quoteVolume, ...]
        close_time_ms = int(row[6])
        candles.append(
            Candle(
                symbol=symbol,
                interval=interval,
                open_time_ms=int(row[0]),
                close_time_ms=close_time_ms,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                is_closed=close_time_ms <= now_ms,
            )
        )
    return candles


async def preload_historical_klines(config: EngineConfig, symbols: List[str], interval: str, limit: int = 120) -> Dict[str, List[Candle]]:
    """Load recent candles concurrently with a small semaphore for laptop/network stability."""
    sem = asyncio.Semaphore(6)

    async def one(sym: str):
        async with sem:
            try:
                return sym, await get_historical_klines(config, sym, interval, limit)
            except Exception:
                return sym, []

    pairs = await asyncio.gather(*(one(s) for s in symbols))
    return {sym: candles for sym, candles in pairs}


async def get_open_interest(config: EngineConfig, symbol: str) -> Optional[float]:
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, f"{config.rest_base}/fapi/v1/openInterest", {"symbol": symbol})
    try:
        return float(data["openInterest"])
    except Exception:
        return None


async def get_recent_funding_rate(config: EngineConfig, symbol: str) -> Optional[float]:
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, f"{config.rest_base}/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
    try:
        if isinstance(data, list) and data:
            return float(data[-1]["fundingRate"])
    except Exception:
        return None
    return None
