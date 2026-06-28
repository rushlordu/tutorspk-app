from __future__ import annotations

import asyncio
import json
import random
from typing import AsyncIterator, Dict, Iterable, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from .config import EngineConfig
from .models import BookSnapshot, Candle, TradePrint


def chunks(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def kline_streams(symbols: Iterable[str], interval: str) -> List[str]:
    return [f"{s.lower()}@kline_{interval}" for s in symbols]


def agg_trade_streams(symbols: Iterable[str]) -> List[str]:
    return [f"{s.lower()}@aggTrade" for s in symbols]


def mark_price_streams(symbols: Iterable[str]) -> List[str]:
    return [f"{s.lower()}@markPrice@1s" for s in symbols]


def partial_depth_streams(symbols: Iterable[str], levels: int = 20, speed_ms: int = 500) -> List[str]:
    speed = "100ms" if speed_ms <= 100 else "500ms"
    return [f"{s.lower()}@depth{levels}@{speed}" for s in symbols]


def parse_event(payload: dict):
    data = payload.get("data", payload)
    event_type = data.get("e")

    if event_type == "kline":
        k = data["k"]
        return Candle(
            symbol=data["s"],
            interval=k["i"],
            open_time_ms=int(k["t"]),
            close_time_ms=int(k["T"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            is_closed=bool(k["x"]),
        )

    if event_type == "aggTrade":
        return TradePrint(
            symbol=data["s"],
            event_time_ms=int(data["E"]),
            price=float(data["p"]),
            qty=float(data["q"]),
            is_buyer_maker=bool(data["m"]),
        )

    # Partial book depth events vary; usually e=depthUpdate with b/a arrays.
    if event_type == "depthUpdate" and "b" in data and "a" in data:
        return BookSnapshot(
            symbol=data["s"],
            event_time_ms=int(data["E"]),
            bids=[(float(p), float(q)) for p, q in data.get("b", [])],
            asks=[(float(p), float(q)) for p, q in data.get("a", [])],
        )

    if event_type == "markPriceUpdate":
        # Return a compact dict to keep state updates simple.
        return {
            "type": "mark",
            "symbol": data.get("s"),
            "mark_price": float(data.get("p", 0)),
            "funding_rate": float(data.get("r", 0)),
            "event_time_ms": int(data.get("E", 0)),
        }

    return None


async def stream_events(streams: List[str], base_url: str, chunk_size: int = 180) -> AsyncIterator[object]:
    """Yield parsed Binance events from combined streams.

    Reconnects automatically. For production, add persistent logs.
    """
    stream_groups = list(chunks(streams, chunk_size))
    queues: List[asyncio.Queue] = [asyncio.Queue(maxsize=5000) for _ in stream_groups]

    async def run_group(group: List[str], q: asyncio.Queue):
        url = base_url + "/".join(group)
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=None, close_timeout=5, max_queue=4096) as ws:
                    backoff = 1.0
                    async for msg in ws:
                        try:
                            payload = json.loads(msg)
                            event = parse_event(payload)
                            if event is not None:
                                try:
                                    q.put_nowait(event)
                                except asyncio.QueueFull:
                                    _ = q.get_nowait()
                                    q.put_nowait(event)
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
