# CryptoSignal Pro — Operations & Troubleshooting Guide
*Last updated: April 19, 2026*

---

## QUICK HEALTH CHECK — Run This Every Morning

```bash
cd ~/arkhsen
python3 scan_once.py 2>&1 | tail -10
```

Healthy output:
```
Nansen screener: 30 SM tokens (key #1/9)
CryptoCompare: 100 coins loaded
Dynamic threshold: 40/100 (Fear&Greed=26)
Nansen LONG: 30 tokens | SHORT: 20 tokens
No signals this scan. Best: 0/100 (credits used: 5-30)
```

---

## PROBLEM 1 — Nansen HTTP 403 (Key Exhausted)

Symptom: `Nansen screener HTTP 403` / `Nansen LONG: 0 tokens`

The first key in .env ran out of credits. Rotate it:

```bash
python3 << 'EOF'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.home() / 'arkhsen' / '.env')
keys = os.getenv('NANSEN_API_KEYS','').split(',')
keys = [k.strip() for k in keys if k.strip()]
rotated = keys[1:] + [keys[0]]
content = open(Path.home() / 'arkhsen' / '.env').read()
content = content.replace(
    f"NANSEN_API_KEYS={','.join(keys)}",
    f"NANSEN_API_KEYS={','.join(rotated)}"
)
open(Path.home() / 'arkhsen' / '.env', 'w').write(content)
print(f"Done. New key: {rotated[0][:8]}...")
EOF
```

Then update GitHub Secret too:
```bash
grep "NANSEN_API_KEYS" ~/arkhsen/.env
```
Copy value → GitHub → Settings → Secrets → NANSEN_API_KEYS → Update

---

## PROBLEM 2 — No Signals (Is It Normal?)

Symptom: `No signals this scan. Best: 0/100`

NORMAL when: entire market falling, Fear&Greed below 20, weekend

NOT NORMAL when: dashboard shows scan older than 30 min, credits = 0

Check Fear & Greed:
```bash
curl -s https://api.alternative.me/fng/ | python3 -c "
import json,sys
d=json.load(sys.stdin)
v=d['data'][0]
fg=int(v['value'])
t=35 if fg<25 else 40 if fg<45 else 50 if fg<55 else 55 if fg<75 else 60
print(f'Fear and Greed: {fg} — {v[\"value_classification\"]}')
print(f'Current threshold: {t}/100')
"
```

Test with lower threshold to see what scores exist:
```bash
sed -i '' 's/SIGNAL_THRESHOLD    = 38/SIGNAL_THRESHOLD    = 25/' ~/arkhsen/scanner_v2.py
python3 scan_once.py 2>&1 | tail -8
sed -i '' 's/SIGNAL_THRESHOLD    = 25/SIGNAL_THRESHOLD    = 38/' ~/arkhsen/scanner_v2.py
```

---

## PROBLEM 3 — GitHub Actions Failed / Dashboard Stale

Dashboard LAST SCAN more than 30 minutes ago means Actions failed.

Go to: https://github.com/adekolagh/cryptosignal-pro/actions

Run manually: Click "CryptoSignal Scanner" → "Run workflow" → Run workflow

If it fails with 403 — update NANSEN_API_KEYS secret with current .env value

---

## PROBLEM 4 — Telegram Not Sending

Test connection:
```bash
python3 << 'EOF'
import asyncio, aiohttp, os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path.home() / 'arkhsen' / '.env')
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT  = os.getenv('TELEGRAM_CHAT_ID')
async def test():
    async with aiohttp.ClientSession() as s:
        r = await s.post(
            f'https://api.telegram.org/bot{TOKEN}/sendMessage',
            json={'chat_id': CHAT, 'text': 'CryptoSignal Pro — Telegram test OK'}
        )
        d = await r.json()
        print('OK' if d.get('ok') else f'ERROR: {d}')
asyncio.run(test())
EOF
```

Check .env values:
```bash
grep "TELEGRAM" ~/arkhsen/.env
```

---

## PROBLEM 5 — signals.json Conflict on Push

```bash
git checkout --theirs templates/signals.json
git add templates/signals.json
git commit -m "resolve signals.json"
git push https://YOUR_TOKEN@github.com/adekolagh/cryptosignal-pro.git main
```

---

## DAILY ROUTINE — 3 Commands Every Morning

```bash
# 1. Health check
python3 scan_once.py 2>&1 | tail -8

# 2. Fear and Greed
curl -s https://api.alternative.me/fng/ | python3 -c "
import json,sys; d=json.load(sys.stdin); v=d['data'][0]
print(f'F&G: {v[\"value\"]} {v[\"value_classification\"]}')"

# 3. Git status
git log --oneline -3
```

---

## NANSEN CREDITS — Status & Duration

9 keys x ~1000 credits = ~9000 total
At 30 credits per scan x 96 scans per day = 2880 per day
Total duration = ~3 days before rotation needed

Check active key:
```bash
python3 -c "
import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path.home() / 'arkhsen' / '.env')
keys = os.getenv('NANSEN_API_KEYS','').split(',')
print(f'Active: {keys[0][:8]}... | Total: {len(keys)} keys')
"
```

---

## KEY NUMBERS

| Item | Value |
|------|-------|
| Dashboard | https://adekolagh.github.io/cryptosignal-pro/templates/dashboard.html |
| Scan every | 15 minutes |
| Threshold now | 40 (Fear&Greed=26) |
| Threshold neutral | 50 (Fear&Greed=50) |
| Threshold bull | 60 (Fear&Greed=75+) |
| Min flow | 5% of liquidity |
| Chains | ethereum, solana, base, arbitrum, bnb, polygon, optimism, avalanche |

---

## TRADING RULES — NEVER BREAK

```
1. Always set stop loss before entering — no exceptions
2. Score below 50 = watch only, never trade large
3. Liquidity below $1M = 25% position size maximum
4. Thin or new token = trailing stop not fixed stop
5. Contract address = identity, never trade by name alone
6. Price already moved more than 8% = reduce size by 50%
7. Confirm with Wilder SAR and ADX before entering
8. One bad trade never justifies a revenge trade
```

---

## SIGNAL QUALITY GUIDE

| Score | Action | Size |
|-------|--------|------|
| 75-100 | Trade with conviction | Full size |
| 60-74 | Trade with confidence | 75% size |
| 50-59 | Trade carefully | 50% size |
| 40-49 | Watch only | 0% |
| Below 40 | Ignore | 0% |

---

## WHAT THE SCANNER CATCHES vs MISSES

Catches:
- SM accumulation 1-3 days before pump
- SM distribution before dump
- Post-pump selling into strength
- Flow above 5% of liquidity with price confirmation

Cannot catch:
- Protocol exploits (like NEAR Rhea Finance $18M hack)
- Exchange hacks and regulatory news
- Micro-cap pump and dump coordination
- Chains without Nansen SM coverage
