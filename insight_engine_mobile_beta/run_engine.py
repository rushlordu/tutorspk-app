from __future__ import annotations

import argparse
import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from insight_engine.config import EngineConfig
from insight_engine.engine import InsightEngine
from insight_engine.rest import get_top_usdt_perp_symbols

console = Console()


def parse_symbols(raw: str):
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


async def main():
    parser = argparse.ArgumentParser(description="INSIGHT Trading Engine v1 - signal only")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ZECUSDT", help="Comma-separated symbols")
    parser.add_argument("--auto-symbols", type=int, default=0, help="Auto-select top N USDT futures symbols by 24h quote volume")
    parser.add_argument("--interval", default="1m", help="Kline interval, e.g. 1m, 5m, 15m")
    parser.add_argument("--deep-limit", type=int, default=8, help="Number of symbols to deep analyze after light scan")
    parser.add_argument("--min-score", type=int, default=65, help="Minimum score to print")
    args = parser.parse_args()

    config = EngineConfig(interval=args.interval, deep_limit=args.deep_limit, min_signal_score=args.min_score)

    if args.auto_symbols > 0:
        symbols = await get_top_usdt_perp_symbols(config, args.auto_symbols)
    else:
        symbols = parse_symbols(args.symbols)

    console.print(Panel.fit(
        f"INSIGHT Engine v1 running in SIGNAL-ONLY mode\n"
        f"Symbols: {', '.join(symbols)}\n"
        f"Interval: {config.interval} | Deep limit: {config.deep_limit} | Min score: {config.min_signal_score}\n"
        f"No real orders will be placed.",
        title="INSIGHT",
        border_style="green",
    ))

    engine = InsightEngine(symbols, config)
    async for sig in engine.run():
        notes = "\n".join(f"- {n}" for n in sig.features.notes[:8]) or "- no notes"
        sessions = ", ".join(sig.features.session_tags)
        body = (
            f"[bold]{sig.one_line()}[/bold]\n\n"
            f"Sessions: {sessions}\n"
            f"Flow delta: {sig.features.buy_sell_delta:.2f} | Book imbalance: {sig.features.book_imbalance:.2f} | "
            f"Vol impulse: {sig.features.volume_impulse:.2f}x\n\n"
            f"Reasons:\n{notes}"
        )
        color = "green" if sig.direction == "long" else "red"
        console.print(Panel(body, title=f"{sig.symbol} {sig.direction.upper()} SETUP", border_style=color))


if __name__ == "__main__":
    asyncio.run(main())
