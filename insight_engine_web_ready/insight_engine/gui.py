from __future__ import annotations

import argparse
import asyncio
import queue
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from .binance_ws import (
    agg_trade_streams,
    kline_streams,
    mark_price_streams,
    partial_depth_streams,
    stream_events,
)
from .config import EngineConfig
from .engine import InsightEngine
from .flow import latest_book_imbalance, trade_flow_delta, volume_impulse
from .models import Candle, ScoreSnapshot, Signal, TradePrint
from .rest import get_core_plus_dynamic_symbols, normalize_symbol, preload_historical_klines
from .signal_store import append_signal
from .virtual_trader import VirtualTrader, VirtualTrade
from .binance_demo_executor import BinanceFuturesDemoExecutor


MAJOR_DEFAULTS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT"]


def score_bar(score: int, width: int = 10) -> str:
    score = max(0, min(100, int(score)))
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


class InsightDashboard(tk.Tk):
    """Lightweight Tkinter dashboard for INSIGHT Engine v3.7.

    v3.7 keeps all 15 coins visible, uses a core + dynamic shortlist,
    honors manual top-bar coins as pins, adds a Coin Shuffle button,
    and adds a visible paper-bot trade scanner with skip diagnostics.
    """

    def __init__(self, initial_symbols: List[str], config: EngineConfig, auto_symbols: int = 15):
        super().__init__()
        self.title("INSIGHT Live Trading Dashboard v3.8.0 - Signal + Paper Bot + API Demo")
        self.geometry("1680x950")
        self.minsize(1250, 760)

        self.config = config
        self.initial_symbols = initial_symbols[:15]
        self.auto_symbols = auto_symbols
        self.engine: Optional[InsightEngine] = None

        self.ui_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.running = False
        self.start_time = time.time()
        self.signal_count = 0
        self.signal_keys: set[str] = set()
        self.latest_rows: Dict[str, dict] = {}
        self.latest_snapshots: Dict[str, ScoreSnapshot] = {}
        self.ticker_labels: Dict[str, dict] = {}
        self.saved_signals: List[Signal] = []
        self.selected_symbol: Optional[str] = None
        self.pending_restart_symbols: Optional[List[str]] = None
        self.shuffle_in_progress = False
        self.shortlist_reasons: Dict[str, str] = {}
        self.last_generated_symbols_text = ",".join(self.initial_symbols)
        self.virtual_bot = VirtualTrader()
        self.api_demo = BinanceFuturesDemoExecutor()
        self.api_demo_enabled_var = tk.BooleanVar(value=False)
        self.bot_enabled_var = tk.BooleanVar(value=True)
        self.bot_score_var = tk.IntVar(value=55)
        self.bot_scan_seconds_var = tk.IntVar(value=5)
        self.last_bot_scan_ts = 0.0
        self.last_bot_attempt_ts: Dict[str, float] = {}
        self.last_bot_skip_ts: Dict[str, float] = {}

        self._build_style()
        self._build_layout()
        self.after(200, self._process_queue)
        self.after(1000, self._tick_clock)

    def _build_style(self) -> None:
        self.configure(bg="#101418")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#101418")
        style.configure("Card.TFrame", background="#172027", relief="flat")
        style.configure("TLabel", background="#101418", foreground="#d8dee9", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#101418", foreground="#ffffff", font=("Segoe UI", 16, "bold"))
        style.configure("Small.TLabel", background="#101418", foreground="#93a4b3", font=("Segoe UI", 9))
        style.configure("Card.TLabel", background="#172027", foreground="#d8dee9", font=("Segoe UI", 10))
        style.configure("Price.TLabel", background="#172027", foreground="#ffffff", font=("Segoe UI", 15, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure(
            "Treeview",
            background="#111820",
            foreground="#d8dee9",
            fieldbackground="#111820",
            rowheight=24,
            borderwidth=0,
            font=("Consolas", 10),
        )
        style.configure("Treeview.Heading", background="#202a33", foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#2a5d84")], foreground=[("selected", "#ffffff")])
        style.configure("TLabelframe", background="#101418", foreground="#d8dee9")
        style.configure("TLabelframe.Label", background="#101418", foreground="#ffffff", font=("Segoe UI", 10, "bold"))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)
        ttk.Label(header, text="INSIGHT Live Trading Dashboard v3.7.3", style="Title.TLabel").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Idle | 5 fixed core + 10 dynamic coins | Paper Bot + API Demo")
        ttk.Label(header, textvariable=self.status_var, style="Small.TLabel").pack(side=tk.RIGHT)

        # Controls are split into two rows so Start/Stop/Coin Shuffle never get pushed
        # off-screen on laptop displays. v3.7 had too many controls on one row.
        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, pady=(8, 3))

        ttk.Label(controls, text="Symbols:").pack(side=tk.LEFT, padx=(0, 4))
        self.symbol_var = tk.StringVar(value=",".join(self.initial_symbols))
        self.symbol_entry = ttk.Entry(controls, textvariable=self.symbol_var, width=54)
        self.symbol_entry.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)

        self.start_btn = ttk.Button(controls, text="▶ Start", style="Accent.TButton", command=self.start_engine)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn = ttk.Button(controls, text="■ Stop", command=self.stop_engine, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.shuffle_btn = ttk.Button(controls, text="Coin Shuffle", command=self.coin_shuffle)
        self.shuffle_btn.pack(side=tk.LEFT, padx=(0, 0))

        controls2 = ttk.Frame(root)
        controls2.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(controls2, text="Interval:").pack(side=tk.LEFT, padx=(0, 4))
        self.interval_var = tk.StringVar(value=self.config.interval)
        ttk.Combobox(
            controls2,
            textvariable=self.interval_var,
            values=["1m", "3m", "5m", "15m", "30m", "1h", "4h"],
            width=6,
            state="readonly",
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(controls2, text="Auto slots:").pack(side=tk.LEFT, padx=(0, 4))
        self.auto_var = tk.IntVar(value=self.auto_symbols)
        ttk.Spinbox(controls2, from_=0, to=15, textvariable=self.auto_var, width=5).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(controls2, text="Alert score:").pack(side=tk.LEFT, padx=(0, 4))
        self.min_score_var = tk.IntVar(value=self.config.min_signal_score)
        ttk.Spinbox(controls2, from_=50, to=95, increment=5, textvariable=self.min_score_var, width=5).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(controls2, text="Paper Bot", variable=self.bot_enabled_var).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Checkbutton(controls2, text="API Demo", variable=self.api_demo_enabled_var, command=self._toggle_api_demo).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(controls2, text="Test API", command=self._test_api_demo_connection).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(controls2, text="Bot score:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Spinbox(controls2, from_=45, to=95, increment=5, textvariable=self.bot_score_var, width=5).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(controls2, text="20 USDT x10 | net TP >3 | SL 3", style="Small.TLabel").pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(controls2, text="Depth:").pack(side=tk.LEFT, padx=(0, 4))
        self.deep_var = tk.IntVar(value=self.config.deep_limit)
        ttk.Spinbox(controls2, from_=3, to=15, textvariable=self.deep_var, width=5).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(controls2, text="Top-bar coins are manual pins. Bot scans valid score setups and explains skips.", style="Small.TLabel").pack(side=tk.LEFT)

        self.ticker_frame = ttk.Frame(root)
        self.ticker_frame.pack(fill=tk.X, pady=(0, 10))
        self._build_ticker_cards(self.initial_symbols)

        body = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(body)
        middle = ttk.Frame(body)
        right = ttk.Frame(body)
        body.add(left, weight=1)
        body.add(middle, weight=3)
        body.add(right, weight=1)

        feed_box = ttk.Labelframe(left, text="Live Feed")
        feed_box.pack(fill=tk.BOTH, expand=True, padx=(0, 8))
        self.feed_text = tk.Text(
            feed_box,
            bg="#0c1117",
            fg="#d8dee9",
            insertbackground="#ffffff",
            height=20,
            wrap=tk.WORD,
            font=("Consolas", 10),
            relief=tk.FLAT,
        )
        self.feed_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        feed_scroll = ttk.Scrollbar(feed_box, orient=tk.VERTICAL, command=self.feed_text.yview)
        feed_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.feed_text.configure(yscrollcommand=feed_scroll.set)
        self.feed_text.tag_configure("ok", foreground="#7bd88f")
        self.feed_text.tag_configure("warn", foreground="#ffd166")
        self.feed_text.tag_configure("bad", foreground="#ff6b6b")
        self.feed_text.tag_configure("muted", foreground="#93a4b3")
        self.feed_text.tag_configure("signal", foreground="#ffffff", background="#243447")

        market_box = ttk.Labelframe(middle, text="15-Coin Buy/Sell Score Board - tuned live pressure scores; double-click for details")
        market_box.pack(fill=tk.BOTH, expand=True, padx=(0, 8))
        columns = ("symbol", "price", "move", "buybar", "buy", "sellbar", "sell", "bias", "urgency", "flow", "book", "vol", "candles", "last")
        self.market_tree = ttk.Treeview(market_box, columns=columns, show="headings", selectmode="browse", height=15)
        headings = {
            "symbol": "Coin",
            "price": "Last Price",
            "move": "1m",
            "buybar": "Buy Bar",
            "buy": "Buy",
            "sellbar": "Sell Bar",
            "sell": "Sell",
            "bias": "Bias",
            "urgency": "Candle Map",
            "flow": "Flow Δ",
            "book": "Book",
            "vol": "Vol",
            "candles": "Cndl",
            "last": "Update",
        }
        widths = {
            "symbol": 92,
            "price": 105,
            "move": 62,
            "buybar": 118,
            "buy": 44,
            "sellbar": 118,
            "sell": 44,
            "bias": 120,
            "urgency": 115,
            "flow": 70,
            "book": 65,
            "vol": 62,
            "candles": 52,
            "last": 78,
        }
        for col in columns:
            self.market_tree.heading(col, text=headings[col])
            self.market_tree.column(col, width=widths[col], anchor=tk.CENTER)
        self.market_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # No vertical scrollbar here by design: the dashboard is fixed to 15 coins
        # so the trader can monitor every score row at once.
        self.market_tree.bind("<Double-1>", self._on_symbol_double_click)
        self.market_tree.tag_configure("buy", foreground="#7bd88f")
        self.market_tree.tag_configure("sell", foreground="#ff6b6b")
        self.market_tree.tag_configure("watch", foreground="#ffd166")
        self.market_tree.tag_configure("conflict", foreground="#c792ea")
        self.market_tree.tag_configure("neutral", foreground="#d8dee9")

        detail_box = ttk.Labelframe(middle, text="Frozen Snapshot / Instructions")
        detail_box.pack(fill=tk.BOTH, expand=True, padx=(0, 8), pady=(8, 0))
        self.detail_text = tk.Text(
            detail_box,
            bg="#0c1117",
            fg="#d8dee9",
            height=16,
            wrap=tk.WORD,
            font=("Consolas", 10),
            relief=tk.FLAT,
        )
        self.detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll = ttk.Scrollbar(detail_box, orient=tk.VERTICAL, command=self.detail_text.yview)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        self.detail_text.insert(tk.END, "All 15 coins remain visible in the board above.\nDouble-click any coin to open a frozen backend score explanation for that exact moment.\nClick any saved signal on the right to open the frozen signal explanation from the moment it was generated.\n")

        sig_box = ttk.Labelframe(right, text="Saved Signals / Alerts")
        sig_box.pack(fill=tk.BOTH, expand=True)
        self.signal_list = tk.Listbox(
            sig_box,
            bg="#0c1117",
            fg="#d8dee9",
            selectbackground="#2a5d84",
            font=("Consolas", 10),
            relief=tk.FLAT,
            activestyle="none",
        )
        self.signal_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.signal_list.bind("<<ListboxSelect>>", self._on_signal_select)
        sig_scroll = ttk.Scrollbar(sig_box, orient=tk.VERTICAL, command=self.signal_list.yview)
        sig_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.signal_list.configure(yscrollcommand=sig_scroll.set)

        bot_box = ttk.Labelframe(right, text="Virtual Trading Bot / API Demo Mirror")
        bot_box.pack(fill=tk.X, pady=(8, 0))
        self.bot_summary_var = tk.StringVar(value=self.virtual_bot.summary_text())
        ttk.Label(bot_box, textvariable=self.bot_summary_var, style="Small.TLabel", wraplength=360, justify=tk.LEFT).pack(fill=tk.X, padx=6, pady=(4, 4))
        trade_cols = ("id", "coin", "side", "entry", "tp", "sl", "pnl", "status", "action")
        self.bot_tree = ttk.Treeview(bot_box, columns=trade_cols, show="headings", height=8, selectmode="browse")
        trade_widths = {"id": 34, "coin": 58, "side": 54, "entry": 68, "tp": 68, "sl": 68, "pnl": 58, "status": 88, "action": 64}
        for c in trade_cols:
            self.bot_tree.heading(c, text=c.upper())
            self.bot_tree.column(c, width=trade_widths[c], anchor=tk.CENTER)
        self.bot_tree.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.bot_tree.bind("<Button-1>", self._on_bot_tree_click)
        self.bot_tree.tag_configure("open", foreground="#ffd166")
        self.bot_tree.tag_configure("win", foreground="#7bd88f")
        self.bot_tree.tag_configure("loss", foreground="#ff6b6b")
        ttk.Label(bot_box, text="Click CLOSE to manually close locally. If API Demo is ON and mirrored, testnet close is also sent.", style="Small.TLabel").pack(fill=tk.X, padx=6, pady=(0, 4))

        footer = ttk.Frame(root)
        footer.pack(fill=tk.X, pady=(8, 0))
        self.footer_var = tk.StringVar(value="Ready. Core = BTC, ETH, BNB, SOL, XRP. Top-bar coins are pins. Coin Shuffle refreshes dynamic picks.")
        ttk.Label(footer, textvariable=self.footer_var, style="Small.TLabel").pack(side=tk.LEFT)
        ttk.Button(footer, text="Clear Feed", command=lambda: self.feed_text.delete("1.0", tk.END)).pack(side=tk.RIGHT)

    def _build_ticker_cards(self, symbols: List[str]) -> None:
        for child in self.ticker_frame.winfo_children():
            child.destroy()
        self.ticker_labels.clear()
        majors = []
        for sym in MAJOR_DEFAULTS + symbols:
            if sym not in majors:
                majors.append(sym)
        majors = majors[:8]
        for sym in majors:
            card = ttk.Frame(self.ticker_frame, style="Card.TFrame", padding=(12, 8))
            card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
            name = ttk.Label(card, text=sym.replace("USDT", ""), style="Card.TLabel")
            price = ttk.Label(card, text="--", style="Price.TLabel")
            sub = ttk.Label(card, text="waiting", style="Card.TLabel")
            name.pack(anchor=tk.W)
            price.pack(anchor=tk.W)
            sub.pack(anchor=tk.W)
            self.ticker_labels[sym] = {"price": price, "sub": sub}

    def _parse_symbols(self) -> List[str]:
        raw = self.symbol_var.get()
        syms: List[str] = []
        for part in raw.replace(" ", "").split(","):
            s = normalize_symbol(part, self.config.quote_asset)
            if s and s not in syms:
                syms.append(s)
        return syms[:15]

    def _current_running_symbols(self) -> List[str]:
        if self.engine is not None and self.engine.symbols:
            return list(self.engine.symbols)
        return self._parse_symbols()

    def _manual_pins_from_entry(self) -> List[str]:
        """Return user-requested top-bar pins for auto mode.

        The entry shows the running list. To avoid accidentally pinning all 15
        current coins, only symbols newly typed/added since the last generated
        list are treated as manual pins. For a fully manual list, set Auto slots
        to 0.
        """
        typed = self._parse_symbols()
        last = [normalize_symbol(x, self.config.quote_asset) for x in self.last_generated_symbols_text.split(",") if x.strip()]
        if typed == last:
            return []
        additions = [s for s in typed if s not in last]
        if additions:
            return additions[:15]
        return typed[:15] if int(self.auto_var.get()) == 0 else []

    def start_engine(self) -> None:
        if self.running:
            return
        auto_symbols = int(self.auto_var.get())
        symbols = self._manual_pins_from_entry() if auto_symbols > 0 else self._parse_symbols()
        self._launch_engine(symbols, auto_symbols, reset_signals=True)

    def _launch_engine(self, symbols: List[str], auto_symbols: int, reset_signals: bool = True) -> None:
        self.config.interval = self.interval_var.get()
        self.config.min_signal_score = int(self.min_score_var.get())
        self.config.deep_limit = min(15, int(self.deep_var.get()))

        self._build_ticker_cards(symbols)
        self.stop_event.clear()
        self.running = True
        self.start_time = time.time()
        if reset_signals:
            self.signal_count = 0
            self.signal_keys.clear()
            self.saved_signals.clear()
            self.signal_list.delete(0, tk.END)
        self.latest_rows.clear()
        self.latest_snapshots.clear()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        # Keep the top bar editable while running. Typed coins become manual pins
        # on the next Start or Coin Shuffle.
        self.symbol_entry.configure(state=tk.NORMAL)
        self.status_var.set("Starting live feed...")
        self._feed("Starting INSIGHT v3.8.0. Paper Bot + Candle Map + optional Binance Futures TESTNET API Demo.\n", "ok")

        self.worker_thread = threading.Thread(target=self._worker_main, args=(symbols, auto_symbols, self.config), daemon=True)
        self.worker_thread.start()

    def stop_engine(self) -> None:
        self._request_stop(for_restart=False)

    def _request_stop(self, for_restart: bool = False) -> None:
        self.stop_event.set()
        self.running = False
        self.stop_btn.configure(state=tk.DISABLED)
        self.symbol_entry.configure(state=tk.NORMAL)
        if for_restart:
            self.start_btn.configure(state=tk.DISABLED)
            self.status_var.set("Restarting with shuffled coins...")
            self._feed("Coin Shuffle selected a new list. Restarting feed after current websocket cycle.\n", "warn")
        else:
            self.start_btn.configure(state=tk.NORMAL)
            self.status_var.set("Stopped | Paper Bot + API Demo")
            self._feed("Stop requested. Feed will close after current websocket cycle.\n", "warn")

    def coin_shuffle(self) -> None:
        """Re-run the shortlist criteria and optionally restart the live feed with new coins."""
        if self.shuffle_in_progress:
            return
        self.shuffle_in_progress = True
        self.shuffle_btn.configure(state=tk.DISABLED)
        self.status_var.set("Running Coin Shuffle...")
        manual_symbols = self._manual_pins_from_entry()
        current_symbols = self._current_running_symbols()
        limit = min(15, max(1, int(self.auto_var.get()) or 15))
        self._feed("Coin Shuffle requested. Rechecking core + manual pins + dynamic criteria...\n", "warn")
        threading.Thread(
            target=self._shuffle_worker,
            args=(manual_symbols, current_symbols, limit, self.config),
            daemon=True,
        ).start()

    def _shuffle_worker(self, manual_symbols: List[str], current_symbols: List[str], limit: int, config: EngineConfig) -> None:
        try:
            symbols, reasons = asyncio.run(get_core_plus_dynamic_symbols(config, limit=limit, manual_symbols=manual_symbols))
            self.ui_queue.put({
                "type": "shuffle_result",
                "symbols": symbols,
                "reasons": reasons,
                "previous": current_symbols,
            })
        except Exception as exc:
            self.ui_queue.put({"type": "shuffle_error", "message": str(exc)})

    def _describe_shortlist_changes(self, previous: List[str], symbols: List[str], reasons: Dict[str, str]) -> str:
        prev_set = set(previous)
        new_set = set(symbols)
        added = [s for s in symbols if s not in prev_set]
        removed = [s for s in previous if s not in new_set]
        lines: List[str] = []
        if added:
            lines.append("New coins added:")
            for s in added:
                lines.append(f"- {s}: {reasons.get(s, 'selected by shortlist engine')}")
        else:
            lines.append("No new coins were added.")
        if removed:
            lines.append("\nCoins removed:")
            for s in removed:
                lines.append(f"- {s}")
        else:
            lines.append("\nNo coins were removed.")
        lines.append("\nCurrent 15-coin list:")
        lines.append(", ".join(symbols))
        return "\n".join(lines)

    def _worker_main(self, symbols: List[str], auto_symbols: int, config: EngineConfig) -> None:
        try:
            asyncio.run(self._engine_loop(symbols, auto_symbols, config))
        except Exception as exc:
            self.ui_queue.put({"type": "error", "message": str(exc)})

    async def _engine_loop(self, symbols: List[str], auto_symbols: int, config: EngineConfig) -> None:
        if auto_symbols > 0:
            limit = min(15, max(1, int(auto_symbols)))
            self.ui_queue.put({"type": "feed", "message": "Building shortlist: fixed high-cap core + top-bar pins + dynamic opportunities...\n", "tag": "muted"})
            symbols, reasons = await get_core_plus_dynamic_symbols(config, limit=limit, manual_symbols=symbols)
            self.ui_queue.put({"type": "symbols", "symbols": symbols, "reasons": reasons})
            core_txt = ", ".join(config.core_symbols)
            self.ui_queue.put({"type": "feed", "message": f"Core symbols: {core_txt}. Dynamic slots filled by liquidity/movement/spread criteria.\n", "tag": "ok"})
        else:
            symbols = symbols[:15]
            reasons = {s: "manual top-bar symbol" for s in symbols}
            self.ui_queue.put({"type": "symbols", "symbols": symbols, "reasons": reasons})

        engine = InsightEngine(symbols, config)
        self.engine = engine

        self.ui_queue.put({"type": "feed", "message": f"Preloading {config.historical_klines_limit} recent candles for {len(engine.symbols)} symbols...\n", "tag": "muted"})
        history = await preload_historical_klines(config, engine.symbols, config.interval, config.historical_klines_limit)
        loaded = 0
        for sym, candles in history.items():
            for c in candles:
                engine.update_state(c)
            if candles:
                loaded += 1
        self.ui_queue.put({"type": "feed", "message": f"Historical candle preload complete: {loaded}/{len(engine.symbols)} symbols.\n", "tag": "ok"})

        last_snapshot = 0.0
        last_feed_by_symbol: Dict[str, float] = {}
        last_alert_ts: Dict[str, float] = {}

        streams = []
        streams += kline_streams(engine.symbols, config.interval)
        streams += agg_trade_streams(engine.symbols)
        streams += mark_price_streams(engine.symbols)
        depth_symbols = engine.symbols[: max(3, min(config.deep_limit, 15))]
        streams += partial_depth_streams(depth_symbols, config.depth_levels, config.depth_speed_ms)

        self.ui_queue.put({
            "type": "feed",
            "message": f"Connected stream plan: {len(engine.symbols)} symbols, {len(streams)} streams, depth on {len(depth_symbols)} symbols.\n",
            "tag": "ok",
        })

        # Initial snapshot immediately after preloading.
        snaps = engine.score_all()
        rows = self._snapshot_rows(engine, snaps)
        self.ui_queue.put({"type": "snapshot", "rows": rows, "snapshots": snaps, "running_symbols": engine.symbols})

        async for event in stream_events(streams, config.market_ws_base, config.websocket_chunk_size):
            if self.stop_event.is_set():
                break
            engine.update_state(event)
            now = time.time()

            if isinstance(event, Candle) and event.is_closed:
                self.ui_queue.put({"type": "feed", "message": self._closed_candle_line(event), "tag": "muted"})

            if isinstance(event, TradePrint):
                last = last_feed_by_symbol.get(event.symbol, 0)
                if now - last > 2.0 and event.symbol in set(MAJOR_DEFAULTS + engine.symbols[:10]):
                    last_feed_by_symbol[event.symbol] = now
                    side = "BUY" if event.taker_side == "buy" else "SELL"
                    tag = "ok" if side == "BUY" else "bad"
                    self.ui_queue.put({"type": "feed", "message": f"{self._clock()} {event.symbol:<9} {side:<4} {event.price:.8g} notional={event.notional:,.0f}\n", "tag": tag})

            if now - last_snapshot > 1.0:
                last_snapshot = now
                snaps = engine.score_all()
                rows = self._snapshot_rows(engine, snaps)
                self.ui_queue.put({"type": "snapshot", "rows": rows, "snapshots": snaps, "running_symbols": engine.symbols})

                # Alert when a score bar crosses threshold and the opposite side is not conflicting.
                for sym, snap in snaps.items():
                    if not snap.alert_direction:
                        continue
                    score = snap.buy_score if snap.alert_direction == "long" else snap.sell_score
                    if score < config.min_signal_score:
                        continue
                    cooldown_key = f"{sym}:{snap.alert_direction}"
                    if now - last_alert_ts.get(cooldown_key, 0) < config.signal_cooldown_seconds:
                        continue
                    sig = engine.signal_from_snapshot(snap)
                    if not sig:
                        continue
                    key = f"{sig.symbol}:{sig.timestamp.isoformat()}:{sig.direction}"
                    if key in self.signal_keys:
                        continue
                    self.signal_keys.add(key)
                    last_alert_ts[cooldown_key] = now
                    try:
                        append_signal(sig)
                    except Exception as exc:
                        self.ui_queue.put({"type": "feed", "message": f"Could not save signal CSV: {exc}\n", "tag": "warn"})
                    self.ui_queue.put({"type": "signal", "signal": sig})

        self.ui_queue.put({"type": "feed", "message": "Engine loop ended.\n", "tag": "warn"})
        self.ui_queue.put({"type": "loop_ended"})

    def _snapshot_rows(self, engine: InsightEngine, snapshots: Dict[str, ScoreSnapshot]) -> Dict[str, dict]:
        rows: Dict[str, dict] = {}
        for sym, st in engine.states.items():
            closed = [c for c in st.candles if c.is_closed]
            snap = snapshots.get(sym)
            last_price = st.last_price or (closed[-1].close if closed else None)
            move = "--"
            if len(closed) >= 2:
                prev = closed[-2].close
                cur = closed[-1].close
                pct = (cur - prev) / max(prev, 1e-12) * 100
                move = f"{pct:+.2f}%"
            buy = snap.buy_score if snap else 0
            sell = snap.sell_score if snap else 0
            bias = snap.bias_label if snap else "LOADING"
            rows[sym] = {
                "symbol": sym.replace("USDT", ""),
                "price": "--" if last_price is None else f"{last_price:.8g}",
                "move": move,
                "buybar": score_bar(buy),
                "buy": f"{buy:>3}",
                "sellbar": score_bar(sell),
                "sell": f"{sell:>3}",
                "bias": bias,
                "urgency": snap.features.candle_urgency_label if snap else "--",
                "flow": f"{trade_flow_delta(st.trades, engine.config.trade_flow_window):+.2f}",
                "book": f"{latest_book_imbalance(st.books):+.2f}",
                "vol": f"{volume_impulse(st.candles):.2f}x" if len(st.candles) > 5 else "--",
                "candles": str(len(closed)),
                "last": self._clock(),
            }
        return rows

    def _process_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                typ = item.get("type")
                if typ == "feed":
                    self._feed(item["message"], item.get("tag", "muted"))
                elif typ == "snapshot":
                    self.latest_snapshots = item.get("snapshots", {})
                    self._update_virtual_bot_prices(self.latest_snapshots)
                    self._paper_bot_scan_snapshots(self.latest_snapshots)
                    self._update_market(item["rows"])
                    self._refresh_bot_panel()
                    self.status_var.set(f"Live | {len(item.get('running_symbols', []))} symbols | Signals saved: {self.signal_count} | Paper trades: {len(self.virtual_bot.trades)} | {self.api_demo.summary_text()}")
                    # v3.2 deliberately does NOT auto-refresh any open explanation.
                    # Double-click/click popups are frozen at the exact moment requested.
                elif typ == "signal":
                    self._add_signal(item["signal"])
                elif typ == "symbols":
                    self.symbol_var.set(",".join(item["symbols"]))
                    self.last_generated_symbols_text = ",".join(item["symbols"])
                    self.shortlist_reasons = item.get("reasons", {})
                    self._build_ticker_cards(item["symbols"])
                elif typ == "shuffle_result":
                    self.shuffle_in_progress = False
                    self.shuffle_btn.configure(state=tk.NORMAL)
                    new_symbols = item["symbols"]
                    previous = item.get("previous", [])
                    reasons = item.get("reasons", {})
                    changed = list(previous) != list(new_symbols)
                    self.shortlist_reasons = reasons
                    self.symbol_var.set(",".join(new_symbols))
                    self.last_generated_symbols_text = ",".join(new_symbols)
                    self._build_ticker_cards(new_symbols)
                    msg = self._describe_shortlist_changes(previous, new_symbols, reasons)
                    title = "Coin Shuffle: New List Selected" if changed else "Coin Shuffle: No Change"
                    messagebox.showinfo(title, msg)
                    self._feed(("Coin Shuffle changed the shortlist.\n" if changed else "Coin Shuffle found no change.\n"), "ok" if changed else "muted")
                    if changed and self.running:
                        self.pending_restart_symbols = new_symbols
                        self._request_stop(for_restart=True)
                elif typ == "shuffle_error":
                    self.shuffle_in_progress = False
                    self.shuffle_btn.configure(state=tk.NORMAL)
                    self._feed(f"Coin Shuffle error: {item['message']}\n", "bad")
                    messagebox.showerror("Coin Shuffle Error", item["message"])
                elif typ == "loop_ended":
                    if self.pending_restart_symbols:
                        symbols_to_start = self.pending_restart_symbols
                        self.pending_restart_symbols = None
                        self._launch_engine(symbols_to_start, auto_symbols=0, reset_signals=False)
                    elif not self.running:
                        self.start_btn.configure(state=tk.NORMAL)
                        self.stop_btn.configure(state=tk.DISABLED)
                elif typ == "error":
                    self._feed(f"ERROR: {item['message']}\n", "bad")
                    messagebox.showerror("INSIGHT Engine Error", item["message"])
                    self.pending_restart_symbols = None
                    self.stop_engine()
        except queue.Empty:
            pass
        self.after(200, self._process_queue)

    def _update_market(self, rows: Dict[str, dict]) -> None:
        self.latest_rows = rows
        ordered = []
        for sym in MAJOR_DEFAULTS:
            if sym in rows:
                ordered.append(sym)
        ordered += [s for s in rows if s not in ordered]
        ordered = ordered[:15]

        existing = set(self.market_tree.get_children(""))
        for sym in ordered:
            row = rows[sym]
            vals = (
                row["symbol"], row["price"], row["move"], row["buybar"], row["buy"],
                row["sellbar"], row["sell"], row["bias"], row["urgency"], row["flow"], row["book"], row["vol"], row["candles"], row["last"],
            )
            tag = self._row_tag(row["bias"])
            if sym in existing:
                self.market_tree.item(sym, values=vals, tags=(tag,))
            else:
                self.market_tree.insert("", tk.END, iid=sym, values=vals, tags=(tag,))
        for iid in existing - set(ordered):
            self.market_tree.delete(iid)

        for sym, labels in self.ticker_labels.items():
            if sym in rows:
                labels["price"].configure(text=rows[sym]["price"])
                labels["sub"].configure(text=f"B {rows[sym]['buy'].strip()} | S {rows[sym]['sell'].strip()} | {rows[sym]['bias']} | {rows[sym]['urgency']}")



    def _toggle_api_demo(self) -> None:
        self.api_demo.config.enabled = bool(self.api_demo_enabled_var.get())
        if self.api_demo.config.enabled:
            self._feed("API Demo ON: local paper entries/exits will be mirrored to Binance Futures Testnet only.\n", "warn")
            if not self.api_demo.has_keys:
                self._feed("API Demo warning: keys are missing. Set BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET before expecting testnet orders.\n", "warn")
        else:
            self._feed("API Demo OFF: bot will remain local paper-only.\n", "muted")
        self._refresh_bot_panel()

    def _test_api_demo_connection(self) -> None:
        self.api_demo.config.enabled = bool(self.api_demo_enabled_var.get())
        result = self.api_demo.test_connection()
        self._feed(result.message + "\n", "ok" if result.ok else "warn")
        if result.ok:
            messagebox.showinfo("API Demo", result.message)
        else:
            messagebox.showwarning("API Demo", result.message)
        self._refresh_bot_panel()

    def _mirror_api_demo_open(self, trade: Optional[VirtualTrade]) -> None:
        if not trade or not self.api_demo_enabled_var.get():
            return
        self.api_demo.config.enabled = True
        result = self.api_demo.open_from_virtual_trade(trade)
        self._feed(result.message + "\n", "ok" if result.ok else "warn")
        self._refresh_bot_panel()

    def _mirror_api_demo_close(self, trade: Optional[VirtualTrade], reason: str) -> None:
        if not trade or not self.api_demo_enabled_var.get():
            return
        self.api_demo.config.enabled = True
        result = self.api_demo.close_virtual_trade(trade, reason=reason)
        self._feed(result.message + "\n", "ok" if result.ok else "warn")
        self._refresh_bot_panel()

    def _paper_bot_scan_snapshots(self, snapshots: Dict[str, ScoreSnapshot]) -> None:
        """Let the paper bot attempt virtual trades from score-board setups.

        v3.4/v3.5/v3.6 only let the paper bot act after a saved alert. In quiet or
        very strict conditions this meant the bot looked ON but never attempted
        anything. v3.7 adds a paper-bot scanner: it still respects the +3 USDT
        net-profit and balanced 3 USDT max-loss rules, but it can test strong WATCH
        setups as paper-only opportunities. Skip reasons are shown in the feed
        so the user can see exactly why a trade did not open.
        """
        if not self.bot_enabled_var.get() or not snapshots:
            return
        now = time.time()
        if now - self.last_bot_scan_ts < max(2, int(self.bot_scan_seconds_var.get())):
            return
        self.last_bot_scan_ts = now

        try:
            threshold = int(self.bot_score_var.get())
        except Exception:
            threshold = 60

        candidates = []
        for sym, snap in snapshots.items():
            if not snap or snap.price is None or not snap.entry_zone or snap.invalidation is None or not snap.targets:
                continue
            side = None
            score = 0
            opposite = 0
            if snap.alert_direction == "long" or (snap.buy_score >= threshold and snap.buy_score >= snap.sell_score + 10 and snap.sell_score < 60):
                side = "long"
                score = snap.buy_score
                opposite = snap.sell_score
            elif snap.alert_direction == "short" or (snap.sell_score >= threshold and snap.sell_score >= snap.buy_score + 10 and snap.buy_score < 60):
                side = "short"
                score = snap.sell_score
                opposite = snap.buy_score
            if not side or score < threshold:
                continue
            candidates.append((score, sym, side, opposite, snap))

        if not candidates:
            return
        candidates.sort(reverse=True, key=lambda x: x[0])

        for score, sym, side, opposite, snap in candidates[:5]:
            if len(self.virtual_bot.open_trades) >= self.virtual_bot.config.max_open_trades:
                return
            key = f"{sym}:{side}"
            # Avoid repeatedly testing the same coin every few seconds.
            if now - self.last_bot_attempt_ts.get(key, 0.0) < 90:
                continue
            self.last_bot_attempt_ts[key] = now

            sig = self._signal_from_snapshot_side(snap, side, score, paper_only=True)
            if not sig:
                continue
            decision = self.virtual_bot.try_open_from_signal(sig, current_price=snap.price)
            if decision.opened:
                try:
                    append_signal(sig)
                except Exception as exc:
                    self._feed(f"Could not save paper-bot signal CSV: {exc}\n", "warn")
                self.saved_signals.insert(0, sig)
                self.signal_count += 1
                stamp = sig.timestamp.strftime("%H:%M")
                line = f"{stamp} {sig.symbol:<9} {sig.direction.upper():<5} {sig.score:>3} paper-bot"
                self.signal_list.insert(0, line)
                self.bell()
                self._feed("\n" + sig.one_line() + "\n", "signal")
                self._feed(decision.message + "\n", "ok")
                self._mirror_api_demo_open(decision.trade)
                self._feed("\n", "ok")
                self._refresh_bot_panel()
            else:
                # Show skip reasons, but throttle per coin/side so the feed stays readable.
                if now - self.last_bot_skip_ts.get(key, 0.0) > 60:
                    self.last_bot_skip_ts[key] = now
                    self._feed(decision.message + f" | score {score}, opposite {opposite}.\n", "warn")

    def _signal_from_snapshot_side(self, snap: ScoreSnapshot, side: str, score: int, paper_only: bool = False) -> Optional[Signal]:
        if side not in {"long", "short"} or not snap.entry_zone or snap.invalidation is None:
            return None
        if score >= 85:
            label = "strong-signal"
        elif score >= 75:
            label = "signal"
        elif paper_only:
            label = "paper-setup"
        else:
            label = "watch"
        # Freeze this snapshot as a signal-like object so saved signal popups and
        # virtual trade records preserve the reasoning at the time of entry.
        return Signal(
            symbol=snap.symbol,
            timestamp=snap.timestamp,
            direction=side,
            score=score,
            confidence_label=label,
            entry_zone=snap.entry_zone,
            invalidation=snap.invalidation,
            targets=snap.targets[:3],
            features=snap.features,
            buy_score=snap.buy_score,
            sell_score=snap.sell_score,
            bias_label=snap.bias_label if not paper_only else f"PAPER BOT {side.upper()} SETUP",
            factor_rows=list(snap.factor_rows),
            price=snap.price,
        )

    def _update_virtual_bot_prices(self, snapshots: Dict[str, ScoreSnapshot]) -> None:
        if not self.bot_enabled_var.get():
            return
        prices = {sym: snap.price for sym, snap in snapshots.items() if snap.price is not None}
        if not prices:
            return
        closed_now = self.virtual_bot.update_prices(prices)
        for trade in closed_now:
            tag = "ok" if trade.net_pnl > 0 else "bad"
            self._feed(
                f"Virtual bot CLOSED #{trade.trade_id} {trade.symbol} {trade.side_label()} | {trade.exit_reason} | "
                f"exit {trade.exit_price:.8g} | net PnL {trade.net_pnl:+.2f} USDT | wallet/equity {self.virtual_bot.equity:.2f}\n",
                tag,
            )
            self._mirror_api_demo_close(trade, trade.exit_reason or "LOCAL CLOSE")

    def _refresh_bot_panel(self) -> None:
        if not hasattr(self, "bot_summary_var"):
            return
        self.bot_summary_var.set((self.virtual_bot.summary_text() if self.bot_enabled_var.get() else "Paper Bot OFF") + "\n" + self.api_demo.summary_text())
        existing = self.bot_tree.get_children("") if hasattr(self, "bot_tree") else []
        for item in existing:
            self.bot_tree.delete(item)
        if not hasattr(self, "bot_tree"):
            return
        for row in self.virtual_bot.recent_rows(limit=8):
            # row = id, coin, side, entry, tp, sl, pnl, status, action
            status = row[-2]
            try:
                pnl = float(row[-3])
            except Exception:
                pnl = 0.0
            tag = "open" if status == "OPEN" else "win" if pnl > 0 else "loss"
            iid = f"trade_{row[0]}"
            self.bot_tree.insert("", tk.END, iid=iid, values=row, tags=(tag,))

    def _on_bot_tree_click(self, event=None) -> None:
        """Close an open paper trade when the user clicks its CLOSE action cell."""
        if not hasattr(self, "bot_tree") or event is None:
            return
        item_id = self.bot_tree.identify_row(event.y)
        col_id = self.bot_tree.identify_column(event.x)
        if not item_id or not col_id:
            return
        columns = list(self.bot_tree["columns"])
        action_col = f"#{len(columns)}"
        if col_id != action_col:
            return

        values = self.bot_tree.item(item_id, "values")
        if not values or len(values) < 9:
            return
        action = values[-1]
        status = values[-2]
        if action != "CLOSE" or status != "OPEN":
            return

        try:
            trade_id = int(values[0])
        except Exception:
            self._feed("Manual close failed: could not read selected trade ID.\n", "warn")
            return

        trade = next((t for t in self.virtual_bot.open_trades if t.trade_id == trade_id), None)
        if trade is None:
            self._feed(f"Manual close skipped: trade #{trade_id} is no longer open.\n", "warn")
            self._refresh_bot_panel()
            return

        snap = self.latest_snapshots.get(trade.symbol)
        live_price = snap.price if snap and snap.price else trade.last_price
        decision = self.virtual_bot.manual_close_trade(trade_id, current_price=live_price)
        self._feed(decision.message + "\n", "ok" if decision.opened else "warn")
        if decision.opened:
            self._mirror_api_demo_close(decision.trade, "MANUAL CLOSE")
        self._refresh_bot_panel()

    def _row_tag(self, bias: str) -> str:
        if "BUY" in bias or "LONG" in bias:
            return "buy"
        if "SELL" in bias or "SHORT" in bias:
            return "sell"
        if "CONFLICT" in bias:
            return "conflict"
        if "WEAK" in bias or "WATCH" in bias:
            return "watch"
        return "neutral"

    def _add_signal(self, sig: Signal) -> None:
        self.saved_signals.insert(0, sig)
        self.signal_count += 1
        stamp = sig.timestamp.strftime("%H:%M")
        line = f"{stamp} {sig.symbol:<9} {sig.direction.upper():<5} {sig.score:>3} {sig.confidence_label}"
        self.signal_list.insert(0, line)
        # Do not auto-open a detail window on signal creation. The trader can click
        # the saved signal later to review the frozen generation-time reasoning.
        self.bell()
        self._feed("\n" + sig.one_line() + "\n", "signal")
        self._feed("Beep alert. Saved to signals/insight_signals.csv. Click the saved signal to review frozen reasoning.\n", "ok")

        if self.bot_enabled_var.get():
            current_price = sig.price
            snap = self.latest_snapshots.get(sig.symbol)
            if snap and snap.price:
                current_price = snap.price
            decision = self.virtual_bot.try_open_from_signal(sig, current_price=current_price)
            self._feed(decision.message + "\n", "ok" if decision.opened else "warn")
            if decision.opened:
                self._mirror_api_demo_open(decision.trade)
            self._feed("\n", "ok" if decision.opened else "warn")
            self._refresh_bot_panel()
        else:
            self._feed("Paper Bot is OFF, so no virtual trade was opened.\n\n", "muted")

    def _on_signal_select(self, _event=None) -> None:
        sel = self.signal_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.saved_signals):
            sig = self.saved_signals[idx]
            self._open_detail_popup(f"Saved Signal Detail - {sig.symbol} {sig.direction.upper()}", self._format_signal_detail(sig))

    def _on_symbol_double_click(self, event=None) -> None:
        # Freeze the latest score snapshot at the exact moment of double-click.
        item_id = self.market_tree.identify_row(event.y) if event is not None else ""
        if not item_id:
            sel = self.market_tree.selection()
            item_id = sel[0] if sel else ""
        if not item_id:
            return
        snap = self.latest_snapshots.get(item_id)
        if not snap:
            self._open_detail_popup("Coin Snapshot", f"{item_id}: waiting for enough candle data.\n")
            return
        self._open_detail_popup(f"Coin Score Snapshot - {snap.symbol}", self._format_symbol_detail(snap))

    def _format_signal_detail(self, sig: Signal) -> str:
        """Return a frozen explanation of a saved alert at generation time."""
        f = sig.features
        buy = sig.buy_score or (sig.score if sig.direction == "long" else 0)
        sell = sig.sell_score or (sig.score if sig.direction == "short" else 0)
        bias = sig.bias_label or sig.direction.upper()
        reasons = "\n".join(f"- {n}" for n in f.notes[:18]) or "- no notes recorded"
        targets = ", ".join(f"{x:.8g}" for x in sig.targets)
        factor_rows = "\n".join(
            f"{name:<22} Buy +{b:<3} Sell +{s:<3} | {reason}"
            for name, b, s, reason in sig.factor_rows
        ) or "No factor rows were saved for this alert."
        return (
            f"SAVED SIGNAL — FROZEN AT GENERATION TIME\n"
            f"{sig.symbol} | {sig.direction.upper()} | Alert score {sig.score} ({sig.confidence_label})\n"
            f"Bias then: {bias}\n"
            f"Buy Score : {score_bar(buy)} {buy}/100\n"
            f"Sell Score: {score_bar(sell)} {sell}/100\n"
            f"Signal candle/time UTC: {sig.timestamp.isoformat()}\n\n"
            f"TRADE PLAN THEN\n"
            f"Entry zone: {sig.entry_zone[0]:.8g} - {sig.entry_zone[1]:.8g}\n"
            f"Invalidation: {sig.invalidation:.8g}\n"
            f"Targets: {targets}\n\n"
            f"SMART MONEY / PRICE ACTION THEN\n"
            f"Market structure: {f.market_structure}\n"
            f"Equal highs: {f.equal_highs} @ {f.equal_high_level if f.equal_high_level is not None else '--'}\n"
            f"Equal lows : {f.equal_lows} @ {f.equal_low_level if f.equal_low_level is not None else '--'}\n"
            f"Bullish sweep: {f.bullish_sweep} | Bearish sweep: {f.bearish_sweep}\n"
            f"Bullish FVG: {f.bullish_fvg} {f.bullish_fvg_zone or '--'}\n"
            f"Bearish FVG: {f.bearish_fvg} {f.bearish_fvg_zone or '--'}\n"
            f"Candlestick trigger: {f.candle_trigger or 'none'}\n"
            f"Take-off map: {f.takeoff_signal or 'none'}\n"
            f"Immediate fall map: {f.fall_signal or 'none'}\n"
            f"Candle urgency: take-off {f.takeoff_score}/100 | fall {f.fall_score}/100\n\n"
            f"FLOW / MARKET CONTEXT THEN\n"
            f"Trade flow delta: {f.buy_sell_delta:+.2f}\n"
            f"Order book imbalance: {f.book_imbalance:+.2f}\n"
            f"Volume impulse: {f.volume_impulse:.2f}x\n"
            f"3-candle momentum: {f.recent_momentum_pct:+.2f}% | Latest close pos: {f.last_close_position:.2f}\n"
            f"BTC alignment: {f.btc_alignment or 'unknown'}\n"
            f"Sessions: {', '.join(f.session_tags) or 'none'}\n\n"
            f"BACKEND SCORE BREAKDOWN THEN\n"
            f"{factor_rows}\n\n"
            f"RECORDED REASONS\n"
            f"{reasons}\n"
        )

    def _format_symbol_detail(self, snap: ScoreSnapshot) -> str:
        """Return a frozen explanation of a coin's latest score snapshot."""
        f = snap.features
        targets = ", ".join(f"{x:.8g}" for x in snap.targets) if snap.targets else "--"
        entry = "--" if not snap.entry_zone else f"{snap.entry_zone[0]:.8g} - {snap.entry_zone[1]:.8g}"
        invalidation = "--" if snap.invalidation is None else f"{snap.invalidation:.8g}"
        fvg_bull = "--" if not f.bullish_fvg_zone else f"{f.bullish_fvg_zone[0]:.8g}-{f.bullish_fvg_zone[1]:.8g}"
        fvg_bear = "--" if not f.bearish_fvg_zone else f"{f.bearish_fvg_zone[0]:.8g}-{f.bearish_fvg_zone[1]:.8g}"
        eh = "--" if f.equal_high_level is None else f"{f.equal_high_level:.8g}"
        el = "--" if f.equal_low_level is None else f"{f.equal_low_level:.8g}"
        rows = "\n".join(
            f"{name:<22} Buy +{b:<3} Sell +{s:<3} | {reason}"
            for name, b, s, reason in snap.factor_rows
        ) or "No active scoring factors yet."
        return (
            f"{snap.symbol} — FROZEN SCORE SNAPSHOT\n"
            f"This explanation is fixed at the exact moment you double-clicked the coin. It will not auto-update.\n\n"
            f"Price: {snap.price if snap.price is not None else '--'} | Bias: {snap.bias_label}\n"
            f"Shortlist reason: {self.shortlist_reasons.get(snap.symbol, 'manual/current list')}\n"
            f"Buy Score : {score_bar(snap.buy_score)} {snap.buy_score}/100\n"
            f"Sell Score: {score_bar(snap.sell_score)} {snap.sell_score}/100\n"
            f"Snapshot candle UTC: {snap.timestamp.isoformat()}\n\n"
            f"TRADE PLAN\n"
            f"Direction candidate: {snap.direction.upper()} | Alert side: {snap.alert_direction or 'none'}\n"
            f"Entry zone: {entry}\n"
            f"Invalidation: {invalidation}\n"
            f"Targets: {targets}\n\n"
            f"SMART MONEY / PRICE ACTION\n"
            f"Market structure: {f.market_structure}\n"
            f"Equal highs: {f.equal_highs} @ {eh}\n"
            f"Equal lows : {f.equal_lows} @ {el}\n"
            f"Bullish sweep: {f.bullish_sweep} | Bearish sweep: {f.bearish_sweep}\n"
            f"Bullish FVG: {f.bullish_fvg} {fvg_bull}\n"
            f"Bearish FVG: {f.bearish_fvg} {fvg_bear}\n"
            f"Candlestick trigger: {f.candle_trigger or 'none'}\n"
            f"Take-off map: {f.takeoff_signal or 'none'}\n"
            f"Immediate fall map: {f.fall_signal or 'none'}\n"
            f"Candle urgency: take-off {f.takeoff_score}/100 | fall {f.fall_score}/100\n\n"
            f"FLOW / MARKET CONTEXT\n"
            f"Trade flow delta: {f.buy_sell_delta:+.2f}\n"
            f"Order book imbalance: {f.book_imbalance:+.2f}\n"
            f"Volume impulse: {f.volume_impulse:.2f}x\n"
            f"3-candle momentum: {f.recent_momentum_pct:+.2f}% | Latest close pos: {f.last_close_position:.2f}\n"
            f"BTC alignment: {f.btc_alignment or 'unknown'}\n"
            f"Sessions: {', '.join(f.session_tags) or 'none'}\n\n"
            f"BACKEND SCORE BREAKDOWN\n"
            f"{rows}\n\n"
            f"Rule: alert only if one side crosses threshold and opposite side stays below conflict level."
        )

    def _open_detail_popup(self, title: str, text: str) -> None:
        popup = tk.Toplevel(self)
        popup.title(title)
        popup.geometry("820x720")
        popup.minsize(650, 500)
        popup.configure(bg="#101418")

        container = ttk.Frame(popup, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text=title, style="Title.TLabel").pack(side=tk.LEFT)

        text_box = tk.Text(
            container,
            bg="#0c1117",
            fg="#d8dee9",
            insertbackground="#ffffff",
            wrap=tk.WORD,
            font=("Consolas", 10),
            relief=tk.FLAT,
        )
        text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=text_box.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        text_box.configure(yscrollcommand=scroll.set)
        text_box.insert(tk.END, text)
        text_box.configure(state=tk.DISABLED)

        # Keep the newest popup visible without blocking the live dashboard.
        popup.transient(self)
        popup.lift()

    def _feed(self, message: str, tag: str = "muted") -> None:
        self.feed_text.insert(tk.END, message, tag)
        line_count = int(self.feed_text.index("end-1c").split(".")[0])
        if line_count > 1200:
            self.feed_text.delete("1.0", "200.0")
        self.feed_text.see(tk.END)

    def _closed_candle_line(self, candle: Candle) -> str:
        direction = "UP" if candle.close >= candle.open else "DOWN"
        return f"{self._clock()} {candle.symbol:<9} closed {candle.interval:<3} {direction:<4} O:{candle.open:.8g} H:{candle.high:.8g} L:{candle.low:.8g} C:{candle.close:.8g}\n"

    def _clock(self) -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    def _tick_clock(self) -> None:
        if self.running:
            elapsed = int(time.time() - self.start_time)
            self.footer_var.set(
                f"Running {elapsed//60:02d}:{elapsed%60:02d} | Saved signals: {self.signal_count} | "
                f"CSV: signals/insight_signals.csv + virtual_trades.csv + api_demo_orders.csv | Double-click coin = frozen explanation | Click saved signal = frozen alert detail"
            )
        self.after(1000, self._tick_clock)


def parse_symbols(raw: str) -> List[str]:
    out: List[str] = []
    for x in raw.split(","):
        s = normalize_symbol(x)
        if s and s not in out:
            out.append(s)
    return out[:15]


def main() -> None:
    parser = argparse.ArgumentParser(description="INSIGHT Trading Engine GUI v3.8.0 - signal + paper bot + API demo mirror + candle map")
    parser.add_argument(
        "--symbols",
        default=",".join(EngineConfig().default_symbols),
        help="Comma-separated symbols. With --auto-symbols > 0, these are treated as manual pins.",
    )
    parser.add_argument("--auto-symbols", type=int, default=15, help="Build a core + dynamic shortlist up to N symbols; top-bar symbols are pinned; max 15")
    parser.add_argument("--interval", default="1m", help="Kline interval, e.g. 1m, 5m, 15m")
    parser.add_argument("--deep-limit", type=int, default=15, help="Symbols with depth stream; max 15")
    parser.add_argument("--min-score", type=int, default=75, help="Minimum score for beep/save alert")
    args = parser.parse_args()

    config = EngineConfig(
        interval=args.interval,
        deep_limit=min(15, args.deep_limit),
        min_signal_score=args.min_score,
    )
    app = InsightDashboard(parse_symbols(args.symbols), config, auto_symbols=min(15, args.auto_symbols))
    app.mainloop()


if __name__ == "__main__":
    main()
