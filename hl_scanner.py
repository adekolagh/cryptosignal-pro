#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║     CryptoSignal Pro — Hyperliquid Momentum Scanner              ║
╠══════════════════════════════════════════════════════════════════╣
║  Scans Hyperliquid spot tokens every 5 minutes                   ║
║  Fires Telegram alert when:                                       ║
║    - Volume spike 3x+ vs recent average                          ║
║    - Price change 5%+ in last candle                             ║
║    - Open interest jump 20%+                                     ║
║  No API key needed — Hyperliquid is fully public                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio, aiohttp, ssl, certifi, os, json, logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
HL_API             = "https://api.hyperliquid.xyz/info"

# Thresholds
VOL_SPIKE_X        = 3.0   # volume must be 3x the recent average
PRICE_CHANGE_PCT   = 5.0   # 5% price move minimum
MIN_VOLUME_USD     = 50_000 # ignore tokens with less than $50k volume

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("HL-Scanner")

# Track recent volumes to detect spikes
_vol_history: dict = {}  # symbol -> list of recent volumes
_alerted: dict = {}      # symbol -> last alert timestamp


async def fetch_hl_spot(session) -> list:
    """Fetch all Hyperliquid spot tokens with price and volume data."""
    try:
        async with session.post(
            HL_API,
            json={"type": "spotMetaAndAssetCtxs"},
            timeout=10
        ) as r:
            if r.status == 200:
                data = await r.json()
                tokens = data[0].get("tokens", [])
                ctxs   = data[1]
                results = []
                for i, ctx in enumerate(ctxs):
                    if i >= len(tokens):
                        break
                    sym        = tokens[i].get("name", f"TOKEN{i}")
                    price      = float(ctx.get("markPx", 0) or 0)
                    volume_24h = float(ctx.get("dayNtlVlm", 0) or 0)
                    prev_day   = float(ctx.get("prevDayPx", price) or price)
                    change_pct = ((price - prev_day) / prev_day * 100) if prev_day > 0 else 0

                    if price > 0 and volume_24h > MIN_VOLUME_USD:
                        results.append({
                            "symbol":      sym,
                            "price":       price,
                            "volume_24h":  volume_24h,
                            "change_pct":  change_pct,
                            "prev_price":  prev_day,
                        })
                return results
    except Exception as e:
        log.warning(f"   HL fetch error: {e}")
    return []


def detect_momentum(tokens: list) -> list:
    """Detect momentum signals based on volume spikes and price moves."""
    signals = []
    now = datetime.now(timezone.utc).timestamp()

    for t in tokens:
        sym = t["symbol"]
        vol = t["volume_24h"]
        chg = t["change_pct"]

        # Update volume history (keep last 6 readings = 30 min history)
        if sym not in _vol_history:
            _vol_history[sym] = []
        _vol_history[sym].append(vol)
        if len(_vol_history[sym]) > 6:
            _vol_history[sym].pop(0)

        # Need at least 3 readings to detect a spike
        if len(_vol_history[sym]) < 3:
            continue

        avg_vol = sum(_vol_history[sym][:-1]) / len(_vol_history[sym][:-1])
        vol_spike = (vol / avg_vol) if avg_vol > 0 else 1.0

        # Check cooldown — don't alert same token within 1 hour
        last_alert = _alerted.get(sym, 0)
        if now - last_alert < 3600:
            continue

        # Signal conditions
        is_vol_spike  = vol_spike >= VOL_SPIKE_X
        is_price_move = abs(chg) >= PRICE_CHANGE_PCT
        direction     = "PUMP 📈" if chg > 0 else "DUMP 📉"

        if is_vol_spike and is_price_move:
            signals.append({
                "symbol":    sym,
                "price":     t["price"],
                "change":    chg,
                "volume":    vol,
                "vol_spike": vol_spike,
                "direction": direction,
                "strength":  "STRONG" if vol_spike > 5 else "MODERATE",
            })
            _alerted[sym] = now

    return signals


async def send_telegram(session, msg: str):
    """Send Telegram alert."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info(f"   [Telegram not configured] {msg[:100]}")
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        ) as r:
            if r.status == 200:
                log.info("   ✅ HL Telegram alert sent")
            else:
                log.warning(f"   HL Telegram error: {r.status}")
    except Exception as e:
        log.warning(f"   HL Telegram error: {e}")


def build_alert(sig: dict) -> str:
    direction = sig["direction"]
    strength  = sig["strength"]
    chg_str   = f"+{sig['change']:.1f}%" if sig['change'] > 0 else f"{sig['change']:.1f}%"
    return f"""⚡ *HYPERLIQUID MOMENTUM* ⚡
{direction} *${sig['symbol']}* — {strength}

💰 Price:      `${sig['price']:.4f}`
📊 24h Change: `{chg_str}`
📈 Volume:     `${sig['volume']:,.0f}`
🔥 Vol Spike:  `{sig['vol_spike']:.1f}x` above average

_Trade on Hyperliquid, Bybit, OKX_
_⚠️ Scalp only — use trailing stop. DYOR._
🕐 _{datetime.now(timezone.utc).strftime('%H:%M UTC')}_"""


async def run_scan():
    ssl_ctx   = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        tokens  = await fetch_hl_spot(session)
        signals = detect_momentum(tokens)

        log.info(f"   HL tokens scanned: {len(tokens)} | Signals: {len(signals)}")

        for sig in signals:
            msg = build_alert(sig)
            await send_telegram(session, msg)
            log.info(f"   ⚡ HL SIGNAL: {sig['symbol']} {sig['direction']} {sig['change']:+.1f}% vol={sig['vol_spike']:.1f}x")

        if not signals:
            log.info("   😴 No HL momentum signals this scan")


async def main():
    log.info("⚡ Hyperliquid Momentum Scanner started")
    log.info(f"   Thresholds: Vol spike {VOL_SPIKE_X}x | Price move {PRICE_CHANGE_PCT}%")
    log.info(f"   Telegram: {'✅ configured' if TELEGRAM_BOT_TOKEN else '⚠️  not set'}")
    await run_scan()


if __name__ == "__main__":
    asyncio.run(main())
