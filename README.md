# ANDX Trading Bot

A crypto trading bot for **ANDX Global** with a local dashboard. It trades long
and short on ANDX derivatives, runs four research-backed strategies, and starts
in a safe practice mode so you can learn before risking anything.

---

## ✅ No security warning (recommended)

**Mac** — open **Terminal** (⌘+Space, type **Terminal**, Enter), paste this, press Enter:
```
curl -fsSL https://raw.githubusercontent.com/andxtrading/andx-trading-bot/main/install.sh | bash
```

**Windows** — open **PowerShell** (Start → type **PowerShell** → Enter), paste this, press Enter:
```
irm https://raw.githubusercontent.com/andxtrading/andx-trading-bot/main/install.ps1 | iex
```

Both download, set up, and open the bot — **with no security prompt** (Terminal/PowerShell downloads aren't flagged the way browser downloads are). Files land in your home folder under `andx-trading-bot`; to start it again later, paste the same line. If Python isn't installed, it tells you where to get it. *(Windows: use PowerShell, not Command Prompt.)*

---

## ⬇️ Or download & double-click

**[➡️ Click here to download the bot (.zip)](https://github.com/andxtrading/andx-trading-bot/archive/refs/heads/main.zip)**

Then:

1. **Unzip it** — double-click the downloaded file to get the `andx-trading-bot-main` folder.
2. **Start it (one-time security step — every computer does this):**
   - **Mac:** **right-click** `Start Bot.command` → **Open** → **Open**. If you instead see *"Apple could not verify…"* with only **Done** / **Move to Trash**, click **Done**, then go to ** menu → System Settings → Privacy & Security**, scroll down, and click **"Open Anyway."** One time only.
   - **Windows:** double-click `Start Bot (Windows).bat`. If a blue "Windows protected your PC" box appears, click **More info → Run anyway**. If it says Python is missing, install it from [python.org](https://www.python.org/downloads/) (tick **"Add Python to PATH"**) and run it again.
   - First launch sets itself up automatically — give it about a minute.
3. **Your browser opens the dashboard** at `http://127.0.0.1:8300`. **That page is the bot.** Press **Start bot**.

It opens in **paper mode** — simulated money, real market prices, no account or keys needed. Trade, learn, and edit the code with zero risk.

---

## Going live with your own account

When you're ready to trade real money:

1. Create API keys in **your own ANDX account** with **trade permission only — never withdrawals**. Use *your* keys, never someone else's.
2. On the dashboard, open **Settings → Exchange & API keys**, paste your key + secret, and hit **Save & test keys** (it checks them against your account).
3. Switch **Trading mode** to **Live** and press Start. You'll be asked to confirm.

Your keys are stored **only on your own computer** (`secrets.json`, which is git-ignored and never uploaded) and are sent nowhere except ANDX.

---

## The dashboard

- **⚡ Derivatives** — trade long *and* short with leverage (stops enforced by the exchange). Off = spot only (buy / sell to USDT).
- **🛡️ Conservative** — patient, profit-focused mode: fewer high-conviction trades, smaller risk, wider stops, daily-loss circuit breaker.
- **Start / Stop / Stop & close all** — run control; "close all" flattens every position at market.
- **🌓** — light / dark theme.
- Tabs: **Overview** (equity, signals, positions), **Activity** (trade history, log), **Settings** (keys, config, risk).

## Strategies

- **Auto (Regime Switch)** — default; trend-follows in trends, mean-reverts in ranges (ADX decides).
- **EMA Trend-Following**, **RSI Mean-Reversion**, **Donchian Breakout** — force one style.

Risk controls: volatility-based position sizing, ATR stops, leverage/position caps, and a daily-loss kill switch. **ATR stop** = the auto exit price, set a few normal price-wiggles away from entry.

## Competition — build your own edge

Everyone starts from the **same** tuned bot. You compete by **editing the strategy** — the more original and effective your code, the better you do. Two ways to edit:

**Easiest — right in the dashboard.** Open the **Edit Strategy** tab. It shows the bot's brain (`strategies.py`), where the buy/sell decisions live. Change the logic, click **Save & Apply**, and your bot immediately runs your code. It's checked for errors first — **broken code is refused, so you can't brick your bot** — and **Restore original** puts it back anytime. For example, in the `EMATrend` strategy:
```python
fast = self.p("fast", 21)   # faster/slower moving averages
slow = self.p("slow", 55)
```
Test in **paper mode** so mistakes cost practice money, not real money.

**Full control — edit the files.** This repo is a GitHub **template**: click **"Use this template"** for your own copy, edit `strategies.py` (signals) and `risk.py` (sizing/stops) in any editor, and commit. That copy is your competition entry.

Put your name or student number in **Settings → Your name / Student ID** to tag your entry.

## Disclaimer

Crypto trading — especially leveraged and short — can lose money quickly. Past
performance doesn't predict future results; no strategy wins in all markets.
This software is provided as-is, with no warranty. You are responsible for any
trades made with your keys. Start in paper mode. Start small.
