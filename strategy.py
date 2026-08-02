import os
import io
import json
import math
import warnings
import requests
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# =========================================================
# CONFIGURATION & PARAMETERS
# =========================================================
LONG_THRESHOLD = float(os.getenv("LONG_THRESHOLD", -0.05))   # Drop trigger (-5%)
SHORT_THRESHOLD = float(os.getenv("SHORT_THRESHOLD", 0.10))   # Rise trigger (+10%)
LONG_BASE_USD = float(os.getenv("LONG_BASE_USD", 5.0))       # Long alloc ($5)
SHORT_BASE_USD = float(os.getenv("SHORT_BASE_USD", 10.0))    # Short alloc ($10)
DYNAMIC_SIZING = os.getenv("DYNAMIC_SIZING", "True").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
HISTORY_FILE = "trade_history.json"

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Telegram credentials missing. Console output:\n", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Telegram notification failed: {e}")

def close_all_alpaca_positions():
    """Liquidates previous week paper positions prior to new signals."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return
    url = f"{ALPACA_BASE_URL}/v2/positions"
    headers = {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}
    try:
        res = requests.delete(url, headers=headers, timeout=10)
        print(f"[ALPACA] Cleared previous positions: HTTP {res.status_code}")
    except Exception as e:
        print(f"[ALPACA ERROR] Could not clear positions: {e}")

def execute_alpaca_order(symbol: str, notional_usd: float, side: str, price: float):
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return {"status": "simulated"}

    endpoint = f"{ALPACA_BASE_URL}/v2/orders"
    headers = {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}
    
    # Alpaca allows 'notional' for buys, but requires integer 'qty' for short sells
    if side == "buy":
        payload = {
            "symbol": symbol,
            "notional": round(notional_usd, 2),
            "side": "buy",
            "type": "market",
            "time_in_force": "day"
        }
    else:
        qty = max(1, math.floor(notional_usd / price))
        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": "sell",
            "type": "market",
            "time_in_force": "day"
        }

    try:
        res = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            return {"status": "executed"}
        else:
            return {"status": "rejected", "error": res.text}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def get_universe_tickers():
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        table = pd.read_html(io.StringIO(resp.text))
        return table[0]['Symbol'].str.replace('.', '-', regex=False).tolist()
    except Exception as e:
        print(f"[WARNING] Wikipedia ticker scrape failed ({e}). Using core universe.")
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD", "NFLX", "INTC"]

def run_strategy():
    print("Initiating strategy run...")
    # Step 1: Liquidate open paper trading positions
    close_all_alpaca_positions()
    
    # Step 2: Fetch market data
    tickers = get_universe_tickers()
    print(f"Fetched {len(tickers)} tickers. Downloading price data in batches...")

    chunk_size = 100
    df_list = []
    total_batches = (len(tickers) + chunk_size - 1) // chunk_size

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        batch_num = (i // chunk_size) + 1
        print(f"Downloading batch {batch_num}/{total_batches} ({len(chunk)} tickers)...")
        try:
            res = yf.download(chunk, period="10d", interval="1d", progress=False, threads=False)
            if not res.empty and 'Close' in res:
                df_list.append(res['Close'])
        except Exception as e:
            print(f"[WARNING] Batch {batch_num} failed: {e}")

    if not df_list:
        send_telegram("❌ *Strategy Alert:* Insufficient market data retrieved.")
        return

    data = pd.concat(df_list, axis=1)

    if data.empty or len(data) < 6:
        send_telegram("❌ *Strategy Alert:* Insufficient market data retrieved.")
        return

    latest_date = data.index[-1].strftime('%Y-%m-%d')
    five_day_returns = (data.iloc[-1] - data.iloc[-6]) / data.iloc[-6]
    
    long_signals = []
    short_signals = []

    for ticker, ret in five_day_returns.items():
        if pd.isna(ret):
            continue

        price = round(float(data[ticker].iloc[-1]), 2)
        if price <= 0:
            continue

        # Buy Signal (Down >= LONG_THRESHOLD)
        if ret <= LONG_THRESHOLD:
            alloc = abs(ret / LONG_THRESHOLD) * LONG_BASE_USD if (DYNAMIC_SIZING and LONG_THRESHOLD != 0) else LONG_BASE_USD
            alloc = round(max(alloc, 1.0), 2)
            paper_res = execute_alpaca_order(symbol=ticker, notional_usd=alloc, side="buy", price=price)
            
            long_signals.append({
                "ticker": ticker,
                "return_5d": round(float(ret) * 100, 2),
                "allocation": alloc,
                "price": price,
                "status": paper_res["status"]
            })

        # Short Signal (Up >= SHORT_THRESHOLD)
        elif ret >= SHORT_THRESHOLD:
            alloc = (ret / SHORT_THRESHOLD) * SHORT_BASE_USD if (DYNAMIC_SIZING and SHORT_THRESHOLD != 0) else SHORT_BASE_USD
            alloc = round(max(alloc, 1.0), 2)
            paper_res = execute_alpaca_order(symbol=ticker, notional_usd=alloc, side="sell", price=price)

            short_signals.append({
                "ticker": ticker,
                "return_5d": round(float(ret) * 100, 2),
                "allocation": alloc,
                "price": price,
                "status": paper_res["status"]
            })

    # Step 3: Performance Tracking & Historical Metrics
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        except Exception:
            history = []

    last_week_pnl = 0.0
    evaluated_trades = 0
    winning_trades = 0

    if history:
        last_run = history[-1]
        for trade in last_run.get("long_signals", []):
            t = trade["ticker"]
            if t in data.columns and not pd.isna(data[t].iloc[-1]) and not pd.isna(data[t].iloc[-6]):
                entry_p, exit_p = trade["price"], float(data[t].iloc[-1])
                trade_pnl = trade["allocation"] * ((exit_p - entry_p) / entry_p)
                last_week_pnl += trade_pnl
                evaluated_trades += 1
                if trade_pnl > 0: winning_trades += 1

        for trade in last_run.get("short_signals", []):
            t = trade["ticker"]
            if t in data.columns and not pd.isna(data[t].iloc[-1]) and not pd.isna(data[t].iloc[-6]):
                entry_p, exit_p = trade["price"], float(data[t].iloc[-1])
                trade_pnl = trade["allocation"] * ((entry_p - exit_p) / entry_p)
                last_week_pnl += trade_pnl
                evaluated_trades += 1
                if trade_pnl > 0: winning_trades += 1

    current_run = {
        "date": latest_date,
        "universe_size": len(tickers),
        "long_count": len(long_signals),
        "short_count": len(short_signals),
        "long_signals": long_signals,
        "short_signals": short_signals,
        "evaluated_trades": evaluated_trades,
        "winning_trades": winning_trades,
        "last_week_pnl": round(last_week_pnl, 2)
    }
    history.append(current_run)

    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=4)

    # Statistical Calculations
    tot_eval_trades = sum(h.get("evaluated_trades", 0) for h in history)
    tot_win_trades = sum(h.get("winning_trades", 0) for h in history)
    indiv_win_rate = (tot_win_trades / tot_eval_trades * 100) if tot_eval_trades > 0 else 0.0
    
    weeks_with_pnl = [h["last_week_pnl"] for h in history if h.get("evaluated_trades", 0) > 0]
    winning_weeks = sum(1 for pnl in weeks_with_pnl if pnl > 0)
    weekly_portfolio_win_rate = (winning_weeks / len(weeks_with_pnl) * 100) if weeks_with_pnl else 0.0
    
    cum_pnl = sum(h.get("last_week_pnl", 0.0) for h in history)
    avg_freq = np.mean([h["long_count"] + h["short_count"] for h in history])

    # Step 4: Dispatch Telegram Report
    msg = f"📈 *MANOJ NARANG 5/10 QUANT REPORT* ({latest_date})\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚙️ *Strategy Parameters*\n"
    msg += f"• Long Threshold: `{LONG_THRESHOLD*100:.1f}%` | Short Threshold: `+{SHORT_THRESHOLD*100:.1f}%`\n"
    msg += f"• Dynamic Sizing: `{DYNAMIC_SIZING}`\n"
    msg += f"• Paper Trading: `{'ALPACA ACTIVE' if ALPACA_API_KEY else 'SIMULATION'}`\n\n"

    msg += f"📊 *Weekly Signal Summary*\n"
    msg += f"• Universe Size: `{len(tickers)} stocks`\n"
    msg += f"• Long Signals: `{len(long_signals)}` | Short Signals: `{len(short_signals)}`\n"
    tot_alloc = sum(s['allocation'] for s in long_signals) + sum(s['allocation'] for s in short_signals)
    msg += f"• Capital Deployed: `${tot_alloc:.2f}`\n\n"

    msg += f"🎯 *Performance & Statistical Win Rates*\n"
    msg += f"• Last Week PnL: `${last_week_pnl:+.2f}`\n"
    msg += f"• Cumulative PnL: `${cum_pnl:+.2f}`\n"
    msg += f"• *Individual Trade Win Rate:* `{indiv_win_rate:.1f}%` ({tot_win_trades}/{tot_eval_trades})\n"
    msg += f"• *Weekly Portfolio Win Rate:* `{weekly_portfolio_win_rate:.1f}%` ({winning_weeks}/{len(weeks_with_pnl)} weeks)\n"
    msg += f"• Avg Signal Frequency: `{avg_freq:.1f} trades/week`\n\n"

    if long_signals:
        msg += f"🟢 *Top Buy Targets (Sample)*\n"
        for s in sorted(long_signals, key=lambda x: x['return_5d'])[:4]:
            msg += f"• `{s['ticker']}`: Drop `{s['return_5d']}%` → Alloc `${s['allocation']}`\n"
        msg += "\n"

    if short_signals:
        msg += f"🔴 *Top Short Targets (Sample)*\n"
        for s in sorted(short_signals, key=lambda x: x['return_5d'], reverse=True)[:4]:
            msg += f"• `{s['ticker']}`: Gain `+{s['return_5d']}%` → Alloc `${s['allocation']}`\n"

    send_telegram(msg)

if __name__ == "__main__":
    run_strategy()
