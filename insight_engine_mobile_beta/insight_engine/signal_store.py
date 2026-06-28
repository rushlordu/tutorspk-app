from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import Signal


FIELDNAMES = [
    "timestamp_utc",
    "symbol",
    "direction",
    "score",
    "buy_score",
    "sell_score",
    "bias",
    "confidence",
    "signal_price",
    "entry_low",
    "entry_high",
    "invalidation",
    "targets",
    "sessions",
    "candle_trigger",
    "takeoff_score",
    "fall_score",
    "takeoff_signal",
    "fall_signal",
    "flow_delta",
    "book_imbalance",
    "volume_impulse",
    "btc_alignment",
    "notes",
]


def signal_to_row(signal: Signal) -> dict:
    f = signal.features
    return {
        "timestamp_utc": signal.timestamp.isoformat(),
        "symbol": signal.symbol,
        "direction": signal.direction,
        "score": signal.score,
        "buy_score": signal.buy_score,
        "sell_score": signal.sell_score,
        "bias": signal.bias_label,
        "confidence": signal.confidence_label,
        "signal_price": "" if signal.price is None else f"{signal.price:.8g}",
        "entry_low": f"{signal.entry_zone[0]:.8g}",
        "entry_high": f"{signal.entry_zone[1]:.8g}",
        "invalidation": f"{signal.invalidation:.8g}",
        "targets": ";".join(f"{x:.8g}" for x in signal.targets),
        "sessions": ";".join(f.session_tags),
        "candle_trigger": f.candle_trigger or "",
        "takeoff_score": f.takeoff_score,
        "fall_score": f.fall_score,
        "takeoff_signal": f.takeoff_signal or "",
        "fall_signal": f.fall_signal or "",
        "flow_delta": f"{f.buy_sell_delta:.6g}",
        "book_imbalance": f"{f.book_imbalance:.6g}",
        "volume_impulse": f"{f.volume_impulse:.6g}",
        "btc_alignment": f.btc_alignment or "",
        "notes": " | ".join(f.notes[:12]),
    }


def append_signal(signal: Signal, path: str | Path = "signals/insight_signals.csv") -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    exists = out.exists() and out.stat().st_size > 0
    with out.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerow(signal_to_row(signal))
    return out
