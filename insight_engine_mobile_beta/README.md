# INSIGHT Engine v3.7.3 GUI — Signal + Paper Bot

Lightweight Windows/Tkinter dashboard for live Binance USD-M Futures monitoring with a local virtual paper-trading bot.
## What changed in v3.7.3

- Added a dedicated **Candle Map** layer for fast candle-chart warnings:
  - `TAKEOFF` = ignition candle / breakout / compression release / lower-wick bear trap.
  - `FALL` = bearish ignition / breakdown / compression dump / upper-wick rejection.
- The score board now shows a **Candle Map** column beside Bias.
- Frozen coin/signal details now show take-off score, fall score, and the exact candle reason.
- CSV signal logs now save take-off/fall scores and text.
- This remains a confirmation layer only; the engine still combines candle urgency with flow, book, BTC alignment, volume and risk.


## What changed in v3.7.2

- Paper Bot minimum expected profit is now **above 3 USDT net after fees**, using balanced mode.
- Stop-loss rule is now **3 USDT max net loss** for balanced testing.
- Trade size remains **20 USDT margin x 10x = 200 USDT notional**.
- Default Bot Score remains **55** for active paper testing.
- GUI label now shows: `20 USDT x10 | net TP >3 | SL 3`.
- Bot still only opens a paper trade when the score-board setup is tradable and the TP/auto-TP can clear the net-profit rule after fee burn.
- It remains **fully virtual**: no API keys, no real Binance orders.

## Run

```powershell
cd C:\Coding_Projs
Expand-Archive .\insight_engine_v3_7_2_gui.zip -DestinationPath . -Force
cd .\insight_engine_v3_7_2_gui

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python run_gui.py --auto-symbols 15 --interval 1m --deep-limit 15 --min-score 70
```

## Suggested testing settings

```text
Interval: 1m
Alert Score: 70
Bot Score: 55
Paper Bot: ON
Profit rule: net TP > 3 USDT
Stop-loss rule: max net loss 3 USDT
```

## Safety

This app is paper-trading only. It does not place real orders.


## v3.7.1 layout fix retained
- Start, Stop and Coin Shuffle buttons are now on the first control row so they stay visible on laptop screens.
- Other settings moved to the second row.


## What changed in v3.7.2

- Balanced paper-bot rule: **net TP > 3 USDT** and **SL 3 USDT**.
- Added an **ACTION / CLOSE** cell beside each open paper trade.
- Click **CLOSE** to manually close that open paper trade at the latest available live price.
- Manual close is still fully virtual/paper-only and is recorded in `signals/virtual_trades.csv` as `MANUAL CLOSE`.
## What changed in v3.8.0 — API-connected demo mode

- Added **API Demo** mode beside the existing local Paper Bot.
- API Demo mirrors local paper-bot entries and exits to **Binance USD-M Futures Testnet only**.
- The local INSIGHT paper bot remains the master risk controller; API Demo is only a testnet execution bridge.
- Added **Test API** button in the GUI.
- Added `signals/api_demo_orders.csv` log for testnet order attempts.
- API Demo is OFF by default and requires environment variables. Never put real keys inside source code.

### API Demo setup on Windows PowerShell

```powershell
$env:BINANCE_DEMO_API_KEY="your_testnet_key_here"
$env:BINANCE_DEMO_API_SECRET="your_testnet_secret_here"
python run_gui.py --auto-symbols 15 --interval 1m --deep-limit 15 --min-score 70
```

Inside the GUI:

1. Keep **Paper Bot** ON.
2. Turn **API Demo** ON.
3. Press **Test API**.
4. Let local paper signals open first; successful opens are mirrored to Binance Futures Testnet.
5. Manual CLOSE or local TP/SL close will also send a reduce-only testnet close order.

Safety rule: use Binance Futures **Testnet / Demo API keys only**. Do not use live Binance keys for this prototype.
