from __future__ import annotations

import asyncio
import json
import random
from typing import AsyncIterator, Iterable, List

import websockets
from websockets.exceptions import ConnectionClosed

from .models import BookSnapshot, Candle, TradePrint


def chunks(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _bybit_interval(interval: str) -> str:
    mapping = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
        "1h": "60", "2h": "120", "4h": "240", "1d": "D"
    }
    return mapping.get(interval, interval.replace("m", ""))


def kline_streams(symbols: Iterable[str], interval: str) -> List[str]:
    i = _bybit_interval(interval)
    return [f"kline.{i}.{s.upper()}" for s in symbols]


def agg_trade_streams(symbols: Iterable[str]) -> List[str]:
    return [f"publicTrade.{s.upper()}" for s in symbols]


def mark_price_streams(symbols: Iterable[str]) -> List[str]:
    return [f"tickers.{s.upper()}" for s in symbols]


def partial_depth_streams(symbols: Iterable[str], levels: int = 20, speed_ms: int = 500) -> List[str]:
    # Bybit supports orderbook.1/50/200/500 on linear. 50 is a good middle ground.
    level = 50 if levels >= 20 else 1
    return [f"orderbook.{level}.{s.upper()}" for s in symbols]


def parse_event(payload: dict):
    topic = payload.get("topic", "")
    data = payload.get("data")
    if not topic or data is None:
        return None

    if topic.startswith("kline."):
        rows = data if isinstance(data, list) else [data]
        events = []
        for k in rows:
            try:
                sym = k.get("symbol") or topic.split(".")[-1]
                interval = str(k.get("interval") or topic.split(".")[1])
                interval_map = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h", "D": "1d"}
                events.append(Candle(
                    symbol=sym,
                    interval=interval_map.get(interval, interval),
                    open_time_ms=int(k.get("start")),
                    close_time_ms=int(k.get("end")),
                    open=float(k.get("open")),
                    high=float(k.get("high")),
                    low=float(k.get("low")),
                    close=float(k.get("close")),
                    volume=float(k.get("volume")),
                    is_closed=bool(k.get("confirm")),
                ))
            except Exception:
                continue
        return events

    if topic.startswith("publicTrade."):
        rows = data if isinstance(data, list) else [data]
        events = []
        for t in rows:
            try:
                side = str(t.get("S", "")).lower()
                events.append(TradePrint(
                    symbol=t.get("s") or topic.split(".")[-1],
                    event_time_ms=int(t.get("T") or payload.get("ts") or 0),
                    price=float(t.get("p")),
                    qty=float(t.get("v")),
                    # In our internal model, True means sell aggressor hit bid.
                    is_buyer_maker=(side == "sell"),
                ))
            except Exception:
                continue
        return events

    if topic.startswith("orderbook."):
        try:
            sym = data.get("s") or topic.split(".")[-1]
            return BookSnapshot(
                symbol=sym,
                event_time_ms=int(data.get("ts") or payload.get("ts") or 0),
                bids=[(float(p), float(q)) for p, q in data.get("b", [])],
                asks=[(float(p), float(q)) for p, q in data.get("a", [])],
            )
        except Exception:
            return None

    if topic.startswith("tickers."):
        try:
            sym = data.get("symbol") or topic.split(".")[-1]
            price = data.get("markPrice") or data.get("lastPrice")
            return {
                "type": "mark",
                "symbol": sym,
                "mark_price": float(price or 0),
                "funding_rate": float(data.get("fundingRate") or 0),
                "event_time_ms": int(payload.get("ts") or 0),
            }
        except Exception:
            return None

    return None


async def stream_events(streams: List[str], base_url: str, chunk_size: int = 30) -> AsyncIterator[object]:
    """Yield parsed Bybit public websocket events.

    Bybit uses a subscribe frame instead of Binance combined-stream URLs.
    """
    stream_groups = list(chunks(streams, max(1, min(chunk_size, 30))))
    queues: List[asyncio.Queue] = [asyncio.Queue(maxsize=5000) for _ in stream_groups]

    async def run_group(group: List[str], q: asyncio.Queue):
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(base_url, ping_interval=20, close_timeout=5, max_queue=4096) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": group}))
                    backoff = 1.0
                    async for msg in ws:
                        try:
                            payload = json.loads(msg)
                            event = parse_event(payload)
                            if event is None:
                                continue
                            events = event if isinstance(event, list) else [event]
                            for e in events:
                                try:
                                    q.put_nowait(e)
                                except asyncio.QueueFull:
                                    _ = q.get_nowait()
                                    q.put_nowait(e)
                        except Exception:
                            continue
            except (ConnectionClosed, OSError, asyncio.TimeoutError):
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 30)
            except Exception:
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, 30)

    tasks = [asyncio.create_task(run_group(g, q)) for g, q in zip(stream_groups, queues)]
    try:
        while True:
            get_tasks = [asyncio.create_task(q.get()) for q in queues]
            done, pending = await asyncio.wait(get_tasks, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            for d in done:
                yield d.result()
    finally:
        for t in tasks:
            t.cancel()
