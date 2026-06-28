# INSIGHT Engine Web Deployment

This folder contains the original INSIGHT Engine plus a browser dashboard wrapper.

## Important

The original `run_gui.py` is a Windows/Tkinter desktop app. A normal website cannot display Tkinter.
Use `run_web.py` for a web link.

## Local test

```powershell
cd insight_engine_web_ready
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-web.txt
uvicorn run_web:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Render deployment

Push this folder to a GitHub repository, then create a Render Web Service.

Build command:

```bash
pip install -r requirements-web.txt
```

Start command:

```bash
uvicorn run_web:app --host 0.0.0.0 --port $PORT
```

Environment variables you may set:

```text
INSIGHT_AUTOSTART=1
INSIGHT_AUTO_SYMBOLS=15
INSIGHT_INTERVAL=1m
INSIGHT_DEEP_LIMIT=15
INSIGHT_MIN_SCORE=70
INSIGHT_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT
```

## Link from Tutors PK website

Add a normal button/link in WordPress/cPanel:

```html
<a href="https://YOUR-DEPLOYED-APP-LINK" target="_blank" rel="noopener">Open INSIGHT Dashboard</a>
```

Or create a subdomain such as:

```text
insight.tutorspk.com
```

and point it to the deployed app.

## Security

Do not put real Binance API keys in this online public app.
Keep the dashboard password-protected if you add account execution later.
This version uses Binance public market data and does not place real orders.
