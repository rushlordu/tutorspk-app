# INSIGHT Engine — Mobile Tiered Local Version

This is the local beta version for the paid mobile signal app.

## What changed

- Mobile-first signal cards instead of a cluttered table.
- Basic tier: 10 USDT/month, top 5 active coins.
- Pro tier: 15 USDT/month, top 10 active coins.
- Entry / SL / TP1 / TP2 / TP3 appears only when the setup is 70+ score and direction is clear.
- Sign In / Sign Up page before the engine.
- Users sign up with name, email, PIN, plan, network, and TX hash.
- Users email payment screenshots/proof instead of uploading media to Render.
- Admin dashboard at `/admin` for approval, rejection, reset device, reset session, extend, disable.
- One account locks to one device/browser on first successful login.
- One active session/tab at a time.
- Text-only support chat.
- Optional Telegram notification if bot token and chat ID are set in environment variables.

## Local test commands

Open PowerShell in this folder:

```powershell
cd "C:\Users\USER\OneDrive\FerFar\codes\tutorpk_app\insight_engine_web_ready"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-web.txt
```

Run locally:

```powershell
$env:INSIGHT_PROVIDER="bybit"
$env:INSIGHT_ADMIN_CODE="change-this-admin-code"
$env:INSIGHT_PAYMENT_WALLET="YOUR_PUBLIC_USDT_ADDRESS"
$env:INSIGHT_PAYMENT_NETWORK="TRC20"
$env:INSIGHT_PAYMENT_EMAIL="insight@tutorsonline.pk"
$env:INSIGHT_AUTOSTART="1"
$env:INSIGHT_AUTO_SYMBOLS="15"
$env:INSIGHT_MIN_SCORE="70"
python -m uvicorn run_web:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Admin panel:

```text
http://127.0.0.1:8000/admin
```

## Optional Telegram alerts

Only set these as environment variables. Do not put them in GitHub.

```powershell
$env:INSIGHT_TELEGRAM_BOT_TOKEN="your_bot_token"
$env:INSIGHT_TELEGRAM_CHAT_ID="your_chat_id"
```

## Notes

- Local data is stored in `insight_auth_local.sqlite3` in the app folder.
- Delete that file if you want a fresh local test database.
- For Render paid users, use persistent storage/database later. Render free local files are not reliable for paid user records.
- Never store wallet seed phrase, private key, exchange password, or exchange API keys in the app or GitHub.
