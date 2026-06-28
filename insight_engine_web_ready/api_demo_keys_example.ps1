# Binance Futures Testnet keys only. Do NOT use live exchange keys here.
$env:BINANCE_DEMO_API_KEY="paste_your_testnet_api_key_here"
$env:BINANCE_DEMO_API_SECRET="paste_your_testnet_api_secret_here"
python run_gui.py --auto-symbols 15 --interval 1m --deep-limit 15 --min-score 70
