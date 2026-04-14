# CryptoSignal Pro 🤖

Multi-source AI crypto scanner → Telegram alerts + live dashboard.

**Stack:** Nansen · Arkham · LunarCrush · Santiment · CoinGecko

---

## Deploy Free on Railway (recommended)

Railway gives $5 free credit/month. This app uses ~$2–3/month.

### Step 1 — Push to GitHub

```bash
cd ~/arkhsen
git init
git add .
git commit -m "Initial commit"
```

Create a new repo at github.com (click + → New repository)
Name it: `cryptosignal-pro` — set to **Private**

```bash
git remote add origin https://github.com/YOUR_USERNAME/cryptosignal-pro.git
git push -u origin main
```

### Step 2 — Deploy on Railway

1. Go to **railway.app** → Sign up free with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select `cryptosignal-pro`
4. Railway detects `railway.toml` and deploys automatically

### Step 3 — Add your API keys in Railway

In your Railway project → **Variables** tab → Add each:

```
NANSEN_API_KEY        = your_key
TELEGRAM_BOT_TOKEN    = your_token
TELEGRAM_CHAT_ID      = your_chat_id
LUNARCRUSH_API_KEY    = your_key
SANTIMENT_API_KEY     = your_key
```

Railway injects these as environment variables — your `.env` file is never needed on the server.

### Step 4 — Get your dashboard URL

Railway → your service → **Settings** → **Networking** → **Generate Domain**

Your dashboard will be at:
```
https://your-app-name.up.railway.app/templates/dashboard.html
```

That's a public URL you can open on your phone, tablet, anywhere.

---

## Local Development (Mac)

```bash
# Install dependencies
pip3 install -r requirements.txt

# Run everything (scanner + dashboard server)
python3 main.py

# Dashboard opens at:
# http://localhost:8888/templates/dashboard.html
```

---

## Project Structure

```
arkhsen/
  main.py           ← entry point (scanner + web server)
  scanner_v2.py     ← signal logic (6 AI layers)
  serve.py          ← standalone local server (optional)
  requirements.txt  ← Python dependencies
  railway.toml      ← Railway deployment config
  .gitignore        ← keeps .env and secrets out of git
  templates/
    dashboard.html  ← live trading dashboard
    signals.json    ← written by scanner, read by dashboard
```

---

## Signal Layers

| # | Source | What it detects | Points |
|---|--------|-----------------|--------|
| 1 | Nansen Smart Money Screener | Institutional wallet buying | 30 |
| 2 | Nansen Smart Money Netflow | Net inflow/outflow direction | 20 |
| 3 | Arkham Intelligence | Entity verification / fraud detection | 20 |
| 4 | LunarCrush | Galaxy Score, AltRank, social hype | 15 |
| 5 | Santiment | Professional trader social spike | 10 |
| 6 | CoinGecko | Volume anomaly confirmation | 5 |

Signal fires when score ≥ 65/100 → Telegram alert sent instantly.

---

## ⚠️ Disclaimer

AI signals only. Always DYOR. Never risk more than you can afford to lose.
