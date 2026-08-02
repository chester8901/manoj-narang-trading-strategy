# Manoj Narang 5/10 Mean-Reversion Strategy

Automated weekly quantitative trading system implementing Manoj Narang's **5/10 Mean-Reversion Strategy** with Python, Alpaca Paper Trading API, GitHub Actions, and Telegram reports.

## 📊 Strategy Rules

1. **Universe & Frequency:** Scans broad stock liquid universe (S&P 500) weekly on Friday before market close (19:50 UTC / 3:50 PM ET).
2. **Weekly Position Liquidation:** Clears existing open positions before issuing new signals to maintain a 1-week holding window.
3. **Long Triggers:** Equities dropping $\ge 5\%$ over the trailing 5 trading days trigger a Buy signal ($5 base allocation, scaled dynamically with drop severity).
4. **Short Triggers:** Equities rising $\ge 10\%$ over the trailing 5 trading days trigger a Short signal ($10 base allocation, scaled dynamically with rise severity). Uses integer share quantities (`qty`) for API short compliance.
5. **Reporting & Persistence:** Dispatches structured Markdown performance metrics to Telegram and updates `trade_history.json` automatically in GitHub repository.

## ⚙️ Setup Instructions

### 1. Telegram Credentials
- Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and save your **Bot Token**.
- Obtain your **Chat ID** from [@userinfobot](https://t.me/userinfobot).

### 2. Alpaca Paper Trading Credentials (Optional)
- Create a paper trading account on [Alpaca Markets](https://alpaca.markets).
- Generate API Key ID and Secret Key from the Paper Trading Dashboard.

### 3. GitHub Secrets Configuration
In your GitHub repository, navigate to **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** and add:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `ALPACA_API_KEY` (Optional)
- `ALPACA_SECRET_KEY` (Optional)

## 🚀 Running Locally

```bash
pip install -r requirements.txt
python strategy.py
```
