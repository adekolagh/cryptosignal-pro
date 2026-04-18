#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║           CryptoSignal Pro — Multi-Layer Smart Money Scanner     ║
╠══════════════════════════════════════════════════════════════════╣
║  Layer 1 ▸ Nansen SM Screener   — wallet activity    (30 pts)   ║
║  Layer 2 ▸ Nansen SM Netflow    — inflow/outflow      (20 pts)   ║
║  Layer 3 ▸ Etherscan Safety     — contract verified   (20 pts)   ║
║  Layer 4 ▸ CryptoCompare        — volume + momentum   (15 pts)   ║
║  Layer 5 ▸ Fear & Greed Index   — market sentiment    (10 pts)   ║
║  Layer 6 ▸ CoinGecko            — volume confirmation  (5 pts)   ║
║                                                        ───────   ║
║  MAX SCORE                                             100 pts   ║
╠══════════════════════════════════════════════════════════════════╣
║  Keys: NANSEN_API_KEYS, ETHERSCAN_API_KEYS,                      ║
║        CRYPTOCOMPARE_API_KEY, TELEGRAM_BOT_TOKEN,                ║
║        TELEGRAM_CHAT_ID  — all stored in .env / GitHub Secrets   ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import aiohttp
import ssl
import certifi
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the arkhsen/ folder (same folder as this script)
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

# Signals JSON written to templates/ so dashboard.html can read it
SIGNALS_JSON = BASE_DIR / "templates" / "signals.json"

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "")

SCAN_INTERVAL_SEC   = 300   # 5 minutes
SIGNAL_THRESHOLD    = 38    # Base — overridden dynamically each scan by Fear&Greed

def dynamic_threshold(fear_greed_value: int) -> int:
    """
    Threshold moves with market conditions automatically.
    Bear market scores are naturally lower — threshold lowers too.
    Bull market scores inflate — threshold rises to filter noise.
    """
    if fear_greed_value < 25:   return 35   # Extreme fear  (now: F&G=26)
    elif fear_greed_value < 45: return 40   # Fear
    elif fear_greed_value < 55: return 50   # Neutral
    elif fear_greed_value < 75: return 55   # Greed
    else:                       return 60   # Extreme greed
COOLDOWN_HOURS      = 4     # Hours before re-alerting same token
MAX_ALERTS_PER_SCAN = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("CSP-v2")


def _load_keys(plural_var: str, singular_var: str = "") -> list:
    """
    Load a pool of API keys from .env.
    - First checks the plural variable  e.g. NANSEN_API_KEYS=key1,key2,key3
    - Falls back to the singular         e.g. NANSEN_API_KEY=key1
    Keys live in .env only. Never committed to GitHub.
    """
    raw = os.getenv(plural_var, "").strip()
    if raw:
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    single = os.getenv(singular_var, "").strip()
    return [single] if single else []


# ─────────────────────────────────────────────────────────────────────
# KEY ROTATOR
# Shared by Nansen and Etherscan layers.
# When a key hits its rate limit or credit limit the rotator silently
# moves to the next key in the pool. All keys live in .env only.
# ─────────────────────────────────────────────────────────────────────
class KeyRotator:
    """
    Manages a pool of API keys for one provider.
    Thread-safe for async use (single event loop, no shared state issues).

    Usage:
        rotator = KeyRotator("Nansen", keys=["key1", "key2", "key3"])
        key = rotator.current()          # get active key
        rotator.rotate("402 credits")    # mark exhausted, move to next
        rotator.reset()                  # call at start of each scan
    """

    def __init__(self, name: str, keys: list):
        self.name       = name
        self._keys      = keys       # full list from .env
        self._index     = 0          # which key is active
        self._exhausted = set()      # keys marked as used up this scan

    def has_keys(self) -> bool:
        return bool(self._available())

    def current(self) -> str:
        """Active key, or empty string if none left."""
        available = self._available()
        if not available:
            return ""
        # Ensure index points to a valid available key
        if self._index >= len(self._keys) or self._keys[self._index] in self._exhausted:
            self._index = self._keys.index(available[0])
        return self._keys[self._index]

    def rotate(self, reason: str = "limit") -> bool:
        """
        Mark current key as exhausted, move to next.
        Returns True  = new key is now active.
        Returns False = ALL keys exhausted, layer disabled.
        """
        current = self.current()
        if current:
            self._exhausted.add(current)
            log.warning(
                f"   [{self.name}] Key #{self._index + 1}/{len(self._keys)} "
                f"exhausted ({reason})"
            )

        available = self._available()
        if not available:
            log.warning(
                f"   [{self.name}] All {len(self._keys)} "
                f"key(s) exhausted — layer disabled this scan"
            )
            return False

        for i, key in enumerate(self._keys):
            if key not in self._exhausted:
                self._index = i
                break

        log.info(
            f"   [{self.name}] Switched to key "
            f"#{self._index + 1}/{len(self._keys)}"
        )
        return True

    def reset(self):
        """
        Call at the start of every scan to allow rate-limited keys
        to recover. Credit-exhausted keys stay in the pool — Nansen
        Pro resets monthly, Etherscan resets per second.
        """
        self._exhausted.clear()
        self._index = 0

    def status(self) -> str:
        total = len(self._keys)
        if total == 0:
            return "⚠️  No keys — add to .env"
        if total == 1:
            return "✅ 1 key loaded"
        return f"✅ {total} keys loaded — rotation active"

    def _available(self) -> list:
        return [k for k in self._keys if k not in self._exhausted]


# ─────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────
@dataclass
class TokenSignal:
    symbol:         str
    name:           str
    chain:          str
    price:          float
    token_address:  str = ""   # contract address (from Nansen or CoinGecko)
    coingecko_id:   str = ""   # e.g. "ethereum", "solana" — for URL
    coingecko_url:  str = ""   # https://www.coingecko.com/en/coins/{id}
    explorer_url:   str = ""   # block explorer link for contract
    score:          int = 0
    confidence:     str = "MODERATE"
    breakdown:      list = field(default_factory=list)
    warnings:       list = field(default_factory=list)
    timeframe:      str = "1–4 hours"
    entry:          float = 0.0
    target_1:       float = 0.0
    target_2:       float = 0.0
    stop_loss:      float = 0.0
    risk:           str = "MEDIUM"
    smart_money_buyers:  int = 0
    sm_netflow_usd:      float = 0.0
    arkham_flagged:      bool = False
    arkham_entity_count: int = 0
    signal_type:         str = "LONG"   # LONG = buy signal, SHORT = sell/dump signal
    liquidity_usd:       float = 0.0
    market_cap_usd:      float = 0.0
    liq_mcap_ratio:      float = 0.0
    flow_impact_pct:     float = 0.0
    flow_classification: str = "NOISE"
    price_change_24h:    float = 0.0
    price_confirmed:     bool = False
    projection:          str = ""


# ─────────────────────────────────────────────────────────────────────
# LAYER 1 + 2 — NANSEN  (Smart Money Screener + Netflow)
# Real API: https://api.nansen.ai
# Requires: Pro plan ($49/mo) which includes API credits
# ─────────────────────────────────────────────────────────────────────
class NansenLayer:
    BASE = "https://api.nansen.ai"

    def __init__(self, rotator: KeyRotator):
        self.rotator = rotator

    def _headers(self) -> dict:
        return {"apiKey": self.rotator.current(), "Content-Type": "application/json"}

    async def screen_smart_money_tokens(
        self, session: aiohttp.ClientSession, chains: list
    ) -> list:
        """
        Layer 1: Nansen Token Screener — only_smart_money=True.
        Returns tokens where Smart Money wallets are actively buying.
        Credit cost: 5 per call.
        On 402 (credits out) or 401 (bad key) → rotates to next key automatically.
        """
        if not self.rotator.has_keys():
            return []

        now_utc = datetime.now(timezone.utc)
        from_dt = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_dt   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        # LONG signal payload — SM buying (sort by highest netflow first)
        payload = {
            "chains": chains,
            "timeframe": "24h",
            "pagination": {"page": 1, "per_page": 30},
            "filters": {
                "only_smart_money": True,
                "market_cap_usd":   {"min": 100_000},  # loose filter — scoring handles quality
            },
            "order_by": [{"field": "netflow", "direction": "DESC"}]
        }

        # Try with current key, rotate on failure, try once more
        for attempt in range(len(self.rotator._keys) + 1):
            key = self.rotator.current()
            if not key:
                break
            try:
                async with session.post(
                    f"{self.BASE}/api/v1/token-screener",
                    headers=self._headers(),
                    json=payload,
                    timeout=15
                ) as r:
                    if r.status == 200:
                        data   = await r.json()
                        tokens = data.get("tokens", data.get("data", []))
                        log.info(
                            f"   Nansen screener: {len(tokens)} SM tokens "
                            f"(key #{self.rotator._index + 1}/{len(self.rotator._keys)})"
                        )
                        return tokens
                    elif r.status == 402:
                        if not self.rotator.rotate("credits exhausted"):
                            break
                    elif r.status == 401:
                        if not self.rotator.rotate("invalid key"):
                            break
                    elif r.status == 429:
                        if not self.rotator.rotate("rate limited"):
                            break
                    else:
                        log.warning(f"   Nansen screener HTTP {r.status}")
                        break
            except Exception as e:
                log.warning(f"   Nansen screener error: {e}")
                break

        return []

    async def screen_short_signals(
        self, session: aiohttp.ClientSession, chains: list
    ) -> list:
        """
        Fetch tokens where Smart Money is SELLING (negative netflow).
        These are SHORT signals — useful for futures/perpetuals trading.
        Sort by netflow ASC = most negative (strongest selling) first.
        """
        if not self.rotator.has_keys():
            return []

        now_utc = datetime.now(timezone.utc)
        from_dt = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_dt   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "chains": chains,
            "timeframe": "24h",
            "pagination": {"page": 1, "per_page": 20},
            "filters": {
                "only_smart_money": True,
                "market_cap_usd":   {"min": 100_000},
            },
            "order_by": [{"field": "netflow", "direction": "ASC"}]  # most negative first
        }

        for attempt in range(len(self.rotator._keys) + 1):
            key = self.rotator.current()
            if not key:
                break
            try:
                async with session.post(
                    f"{self.BASE}/api/v1/token-screener",
                    headers=self._headers(),
                    json=payload,
                    timeout=15
                ) as r:
                    if r.status == 200:
                        data   = await r.json()
                        tokens = data.get("tokens", data.get("data", []))
                        # Only keep tokens with actual negative netflow
                        short_tokens = [t for t in tokens if (t.get("netflow", 0) or 0) < 0]
                        log.info(
                            f"   Nansen SHORT screener: {len(short_tokens)} SM selling tokens"
                        )
                        return short_tokens
                    elif r.status in (401, 402, 429):
                        reason = {401:"invalid key",402:"credits exhausted",429:"rate limited"}[r.status]
                        if not self.rotator.rotate(reason):
                            break
                    else:
                        log.warning(f"   Nansen short screener HTTP {r.status}")
                        break
            except Exception as e:
                log.warning(f"   Nansen short screener error: {e}")
                break
        return []

    async def get_smart_money_netflow(
        self, session: aiohttp.ClientSession, token_address: str, chain: str
    ) -> dict:
        """
        Layer 2: Smart Money net inflow/outflow for a token.
        Positive netflow = buying. Negative = selling.
        Credit cost: 5 per call.
        Rotates key automatically on credit/rate errors.
        """
        if not self.rotator.has_keys() or not token_address:
            return {}

        now_utc = datetime.now(timezone.utc)
        payload = {
            "chain": chain,
            "token_address": token_address,
            "filters": {
                "smart_money": {"include": ["Smart Money"]},
                "include_stablecoins": False,
            },
            "date": {
                "from": (now_utc - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to":   now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            },
            "pagination": {"page": 1, "per_page": 20}
        }

        for attempt in range(len(self.rotator._keys) + 1):
            key = self.rotator.current()
            if not key:
                break
            try:
                async with session.post(
                    f"{self.BASE}/api/v1/smart-money/netflow",
                    headers=self._headers(),
                    json=payload,
                    timeout=15
                ) as r:
                    if r.status == 200:
                        return await r.json()
                    elif r.status in (401, 402, 429):
                        reason = {401: "invalid key", 402: "credits exhausted", 429: "rate limited"}[r.status]
                        if not self.rotator.rotate(reason):
                            break
                    else:
                        log.debug(f"   Nansen netflow HTTP {r.status}")
                        break
            except Exception as e:
                log.debug(f"   Nansen netflow error: {e}")
                break

        return {}


    @staticmethod
    def classify_flow(flow_pct):
        if flow_pct < 5.0:    return ("NOISE",       "⚪")
        elif flow_pct < 15.0: return ("WATCH",       "🔶")
        elif flow_pct < 30.0: return ("SIGNIFICANT", "🟡")
        elif flow_pct < 50.0: return ("MAJOR",       "🔴")
        else:                 return ("EXTREME",     "🚨")

    @staticmethod
    def classify_liq_health(liquidity, market_cap):
        if market_cap <= 0: return ("UNKNOWN", "❓")
        r = (liquidity / market_cap) * 100
        if r < 0.1:   return ("ULTRA-THIN", "🚨")
        elif r < 0.5: return ("THIN",       "⚠️")
        elif r < 2.0: return ("MODERATE",   "🔶")
        elif r < 10:  return ("HEALTHY",    "✅")
        else:         return ("DEEP",       "💎")

    @staticmethod
    def check_price_confirms(price_chg, is_long):
        if is_long:
            if price_chg > 5.0:     return True,  f"✅ Price +{price_chg:.1f}% confirms buying — BULLISH"
            elif price_chg >= -2.0: return True,  f"📊 Price flat ({price_chg:+.1f}%) + inflow = ACCUMULATION — pre-pump likely"
            else:                   return False, f"⚠️ Price {price_chg:+.1f}% falling despite buying — weak LONG"
        else:
            if price_chg < -5.0:   return True,  f"✅ Price {price_chg:.1f}% confirms selling — BEARISH"
            elif price_chg <= 2.0: return True,  f"📊 Price flat ({price_chg:+.1f}%) + outflow = DISTRIBUTION — pre-dump likely"
            else:                  return False, f"🚫 Price +{price_chg:.1f}% rising despite selling — SHORT BLOCKED"

    @staticmethod
    def project_move(flow_pct, flow_class, price_chg, is_long):
        if flow_class == "NOISE": return "Routine rebalancing — no directional pressure"
        word  = "PUMP" if is_long else "DUMP"
        inout = "inflow" if is_long else "outflow"
        dirn  = "upward" if is_long else "downward"
        if flow_class == "EXTREME" and abs(price_chg) < 2:
            return f"🚀 PRE-{word}: {flow_pct:.0f}% of liquidity moved, price not reacted — imminent {dirn} move"
        elif flow_class == "MAJOR" and abs(price_chg) < 5:
            return f"📈 {'ACCUMULATION' if is_long else 'DISTRIBUTION'}: {flow_pct:.0f}% liquidity {inout} — {dirn} move expected 1-3 days"
        elif flow_class == "SIGNIFICANT":
            return f"🔶 {'Buying' if is_long else 'Selling'} pressure: {flow_pct:.0f}% of liquidity — watch for breakout"
        else:
            return f"📊 {flow_pct:.0f}% {inout} — monitor, insufficient for major move yet"


    @staticmethod
    def classify_flow(flow_pct):
        if flow_pct < 5.0:    return ("NOISE",       "⚪")
        elif flow_pct < 15.0: return ("WATCH",       "🔶")
        elif flow_pct < 30.0: return ("SIGNIFICANT", "🟡")
        elif flow_pct < 50.0: return ("MAJOR",       "🔴")
        else:                 return ("EXTREME",     "🚨")

    @staticmethod
    def classify_liq_health(liquidity, market_cap):
        if market_cap <= 0: return ("UNKNOWN", "❓")
        r = (liquidity / market_cap) * 100
        if r < 0.1:   return ("ULTRA-THIN", "🚨")
        elif r < 0.5: return ("THIN",       "⚠️")
        elif r < 2.0: return ("MODERATE",   "🔶")
        elif r < 10:  return ("HEALTHY",    "✅")
        else:         return ("DEEP",       "💎")

    @staticmethod
    def check_price_confirms(price_chg, is_long):
        if is_long:
            if price_chg > 5.0:     return True,  f"✅ Price +{price_chg:.1f}% confirms buying — BULLISH"
            elif price_chg >= -2.0: return True,  f"📊 Price flat ({price_chg:+.1f}%) + inflow = ACCUMULATION — pre-pump likely"
            else:                   return False, f"⚠️ Price {price_chg:+.1f}% falling despite buying — weak LONG"
        else:
            if price_chg < -5.0:   return True,  f"✅ Price {price_chg:.1f}% confirms selling — BEARISH"
            elif price_chg <= 2.0: return True,  f"📊 Price flat ({price_chg:+.1f}%) + outflow = DISTRIBUTION — pre-dump likely"
            else:                  return False, f"🚫 Price +{price_chg:.1f}% rising despite selling — SHORT BLOCKED"

    @staticmethod
    def project_move(flow_pct, flow_class, price_chg, is_long):
        if flow_class == "NOISE": return "Routine rebalancing — no directional pressure"
        word  = "PUMP" if is_long else "DUMP"
        inout = "inflow" if is_long else "outflow"
        dirn  = "upward" if is_long else "downward"
        if flow_class == "EXTREME" and abs(price_chg) < 2:
            return f"🚀 PRE-{word}: {flow_pct:.0f}% of liquidity moved, price not reacted — imminent {dirn} move"
        elif flow_class == "MAJOR" and abs(price_chg) < 5:
            return f"📈 {'ACCUMULATION' if is_long else 'DISTRIBUTION'}: {flow_pct:.0f}% liquidity {inout} — {dirn} move expected 1-3 days"
        elif flow_class == "SIGNIFICANT":
            return f"🔶 {'Buying' if is_long else 'Selling'} pressure: {flow_pct:.0f}% of liquidity — watch for breakout"
        else:
            return f"📊 {flow_pct:.0f}% {inout} — monitor, insufficient for major move yet"

    def score_short(self, token: dict) -> tuple:
        """
        SHORT scoring -- liquidity-valuation based.
        BLOCKS signal if: price rising (buyers absorbing sellers)
        BLOCKS signal if: flow < 5% of liquidity (noise)
        Returns: (pts, notes, valuation_dict)
        """
        pts = 0
        notes = []

        sm_count   = token.get("nof_traders", 0) or 0
        netflow    = token.get("netflow", 0) or 0
        sell_vol   = token.get("sell_volume", 0) or 0
        liquidity  = token.get("liquidity", 0) or 1
        market_cap = token.get("market_cap_usd", 0) or 0
        price_chg  = (token.get("price_change", 0) or 0) * 100
        outflow    = abs(netflow) if netflow < 0 else sell_vol

        flow_pct   = (outflow / liquidity) * 100 if liquidity > 0 else 0
        flow_class, flow_emoji = self.classify_flow(flow_pct)
        liq_class,  liq_emoji  = self.classify_liq_health(liquidity, market_cap)
        liq_ratio  = (liquidity / market_cap * 100) if market_cap > 0 else 0
        price_ok, price_note = self.check_price_confirms(price_chg, is_long=False)
        projection = self.project_move(flow_pct, flow_class, price_chg, is_long=False)

        valuation = {
            "liquidity_usd":       liquidity,
            "market_cap_usd":      market_cap,
            "liq_mcap_ratio":      liq_ratio,
            "liq_classification":  liq_class,
            "flow_usd":            outflow,
            "flow_pct":            flow_pct,
            "flow_classification": flow_class,
            "price_change_24h":    price_chg,
            "price_confirmed":     price_ok,
            "projection":          projection,
        }

        # HARD BLOCK 1: price rising means buyers absorbing sellers
        if not price_ok and price_chg > 2.0:
            notes.append(f"SHORT BLOCKED [Valuation] Price +{price_chg:.1f}% rising -- buyers absorbing {flow_pct:.1f}% outflow")
            notes.append(f"[Projection] {projection}")
            return 0, notes, valuation

        # HARD BLOCK 2: noise-level selling
        if flow_class == "NOISE":
            notes.append(f"SHORT BLOCKED [Valuation] Flow {flow_pct:.1f}% of liquidity -- NOISE not a real dump")
            notes.append(f"[Projection] {projection}")
            return 0, notes, valuation

        # Flow impact score (0-15 pts)
        if flow_class == "EXTREME":
            pts += 15; notes.append(f"EXTREME [Valuation] Outflow {flow_pct:.1f}% of liquidity")
        elif flow_class == "MAJOR":
            pts += 12; notes.append(f"MAJOR [Valuation] Outflow {flow_pct:.1f}% of liquidity")
        elif flow_class == "SIGNIFICANT":
            pts += 8;  notes.append(f"SIGNIFICANT [Valuation] Outflow {flow_pct:.1f}% of liquidity")
        elif flow_class == "WATCH":
            pts += 4;  notes.append(f"WATCH [Valuation] Outflow {flow_pct:.1f}% of liquidity")

        # SM wallet count (0-10 pts)
        if sm_count >= 10:
            pts += 10; notes.append(f"[Nansen] {sm_count} SM wallets SELLING -- coordinated exit")
        elif sm_count >= 5:
            pts += 7;  notes.append(f"[Nansen] {sm_count} SM wallets selling")
        elif sm_count >= 2:
            pts += 4;  notes.append(f"[Nansen] {sm_count} SM wallets selling")
        elif sm_count >= 1:
            pts += 2;  notes.append(f"[Nansen] {sm_count} SM wallet selling")

        # Price confirmation
        notes.append(price_note)
        pts = pts + 3 if price_ok else pts

        notes.append(f"[Valuation] Liq ${liquidity:,.0f} | MCap ${market_cap:,.0f} | Ratio {liq_ratio:.2f}%")
        notes.append(f"[Projection] {projection}")

        return min(pts, 30), notes, valuation


    def score_screener(self, token: dict) -> tuple:
        """
        Layer 1 -- Liquidity-Valuation scoring.
        All flows as percentage of current liquidity.
        Returns: (pts, notes, valuation_dict)
        """
        pts   = 0
        notes = []

        sm_count   = token.get("nof_traders", 0) or 0
        netflow    = token.get("netflow", 0) or 0
        buy_vol    = token.get("buy_volume", 0) or 0
        sell_vol   = token.get("sell_volume", 0) or 0
        liquidity  = token.get("liquidity", 0) or 1
        market_cap = token.get("market_cap_usd", 0) or 0
        age_days   = token.get("token_age_days", 0) or 0
        price_chg  = (token.get("price_change", 0) or 0) * 100

        flow_usd   = abs(netflow) if netflow != 0 else max(buy_vol, sell_vol)
        flow_pct   = (flow_usd / liquidity) * 100 if liquidity > 0 else 0
        flow_class, flow_emoji = self.classify_flow(flow_pct)
        liq_class,  liq_emoji  = self.classify_liq_health(liquidity, market_cap)
        liq_ratio  = (liquidity / market_cap * 100) if market_cap > 0 else 0
        is_long    = netflow >= 0
        price_ok, price_note = self.check_price_confirms(price_chg, is_long)
        projection = self.project_move(flow_pct, flow_class, price_chg, is_long)

        valuation = {
            "liquidity_usd":       liquidity,
            "market_cap_usd":      market_cap,
            "liq_mcap_ratio":      liq_ratio,
            "liq_classification":  liq_class,
            "flow_usd":            flow_usd,
            "flow_pct":            flow_pct,
            "flow_classification": flow_class,
            "price_change_24h":    price_chg,
            "price_confirmed":     price_ok,
            "projection":          projection,
        }

        # A. Flow impact as % of liquidity (0-15 pts)
        if flow_class == "EXTREME":
            pts += 15; notes.append(f"EXTREME [Valuation] Flow: {flow_pct:.1f}% of liquidity")
        elif flow_class == "MAJOR":
            pts += 12; notes.append(f"MAJOR [Valuation] Flow: {flow_pct:.1f}% of liquidity")
        elif flow_class == "SIGNIFICANT":
            pts += 8;  notes.append(f"SIGNIFICANT [Valuation] Flow: {flow_pct:.1f}% of liquidity")
        elif flow_class == "WATCH":
            pts += 4;  notes.append(f"WATCH [Valuation] Flow: {flow_pct:.1f}% of liquidity")
        else:
            notes.append(f"NOISE [Valuation] Flow: {flow_pct:.1f}% -- routine rebalancing")

        # B. Market health (0-5 pts)
        if liq_class in ("THIN", "ULTRA-THIN"):
            pts += 5; notes.append(f"[Valuation] {liq_class} market {liq_ratio:.2f}% liq/mcap -- signals amplified")
        elif liq_class == "MODERATE":
            pts += 3; notes.append(f"[Valuation] {liq_class} market {liq_ratio:.2f}% liq/mcap")
        else:
            pts += 2; notes.append(f"[Valuation] {liq_class} market {liq_ratio:.2f}% liq/mcap")

        # C. SM wallet count (0-8 pts)
        if sm_count >= 10:
            pts += 8; notes.append(f"[Nansen] {sm_count} SM wallets -- HIGH conviction")
        elif sm_count >= 5:
            pts += 6; notes.append(f"[Nansen] {sm_count} SM wallets -- GOOD conviction")
        elif sm_count >= 2:
            pts += 3; notes.append(f"[Nansen] {sm_count} SM wallets")
        elif sm_count >= 1:
            pts += 1; notes.append(f"[Nansen] {sm_count} SM wallet")

        # D. Price confirmation (+2 or -10)
        notes.append(price_note)
        pts = pts + 2 if price_ok else max(0, pts - 10)

        # E. Token age (0-2 pts)
        if age_days >= 365:
            pts += 2; notes.append(f"[Nansen] Token age: {int(age_days)}d established")
        elif age_days >= 90:
            pts += 1; notes.append(f"[Nansen] Token age: {int(age_days)}d maturing")
        elif 0 < age_days < 30:
            pts = max(0, pts - 2); notes.append(f"[Nansen] Token age: {int(age_days)}d very new")

        notes.append(f"[Projection] {projection}")
        return min(pts, 30), notes, valuation


    def score_netflow(self, netflow_data: dict) -> tuple[int, list, float]:
        """Score based on Smart Money netflow direction. Max 20 pts."""
        if not netflow_data:
            return 0, ["⚪ [Nansen] Netflow: No data"], 0.0

        pts = 0
        notes = []
        # Extract net inflow (positive = buying, negative = selling)
        inflow  = netflow_data.get("inflow_usd",  netflow_data.get("total_inflow", 0))  or 0
        outflow = netflow_data.get("outflow_usd", netflow_data.get("total_outflow", 0)) or 0
        net     = inflow - abs(outflow)

        if net > 500_000:
            pts = 20
            notes.append(f"✅ [Nansen] SM net INFLOW: +${net:,.0f} — Strong institutional buying")
        elif net > 100_000:
            pts = 14
            notes.append(f"✅ [Nansen] SM net INFLOW: +${net:,.0f} — Clear buying pressure")
        elif net > 10_000:
            pts = 8
            notes.append(f"🔶 [Nansen] SM net INFLOW: +${net:,.0f} — Mild buying")
        elif net > 0:
            pts = 3
            notes.append(f"📊 [Nansen] SM slightly positive: +${net:,.0f}")
        else:
            pts = 0
            notes.append(f"⚠️  [Nansen] SM net OUTFLOW: ${net:,.0f} — Smart Money is SELLING")

        return pts, notes, net


# ─────────────────────────────────────────────────────────────────────
# LAYER 3 — ETHERSCAN  (Contract Verification + Token Safety Check)
# Replaces Arkham (no API response received).
# Free instant key: etherscan.io/register → My Account → API Keys
# What it checks:
#   1. Is the contract source code verified on Etherscan?
#      Unverified contract = serious red flag — could be a scam.
#   2. How old is the token? Very new tokens (< 30 days) = higher risk.
#   3. How many holders? Very few holders = easy manipulation.
# Note: Only works for Ethereum-based tokens (ERC-20).
#       Solana tokens get a neutral score.
# ─────────────────────────────────────────────────────────────────────
class EtherscanLayer:
    BASE = "https://api.etherscan.io/api"

    def __init__(self, rotator: KeyRotator):
        self.rotator = rotator

    async def _get(
        self,
        session: aiohttp.ClientSession,
        params: dict,
    ) -> Optional[dict]:
        """
        Make one Etherscan API call with the current key.
        On 429 (rate limit) rotate and retry once with the next key.
        Returns parsed JSON or None on failure.
        """
        for attempt in range(len(self.rotator._keys) + 1):
            key = self.rotator.current()
            if not key:
                return None
            try:
                call_params = {**params, "apikey": key}
                async with session.get(
                    self.BASE, params=call_params, timeout=10
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        # Etherscan returns rate-limit errors inside JSON body
                        if isinstance(data, dict):
                            msg = str(data.get("result", "")).lower()
                            if "rate limit" in msg or "max rate" in msg:
                                if not self.rotator.rotate("rate limited"):
                                    return None
                                continue
                        return data
                    elif r.status == 429:
                        if not self.rotator.rotate("HTTP 429 rate limit"):
                            return None
                    else:
                        log.debug(f"   Etherscan HTTP {r.status}")
                        return None
            except Exception as e:
                log.debug(f"   Etherscan call error: {e}")
                return None
        return None

    async def fetch_token_info(
        self,
        session: aiohttp.ClientSession,
        contract_address: str,
        chain: str,
    ) -> dict:
        """
        Check contract safety for Ethereum tokens.
        Call 1: Is the source code verified? (unverified = scam risk)
        Call 2: Does it have active holders? (no holders = likely rug)
        Only runs for Ethereum chain. Other chains get a neutral score.
        """
        if not self.rotator.has_keys():
            return {}
        if chain.lower() not in ("ethereum", "eth") or not contract_address:
            return {}

        result = {}

        # Call 1 — Source code verification
        data = await self._get(session, {
            "module":  "contract",
            "action":  "getsourcecode",
            "address": contract_address,
        })
        if data:
            items = data.get("result", [])
            if items and isinstance(items, list):
                source = items[0].get("SourceCode", "")
                result["is_verified"] = bool(source and source.strip() != "")
                result["contract_name"] = items[0].get("ContractName", "")

        # Call 2 — Holder count (how many unique wallets hold this)
        data2 = await self._get(session, {
            "module":          "token",
            "action":          "tokenholderlist",
            "contractaddress": contract_address,
            "page":            "1",
            "offset":          "10",   # get top 10 holders
        })
        if data2 and data2.get("status") == "1":
            holders = data2.get("result", [])
            result["has_holders"] = True
            # Check concentration — if top holders own too much it is risky
            if holders:
                try:
                    # Get total supply for concentration calc
                    supplies = [float(h.get("TokenHolderQuantity", 0)) for h in holders]
                    total    = sum(supplies)
                    top3     = sum(sorted(supplies, reverse=True)[:3])
                    result["top3_concentration"] = (top3 / total * 100) if total > 0 else 0
                    result["holder_sample"] = len(holders)
                except Exception:
                    pass
        else:
            result["has_holders"] = False

        # Call 3 — Token creation date from first transaction
        data3 = await self._get(session, {
            "module":     "account",
            "action":     "tokentx",
            "contractaddress": contract_address,
            "startblock": "0",
            "endblock":   "999999999",
            "page":       "1",
            "offset":     "1",
            "sort":       "asc",
        })
        if data3 and data3.get("status") == "1":
            txs = data3.get("result", [])
            if txs:
                import time
                ts = int(txs[0].get("timeStamp", 0))
                if ts > 0:
                    age_days = (time.time() - ts) / 86400
                    result["token_age_days"] = round(age_days, 1)

        return result

    def score(
        self, info: dict, chain: str, contract_address: str
    ) -> tuple[int, list, bool]:
        """
        Score based on contract safety checks.
        Returns: (score, notes, is_suspicious)
        Max 20 pts. Unverified contract kills signal (is_suspicious = True).
        """
        # Solana and other non-EVM chains — neutral, no check
        if chain.lower() not in ("ethereum", "eth"):
            return 10, [f"📊 [Safety] {chain.upper()} token — contract check not available (non-EVM)"], False

        # No contract address (native coin like ETH itself)
        if not contract_address:
            return 10, ["📊 [Safety] Native coin — no contract to verify"], False

        # No Etherscan key — neutral score, note it
        if not info:
            return 8, ["⚪ [Safety] Add ETHERSCAN_API_KEY (free at etherscan.io) for contract verification"], False

        pts   = 0
        notes = []
        is_suspicious = False

        # Contract verification — most important check
        is_verified = info.get("is_verified", None)
        if is_verified is True:
            pts += 15
            notes.append("✅ [Safety] Contract source code VERIFIED on Etherscan")
        elif is_verified is False:
            is_suspicious = True
            pts = 0
            notes.append("🚨 [Safety] Contract NOT VERIFIED — source code hidden — HIGH SCAM RISK")
            return pts, notes, is_suspicious
        else:
            pts += 8
            notes.append("🔶 [Safety] Contract verification status unknown")

        # ── Holder distribution check ────────────────────────────
        has_holders = info.get("has_holders", None)
        top3_conc   = info.get("top3_concentration", None)

        if has_holders is True:
            pts += 3
            notes.append("✅ [Safety] Token has active holders on Etherscan")
            # Concentration check — top 3 holders owning too much = risk
            if top3_conc is not None:
                if top3_conc >= 80:
                    pts -= 3
                    notes.append(f"🚨 [Safety] Top 3 holders own {top3_conc:.0f}% — extreme concentration risk")
                elif top3_conc >= 50:
                    notes.append(f"⚠️  [Safety] Top 3 holders own {top3_conc:.0f}% — concentration risk")
                else:
                    pts += 2
                    notes.append(f"✅ [Safety] Top 3 holders own {top3_conc:.0f}% — well distributed")
        elif has_holders is False:
            pts -= 2
            notes.append("⚠️  [Safety] No holder data — token may be very new or illiquid")

        # ── Token age from Etherscan ──────────────────────────────
        eth_age = info.get("token_age_days", None)
        if eth_age is not None:
            if eth_age >= 365:
                pts += 2
                notes.append(f"✅ [Safety] Token age: {eth_age:.0f} days — established on-chain")
            elif eth_age >= 90:
                pts += 1
                notes.append(f"📊 [Safety] Token age: {eth_age:.0f} days — maturing")
            elif eth_age < 14:
                pts -= 3
                notes.append(f"🚨 [Safety] Token age: {eth_age:.0f} days — brand new, extreme risk")
            elif eth_age < 30:
                pts -= 1
                notes.append(f"⚠️  [Safety] Token age: {eth_age:.0f} days — very new token")

        return min(pts, 20), notes, is_suspicious


# ─────────────────────────────────────────────────────────────────────
# LAYER 7 — ARKHAM INTELLIGENCE  (Entity names + flow data)
# API: https://api.arkm.com  |  Docs: https://intel.arkm.com/api/docs
# Adds real entity names (Binance, Jump Trading, a16z) to signals
# ─────────────────────────────────────────────────────────────────────
class ArkhamIntelLayer:
    BASE = "https://api.arkm.com"

    def __init__(self, keys: list):
        self._keys = keys
        self._idx  = 0

    def _key(self) -> str:
        if not self._keys: return ""
        return self._keys[self._idx % len(self._keys)]

    def _rotate(self):
        self._idx = (self._idx + 1) % max(len(self._keys), 1)

    def has_keys(self) -> bool:
        return bool(self._keys)

    async def get_token_flow(self, session, chain: str, address: str) -> list:
        if not self.has_keys() or not address: return []
        if chain.lower() not in ("ethereum","base","arbitrum","bnb","polygon","optimism","avalanche"): return []
        try:
            async with session.get(
                f"{self.BASE}/token/top_flow/{chain}/{address}",
                params={"timeLast": "1d"},
                headers={"API-Key": self._key()},
                timeout=10
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    flows = []
                    for item in (data if isinstance(data, list) else []):
                        entity = (item.get("address") or {}).get("arkhamEntity") or {}
                        name   = entity.get("name", "")
                        if name:
                            flows.append({
                                "entity": name,
                                "type":   entity.get("type", ""),
                                "inUSD":  item.get("inUSD", 0),
                                "outUSD": item.get("outUSD", 0),
                            })
                    return flows
                elif r.status in (429, 403):
                    self._rotate()
        except Exception as e:
            log.debug(f"   Arkham flow error: {e}")
        return []

    async def get_token_holders(self, session, chain: str, address: str) -> list:
        if not self.has_keys() or not address: return []
        if chain.lower() not in ("ethereum","base","arbitrum","bnb","polygon","optimism","avalanche"): return []
        try:
            async with session.get(
                f"{self.BASE}/token/holders/{chain}/{address}",
                headers={"API-Key": self._key()},
                timeout=10
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    holders = []
                    for item in (data.get("addressTopHolders") or {}).get(chain, []):
                        entity = (item.get("address") or {}).get("arkhamEntity") or {}
                        name   = entity.get("name", "")
                        if name:
                            holders.append({
                                "entity": name,
                                "type":   entity.get("type", ""),
                                "pct":    item.get("pctOfCap", 0) or 0,
                            })
                    return holders[:10]
                elif r.status in (429, 403):
                    self._rotate()
        except Exception as e:
            log.debug(f"   Arkham holders error: {e}")
        return []

    def score(self, flows: list, holders: list) -> tuple[int, list]:
        """Bonus pts from entity intelligence. Max +10, min -5."""
        pts, notes = 0, []
        fund_t = ("fund","vc","venture","hedge","defi-protocol","lending-decentralized")
        cex_t  = ("cex","exchange")
        for f in flows:
            etype = (f.get("type") or "").lower()
            name  = f.get("entity", "?")
            net   = f.get("inUSD", 0) - f.get("outUSD", 0)
            if any(t in etype for t in fund_t):
                if net > 100_000:
                    pts += 5; notes.append(f"✅ [Arkham] {name} (fund) +${net:,.0f} inflow")
                elif net > 10_000:
                    pts += 3; notes.append(f"🔶 [Arkham] {name} (fund) buying +${net:,.0f}")
                elif net < -100_000:
                    pts -= 3; notes.append(f"⚠️  [Arkham] {name} (fund) outflow ${net:,.0f}")
            elif any(t in etype for t in cex_t):
                if net < -500_000:
                    pts += 3; notes.append(f"✅ [Arkham] {name} withdrawal ${abs(net):,.0f} — accumulation")
                elif net > 500_000:
                    pts -= 2; notes.append(f"⚠️  [Arkham] {name} deposit ${net:,.0f} — sell pressure")
        fund_h = [h for h in holders if any(t in (h.get("type","").lower()) for t in fund_t)]
        if fund_h:
            total = sum(h["pct"] for h in fund_h)
            if total > 0.05:
                pts += 2
                names = ", ".join(h["entity"] for h in fund_h[:3])
                notes.append(f"✅ [Arkham] Funds hold {total*100:.1f}%: {names}")
        if not notes:
            notes.append("⚪ [Arkham] No named entity flow detected")
        return min(max(pts, -5), 10), notes


# ─────────────────────────────────────────────────────────────────
# LAYER 4 — CRYPTOCOMPARE  (replaces LunarCrush — free instant key)
# Get key: cryptocompare.com → My Account → API Keys (email verify only)
# Docs: https://min-api.cryptocompare.com/documentation
# ─────────────────────────────────────────────────────────────────
class CryptoCompareSocialLayer:
    BASE = "https://min-api.cryptocompare.com/data"

    async def fetch(self, session: aiohttp.ClientSession) -> dict:
        if not CRYPTOCOMPARE_API_KEY:
            return {}
        try:
            async with session.get(
                f"{self.BASE}/top/totalvolfull",
                params={"limit": "100", "tsym": "USD", "api_key": CRYPTOCOMPARE_API_KEY},
                timeout=12
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    result = {}
                    for item in data.get("Data", []):
                        raw  = item.get("RAW", {}).get("USD", {})
                        info = item.get("CoinInfo", {})
                        sym  = info.get("Name", "").upper()
                        if sym:
                            result[sym] = {
                                "CHANGEPCT24HOUR": raw.get("CHANGEPCT24HOUR", 0),
                                "VOLUME24HOURTO":  raw.get("VOLUME24HOURTO", 0),
                                "MKTCAP":          raw.get("MKTCAP", 1),
                            }
                    log.info(f"   CryptoCompare: {len(result)} coins loaded")
                    return result
                log.warning(f"   CryptoCompare HTTP {r.status}")
        except Exception as e:
            log.warning(f"   CryptoCompare error: {e}")
        return {}

    def score(self, symbol: str, data: dict) -> tuple[int, list]:
        """Max 15 pts — volume momentum + 24h price change."""
        if not data:
            return 0, ["⚪ [Social] No data — add CRYPTOCOMPARE_API_KEY (free at cryptocompare.com)"]
        pts = 0
        notes = []
        chg = data.get("CHANGEPCT24HOUR", 0) or 0
        vol = data.get("VOLUME24HOURTO", 0) or 0
        mcp = data.get("MKTCAP", 1) or 1
        vmr = vol / mcp if mcp > 0 else 0

        if 3 <= chg <= 10:
            pts += 7; notes.append(f"✅ [CryptoCompare] +{chg:.1f}% 24h — healthy momentum")
        elif chg > 10:
            pts += 4; notes.append(f"🔶 [CryptoCompare] +{chg:.1f}% 24h — strong move")
        elif 1 <= chg < 3:
            pts += 3; notes.append(f"📊 [CryptoCompare] +{chg:.1f}% 24h — early move")

        if vmr > 0.5:
            pts += 8; notes.append(f"✅ [CryptoCompare] Vol {vmr:.2f}x MCap — extreme buying")
        elif vmr > 0.25:
            pts += 5; notes.append(f"✅ [CryptoCompare] Vol {vmr:.2f}x MCap — high activity")
        elif vmr > 0.10:
            pts += 2; notes.append(f"🔶 [CryptoCompare] Vol {vmr:.2f}x MCap — above average")

        return min(pts, 15), notes


# ─────────────────────────────────────────────────────────────────
# LAYER 5 — FEAR & GREED INDEX  (free, no key needed — alternative.me)
# Source: alternative.me — 100% free, no signup, no approval
# URL: https://api.alternative.me/fng/
# ─────────────────────────────────────────────────────────────────
class FearGreedLayer:
    URL = "https://api.alternative.me/fng/?limit=2"

    async def fetch(self, session: aiohttp.ClientSession) -> Optional[dict]:
        try:
            async with session.get(self.URL, timeout=10) as r:
                if r.status == 200:
                    entries = (await r.json()).get("data", [])
                    if entries:
                        return {
                            "value":     int(entries[0].get("value", 50)),
                            "label":     entries[0].get("value_classification", "Neutral"),
                            "yesterday": int(entries[1].get("value", 50)) if len(entries) > 1 else 50,
                        }
        except Exception as e:
            log.debug(f"   Fear&Greed error: {e}")
        return None

    def score(self, data: Optional[dict]) -> tuple[int, list]:
        """
        Max 10 pts. Smart Money buying during Fear = strongest contrarian signal.
        Extreme Greed = overheated market = reduce confidence.
        """
        if not data:
            return 5, ["📊 [Fear&Greed] Unavailable — neutral score applied"]
        val  = data["value"]
        lab  = data["label"]
        yest = data["yesterday"]
        trend = "↑" if val > yest else "↓" if val < yest else "→"
        if val <= 25:   return 10, [f"✅ [Fear&Greed] EXTREME FEAR {val}/100 {trend} — ideal SM buy zone"]
        if val <= 45:   return 8,  [f"✅ [Fear&Greed] Fear {val}/100 ({lab}) {trend} — good entry conditions"]
        if val <= 55:   return 6,  [f"🔶 [Fear&Greed] Neutral {val}/100 {trend} — balanced market"]
        if val <= 75:   return 3,  [f"📊 [Fear&Greed] Greed {val}/100 {trend} — use tighter stop loss"]
        return 0, [f"⚠️  [Fear&Greed] EXTREME GREED {val}/100 {trend} — market overheated, caution"]


# ─────────────────────────────────────────────────────────────────────
# LAYER 6 — COINGECKO  (Volume Confirmation — supporting signal only)
# Free, no key needed
# ─────────────────────────────────────────────────────────────────────
class CoinGeckoLayer:
    URL = "https://api.coingecko.com/api/v3"

    async def fetch_markets(self, session: aiohttp.ClientSession) -> dict:
        """Returns {symbol: coin_data} dict."""
        try:
            async with session.get(
                f"{self.URL}/coins/markets",
                params={"vs_currency":"usd","order":"volume_desc",
                        "per_page":"200","sparkline":"false",
                        "price_change_percentage":"1h,24h"},
                timeout=12
            ) as r:
                if r.status == 200:
                    coins = await r.json()
                    return {c["symbol"].upper(): c for c in coins}
        except Exception as e:
            log.warning(f"   CoinGecko error: {e}")
        return {}

    async def fetch_trending(self, session: aiohttp.ClientSession) -> set:
        try:
            async with session.get(f"{self.URL}/search/trending", timeout=10) as r:
                if r.status == 200:
                    return {c["item"]["symbol"].upper() for c in (await r.json()).get("coins", [])}
        except:
            pass
        return set()

    async def fetch_coin_detail(
        self, session: aiohttp.ClientSession, coin_id: str
    ) -> dict:
        """
        Fetch /coins/{id} to get contract addresses across all chains.
        The 'platforms' field is a dict: { chain_name: contract_address }
        Example:
          {
            "ethereum":             "0xe28b3b32b6c345a34ff64674606124dd5aceca30",
            "binance-smart-chain":  "0xa2b726b1145a4773f68593cf171187d8ebe4d495"
          }
        We call this ONLY for tokens that passed the signal threshold,
        so it is a small number of calls per scan — not 200.
        Rate limit: free tier = 30 calls/min, so this is safe.
        """
        if not coin_id:
            return {}
        try:
            async with session.get(
                f"{self.URL}/coins/{coin_id}",
                params={
                    "localization":    "false",
                    "tickers":         "false",
                    "market_data":     "false",
                    "community_data":  "false",
                    "developer_data":  "false",
                    "sparkline":       "false",
                },
                timeout=12
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    # platforms = { "ethereum": "0x...", "solana": "So1...", ... }
                    return data.get("platforms", {})
                elif r.status == 429:
                    log.warning("   CoinGecko: Rate limited on coin detail call")
        except Exception as e:
            log.debug(f"   CoinGecko detail error for {coin_id}: {e}")
        return {}

    def pick_contract(self, platforms: dict, chain: str) -> str:
        """
        Pick the right contract address for the token's chain.
        CoinGecko chain names differ slightly from Nansen — map them.
        Returns empty string for native coins (BTC, ETH, SOL) which have no contract.
        """
        if not platforms:
            return ""

        # Map Nansen chain names → CoinGecko platform names
        chain_map = {
            "ethereum":  ["ethereum"],
            "solana":    ["solana"],
            "base":      ["base"],
            "arbitrum":  ["arbitrum-one", "arbitrum"],
            "bnb":       ["binance-smart-chain", "bnb"],
            "polygon":   ["polygon-pos", "polygon"],
            "optimism":  ["optimistic-ethereum", "optimism"],
            "avalanche": ["avalanche"],
        }

        candidates = chain_map.get(chain.lower(), [chain.lower()])
        for c in candidates:
            addr = platforms.get(c, "")
            if addr:
                return addr

        # Fallback: return first non-empty address found
        for addr in platforms.values():
            if addr:
                return addr

        return ""

    def score(self, coin: Optional[dict], is_trending: bool) -> tuple[int, list]:
        """Volume confirmation layer. Max 5 pts (supporting role only)."""
        if not coin:
            return 0, []
        pts = 0
        notes = []
        vol  = coin.get("total_volume", 0) or 0
        mcap = coin.get("market_cap", 1) or 1
        vmr  = vol / mcap

        if vmr > 0.4:
            pts += 3; notes.append(f"✅ [CoinGecko] Extreme volume: {vmr:.2f}x MCap ratio")
        elif vmr > 0.2:
            pts += 2; notes.append(f"🔶 [CoinGecko] High volume: {vmr:.2f}x MCap ratio")
        elif vmr > 0.1:
            pts += 1; notes.append(f"📊 [CoinGecko] Above-avg volume")

        if is_trending:
            pts += 2; notes.append(f"✅ [CoinGecko] 🔥 Trending on CoinGecko")

        return min(pts, 5), notes


# ─────────────────────────────────────────────────────────────────────
# TELEGRAM ALERTER
# ─────────────────────────────────────────────────────────────────────
class TelegramAlerter:
    def __init__(self):
        self._cooldown: dict[str, datetime] = {}

    def can_alert(self, symbol: str) -> bool:
        last = self._cooldown.get(symbol)
        return last is None or datetime.utcnow() - last > timedelta(hours=COOLDOWN_HOURS)

    def mark_alerted(self, symbol: str):
        self._cooldown[symbol] = datetime.utcnow()

    def _confidence_label(self, score: int) -> str:
        if score >= 90: return "🔴 EXTREME"
        if score >= 75: return "🟠 HIGH"
        if score >= 65: return "🟡 MODERATE"
        return "⚪ LOW"

    def build_message(self, sig: TokenSignal) -> str:
        """
        Telegram alert — built around the valuation model.
        Shows liquidity impact %, pattern classification,
        price confirmation and forward projection clearly.
        """
        is_short = sig.signal_type == "SHORT"
        filled   = sig.score // 10
        bar      = "█" * filled + "░" * (10 - filled)
        risk_e   = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(sig.risk, "🟡")

        # ── Flow classification labels ────────────────────────────────
        flow_cls   = sig.flow_classification or "NOISE"
        flow_emoji = {"NOISE":"⚪","WATCH":"🔶","SIGNIFICANT":"🟡","MAJOR":"🔴","EXTREME":"🚨"}.get(flow_cls,"⚪")
        flow_pct   = sig.flow_impact_pct or 0
        liq        = sig.liquidity_usd or 0
        mcap       = sig.market_cap_usd or 0
        liq_ratio  = sig.liq_mcap_ratio or 0
        price_chg  = sig.price_change_24h or 0
        confirmed  = sig.price_confirmed
        projection = sig.projection or ""

        liq_str  = f"${liq/1e6:.2f}M" if liq >= 1e6 else f"${liq/1e3:.0f}K"
        mcap_str = f"${mcap/1e6:.1f}M" if mcap >= 1e6 else f"${mcap/1e3:.0f}K"
        flow_usd = liq * flow_pct / 100
        flow_str = f"${flow_usd/1e6:.2f}M" if flow_usd >= 1e6 else f"${flow_usd/1e3:.0f}K"

        # ── Header ────────────────────────────────────────────────────
        if is_short:
            header = f"📉 *SHORT — ${sig.symbol}* | Score `{sig.score}/100` {bar}"
            subhead = f"_SM wallets distributing — selling pressure detected_"
        else:
            header = f"🚀 *LONG — ${sig.symbol}* | Score `{sig.score}/100` {bar}"
            subhead = f"_SM wallets accumulating — buying pressure detected_"

        # ── Valuation block ───────────────────────────────────────────
        confirm_txt = "✅ CONFIRMED" if confirmed else "⚠️ UNCONFIRMED"
        price_dir   = f"+{price_chg:.1f}%" if price_chg >= 0 else f"{price_chg:.1f}%"

        valuation_block = f"""
━━ *VALUATION ANALYSIS* ━━
{flow_emoji} *Flow Impact:* `{flow_pct:.1f}%` of liquidity `({flow_str})` — *{flow_cls}*
💧 *Liquidity:* `{liq_str}` | MCap `{mcap_str}` | Ratio `{liq_ratio:.2f}%`
📈 *24h Price:* `{price_dir}` — {confirm_txt}
🔮 *Pattern:* _{projection}_"""

        # ── Token identity ────────────────────────────────────────────
        addr_block = ""
        if sig.token_address:
            addr = sig.token_address
            short_addr = addr[:8] + "..." + addr[-6:]
            addr_block = f"\n📋 *Contract:* `{addr}`"
        links = ""
        if sig.coingecko_url:
            links += f"\n🦎 [CoinGecko]({sig.coingecko_url})"
        if sig.explorer_url:
            links += f" | 🔍 [Explorer]({sig.explorer_url})"

        # ── Smart Money block ─────────────────────────────────────────
        sm_wallets = sig.smart_money_buyers or 0
        netflow    = sig.sm_netflow_usd or 0
        if is_short:
            sm_action = f"🐋 *{sm_wallets} SM wallet{'s' if sm_wallets!=1 else ''}* SELLING"
            sm_flow   = f"📤 *Outflow:* `${abs(netflow):,.0f}` in 24h"
        else:
            sm_action = f"🐋 *{sm_wallets} SM wallet{'s' if sm_wallets!=1 else ''}* BUYING"
            sm_flow   = f"📥 *Inflow:* `+${netflow:,.0f}` in 24h"

        sm_block = f"""
━━ *SMART MONEY* ━━
{sm_action}
{sm_flow}"""

        # ── Trade levels ──────────────────────────────────────────────
        def fmt_price(p):
            if p == 0: return "0"
            if p >= 1:     return f"{p:,.4f}"
            if p >= 0.01:  return f"{p:.6f}"
            if p >= 0.0001: return f"{p:.8f}"
            return f"{p:.10f}"

        if is_short:
            t1_pct = round((sig.target_1/sig.entry - 1)*100, 1) if sig.entry else -8
            t2_pct = round((sig.target_2/sig.entry - 1)*100, 1) if sig.entry else -15
            sl_pct = round((sig.stop_loss/sig.entry - 1)*100, 1) if sig.entry else 3
            trade_block = f"""
━━ *SHORT TRADE LEVELS* ━━
📤 *Entry:*     `${fmt_price(sig.entry)}` — short here
🎯 *Target 1:*  `${fmt_price(sig.target_1)}` `({t1_pct:.1f}%)` — partial profit
🎯 *Target 2:*  `${fmt_price(sig.target_2)}` `({t2_pct:.1f}%)` — full target
🛑 *Stop Loss:* `${fmt_price(sig.stop_loss)}` `(+{sl_pct:.1f}%)` — exit if wrong
⏱ *Window:* `{sig.timeframe}`
{risk_e} *Risk:* `{sig.risk}` — use trailing stop
_Platforms: {'Binance Futures · Bybit Perps · OKX · Hyperliquid' if sig.chain.lower() in ('ethereum','arbitrum','bnb','base') and sig.market_cap_usd > 50_000_000 else 'DEX only — check if listed on Bybit/OKX before shorting'}_"""
        else:
            t1_pct = round((sig.target_1/sig.entry - 1)*100, 1) if sig.entry else 7
            t2_pct = round((sig.target_2/sig.entry - 1)*100, 1) if sig.entry else 15
            sl_pct = round((sig.stop_loss/sig.entry - 1)*100, 1) if sig.entry else -3
            trade_block = f"""
━━ *LONG TRADE LEVELS* ━━
📥 *Entry:*     `${fmt_price(sig.entry)}` — buy here
🏆 *Target 1:*  `${fmt_price(sig.target_1)}` `(+{t1_pct:.1f}%)` — partial profit
🏆 *Target 2:*  `${fmt_price(sig.target_2)}` `(+{t2_pct:.1f}%)` — full target
🛑 *Stop Loss:* `${fmt_price(sig.stop_loss)}` `({sl_pct:.1f}%)` — exit if wrong
⏱ *Window:* `{sig.timeframe}`
{risk_e} *Risk:* `{sig.risk}` — use trailing stop
_Verify contract before buying. DYOR._"""

        # ── Chain ─────────────────────────────────────────────────────
        chain_str = f"⛓ Chain: `{sig.chain.upper()}` | `${sig.symbol}`"

        # ── Assemble ──────────────────────────────────────────────────
        msg = f"""{header}
{subhead}
{chain_str}{addr_block}{links}
{valuation_block}
{sm_block}
{trade_block}

🕐 _{datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}_
_⚠️ Signal only. Not financial advice. DYOR._"""

        return msg

    async def send(self, session: aiohttp.ClientSession, message: str):
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("\n" + "═"*65)
            print(message)
            print("═"*65 + "\n")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            async with session.post(
                url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
                timeout=10
            ) as r:
                if r.status == 200:
                    log.info("   ✅ Telegram alert sent!")
                else:
                    log.error(f"   Telegram error {r.status}: {await r.text()}")
        except Exception as e:
            log.error(f"   Telegram failed: {e}")


# ─────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────
class CryptoSignalScannerV2:

    def __init__(self):
        # ── Build key pools from .env ─────────────────────────────────
        # Keys are comma-separated in .env: NANSEN_API_KEYS=key1,key2,key3
        # Falls back to singular NANSEN_API_KEY for backward compatibility
        nansen_keys    = _load_keys("NANSEN_API_KEYS",    "NANSEN_API_KEY")
        etherscan_keys = _load_keys("ETHERSCAN_API_KEYS", "ETHERSCAN_API_KEY")
        arkham_keys    = _load_keys("ARKHAM_API_KEYS",    "ARKHAM_API_KEY")

        # ── Create rotators ────────────────────────────────────────────
        self._nansen_rot    = KeyRotator("Nansen",    nansen_keys)
        self._etherscan_rot = KeyRotator("Etherscan", etherscan_keys)

        # ── Inject rotators into layers ────────────────────────────────
        self.nansen   = NansenLayer(self._nansen_rot)
        self.arkham   = EtherscanLayer(self._etherscan_rot)
        self.lc       = CryptoCompareSocialLayer()
        self.san_fg       = FearGreedLayer()
        self.cg           = CoinGeckoLayer()
        self.arkham_intel = ArkhamIntelLayer(arkham_keys)
        self.telegram = TelegramAlerter()
        self.scan_no  = 0
        # Dynamic MAX_RAW — only count layers that have active keys
        nansen_max    = 50   # Layer 1 (30) + Layer 2 (20) — always Nansen
        etherscan_max = 20 if self._etherscan_rot.has_keys() else 10
        cc_max        = 15 if CRYPTOCOMPARE_API_KEY else 0
        fg_max        = 10   # Fear & Greed — always free
        cg_max        = 5    # CoinGecko — always free
        self.MAX_RAW  = nansen_max + etherscan_max + cc_max + fg_max + cg_max

    CHAINS = ["ethereum", "solana", "base", "arbitrum", "bnb"]

    # Block explorer URLs for contract address verification
    EXPLORERS = {
        "ethereum":  "https://etherscan.io/token/",
        "solana":    "https://solscan.io/token/",
        "base":      "https://basescan.org/token/",
        "arbitrum":  "https://arbiscan.io/token/",
        "bnb":       "https://bscscan.com/token/",
        "polygon":   "https://polygonscan.com/token/",
        "optimism":  "https://optimistic.etherscan.io/token/",
        "avalanche": "https://snowtrace.io/token/",
    }

    def _write_signals_json(self, candidates: list, threshold: int = None):
        """Write signals to templates/signals.json so dashboard.html can read them."""
        try:
            data = {
                "scan_count":    self.scan_no,
                "last_scan":     datetime.utcnow().isoformat() + "Z",
                "threshold":     threshold if threshold is not None else SIGNAL_THRESHOLD,
                "max_raw":       self.MAX_RAW,
                "layer_status": {
                    "nansen":        self._nansen_rot.has_keys(),
                    "etherscan":     self._etherscan_rot.has_keys(),
                    "cryptocompare": bool(CRYPTOCOMPARE_API_KEY),
                    "fear_greed":    True,
                    "coingecko":     True,
                    "arkham":        self.arkham_intel.has_keys(),
                },
                "signals": []
            }
            for sig in candidates[:20]:
                data["signals"].append({
                    "symbol":          sig.symbol,
                    "name":            sig.name,
                    "chain":           sig.chain,
                    "price":           sig.price,
                    "score":           sig.score,
                    "risk":            sig.risk,
                    "timeframe":       sig.timeframe,
                    "entry":           sig.entry,
                    "target1":         sig.target_1,
                    "target2":         sig.target_2,
                    "sl":              sig.stop_loss,
                    "sm_buyers":       sig.smart_money_buyers,
                    "sm_netflow":      sig.sm_netflow_usd,
                    "arkham_entities": sig.arkham_entity_count,
                    "token_address":   sig.token_address,
                    "coingecko_url":   sig.coingecko_url,
                    "explorer_url":    sig.explorer_url,
                    "layers":          [l for l in [
                                            "nansen"    if sig.smart_money_buyers > 0 else None,
                                            "etherscan" if sig.arkham_entity_count >= 0 else None,
                                            "cg",
                                        ] if l],
                    "breakdown":       [{"t": n, "cls": "ok" if "✅" in n else "med" if "🔶" in n else "bad" if "🚨" in n or "OUTFLOW" in n else "neu"} for n in sig.breakdown],
                    "nansen_traders":  sig.smart_money_buyers,
                    "nansen_netflow":  sig.sm_netflow_usd,
                    "signal_type":     sig.signal_type,
                    "timestamp":       datetime.utcnow().isoformat() + "Z",
                    "liquidity_usd":         sig.liquidity_usd,
                    "market_cap_usd":        sig.market_cap_usd,
                    "liq_mcap_ratio":        sig.liq_mcap_ratio,
                    "flow_impact_pct":       sig.flow_impact_pct,
                    "flow_classification":   sig.flow_classification,
                    "price_change_24h":      sig.price_change_24h,
                    "price_confirmed":       sig.price_confirmed,
                    "projection":            sig.projection,
                })
            SIGNALS_JSON.parent.mkdir(parents=True, exist_ok=True)
            SIGNALS_JSON.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"   Could not write signals.json: {e}")

    def _trade_levels(self, price: float, risk: str) -> tuple[float, float, float]:
        """LONG trade levels — targets above entry, stop loss below."""
        t1 = price * (1.05 if risk == "LOW" else 1.07 if risk == "MEDIUM" else 1.10)
        t2 = price * (1.10 if risk == "LOW" else 1.15 if risk == "MEDIUM" else 1.20)
        sl = price * (0.97 if risk != "HIGH" else 0.95)
        return t1, t2, sl

    def _trade_levels_short(self, price: float, risk: str) -> tuple[float, float, float]:
        """SHORT trade levels — targets BELOW entry, stop loss ABOVE entry."""
        t1 = price * (0.92 if risk == "LOW" else 0.90 if risk == "MEDIUM" else 0.87)
        t2 = price * (0.85 if risk == "LOW" else 0.82 if risk == "MEDIUM" else 0.78)
        sl = price * (1.03 if risk != "HIGH" else 1.05)   # stop loss ABOVE entry
        return t1, t2, sl

    def _risk_and_timeframe(self, score: int, sm_buyers: int, netflow: float) -> tuple[str, str]:
        if score >= 85 and sm_buyers >= 10 and netflow > 100_000:
            return "LOW", "2–8 hours"
        if score >= 75 and sm_buyers >= 5:
            return "LOW", "1–4 hours"
        if score >= 65:
            return "MEDIUM", "1–3 hours"
        return "HIGH", "30–90 minutes"

    async def run_scan(self):
        self.scan_no += 1
        # Reset rotators so previously rate-limited keys can be retried
        self._nansen_rot.reset()
        self._etherscan_rot.reset()
        log.info(f"── Scan #{self.scan_no} ─────────────────────────────────────────────")

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:

            # ── Fetch all data concurrently ──────────────────────────
            sm_tokens_task   = self.nansen.screen_smart_money_tokens(session, self.CHAINS)
            sm_shorts_task   = self.nansen.screen_short_signals(session, self.CHAINS)
            cg_markets_task  = self.cg.fetch_markets(session)
            cg_trending_task = self.cg.fetch_trending(session)
            cc_data_task     = self.lc.fetch(session)
            fg_data_task     = self.san_fg.fetch(session)

            sm_tokens, sm_shorts, cg_markets, trending, lc_all, fg_data = await asyncio.gather(
                sm_tokens_task, sm_shorts_task, cg_markets_task,
                cg_trending_task, cc_data_task, fg_data_task
            )

            # Dynamic threshold based on current Fear & Greed
            active_threshold = dynamic_threshold(fg_data.get("value", 50) if fg_data else 50)
            log.info(f"   Dynamic threshold: {active_threshold}/100 (Fear&Greed={fg_data.get('value',50) if fg_data else '?'})")

            log.info(
                f"   Nansen LONG: {len(sm_tokens)} tokens | "
                f"SHORT: {len(sm_shorts)} tokens | "
                f"CG: {len(cg_markets)} coins | Trending: {len(trending)}"
            )

            # ── If no Nansen key, fall back to CoinGecko high-volume coins ──
            if not sm_tokens and not self._nansen_rot.has_keys():
                log.warning("   ⚠️  No Nansen key — scanning CoinGecko volume anomalies as fallback")
                # Build a list of high vol/mcap ratio coins as candidates
                sm_tokens = [
                    {"symbol": sym, "name": d.get("name", sym),
                     "chain": "ethereum", "token_address": "",
                     "smart_money_count": 0, "volume_usd": d.get("total_volume",0)}
                    for sym, d in cg_markets.items()
                    if (d.get("total_volume",0) or 0) / max(d.get("market_cap",1) or 1, 1) > 0.2
                ][:30]

            candidates: list[TokenSignal] = []
            credits_used = 0

            for token in sm_tokens:

                sym       = (token.get("symbol") or "").upper()
                name      = token.get("name", sym)
                chain     = token.get("chain", "ethereum")
                tok_addr  = token.get("token_address", token.get("address", ""))
                price_raw = token.get("price", token.get("price_usd", 0)) or 0

                # Fill price from CoinGecko if Nansen didn't return it
                cg_coin  = cg_markets.get(sym, {})
                price    = price_raw or (cg_coin.get("current_price") or 0)
                if price <= 0:
                    continue

                # ── Score Layer 1: Nansen screener ───────────────────
                nm_s_pts, nm_s_notes, token_valuation = self.nansen.score_screener(token)

                # ── Score Layer 2: Nansen Smart Money netflow ─────────
                nm_nf_pts, nm_nf_notes, net_flow = 0, [], 0.0
                if tok_addr and self.nansen.rotator.has_keys():
                    nf_data = await self.nansen.get_smart_money_netflow(session, tok_addr, chain)
                    nm_nf_pts, nm_nf_notes, net_flow = self.nansen.score_netflow(nf_data)
                    credits_used += 5  # Netflow = 5 credits

                # ── Score Layer 3: Etherscan contract safety check ────
                # Get contract address from CoinGecko if Nansen didn't provide one
                cg_id = (cg_coin.get("id", "") or "").lower() if cg_coin else ""
                if not tok_addr and cg_id:
                    platforms = await self.cg.fetch_coin_detail(session, cg_id)
                    tok_addr  = self.cg.pick_contract(platforms, chain)

                # Run Etherscan safety check
                etherscan_info = await self.arkham.fetch_token_info(session, tok_addr, chain)
                ark_pts, ark_notes, is_suspicious = self.arkham.score(etherscan_info, chain, tok_addr)
                entity_count = 0

                # CRITICAL: Unverified contract = skip signal entirely
                if is_suspicious:
                    log.warning(f"   🚨 {sym} SKIPPED — unverified contract on Etherscan")
                    continue

                # ── Score Layer 7: Arkham entity intelligence ─────────
                ark_i_pts, ark_i_notes = 0, []
                if tok_addr and self.arkham_intel.has_keys():
                    af, ah = await asyncio.gather(
                        self.arkham_intel.get_token_flow(session, chain, tok_addr),
                        self.arkham_intel.get_token_holders(session, chain, tok_addr)
                    )
                    ark_i_pts, ark_i_notes = self.arkham_intel.score(af, ah)

                # ── Score Layer 4: LunarCrush social ─────────────────
                lc_pts, lc_notes = self.lc.score(sym, lc_all.get(sym, {}))

                # ── Score Layer 5: Fear & Greed (no key needed) ──────
                san_pts, san_notes = self.san_fg.score(fg_data)

                # ── Score Layer 6: CoinGecko volume confirmation ──────
                cg_pts, cg_notes = self.cg.score(cg_coin if cg_coin else None, sym in trending)

                # ── Combine ───────────────────────────────────────────
                raw_total = nm_s_pts + nm_nf_pts + ark_pts + lc_pts + san_pts + cg_pts + ark_i_pts
                norm = int((raw_total / self.MAX_RAW) * 100)

                if norm < active_threshold:
                    continue

                sm_count = token.get("nof_traders", token.get("smart_money_count", token.get("nof_smart_money_traders", 0))) or 0
                risk, tf = self._risk_and_timeframe(norm, sm_count, net_flow)
                t1, t2, sl = self._trade_levels(price, risk)

                all_notes = nm_s_notes + nm_nf_notes + ark_notes + lc_notes + san_notes + cg_notes + ark_i_notes

                # ── Enrich with CoinGecko ID + explorer URL ───────────
                cg_id  = cg_coin.get("id", "").lower() if cg_coin else ""
                cg_url = f"https://www.coingecko.com/en/coins/{cg_id}" if cg_id else ""
                exp_base = self.EXPLORERS.get(chain, "")
                exp_url  = (exp_base + tok_addr) if (exp_base and tok_addr) else ""

                candidates.append(TokenSignal(
                    symbol=sym, name=name, chain=chain, price=price,
                    token_address=tok_addr,
                    coingecko_id=cg_id,
                    coingecko_url=cg_url,
                    explorer_url=exp_url,
                    score=norm,
                    breakdown=all_notes, timeframe=tf,
                    entry=price, target_1=t1, target_2=t2, stop_loss=sl,
                    risk=risk, smart_money_buyers=sm_count,
                    sm_netflow_usd=net_flow, arkham_entity_count=entity_count,
                    liquidity_usd=token_valuation.get("liquidity_usd", 0),
                    market_cap_usd=token_valuation.get("market_cap_usd", 0),
                    liq_mcap_ratio=token_valuation.get("liq_mcap_ratio", 0),
                    flow_impact_pct=token_valuation.get("flow_pct", 0),
                    flow_classification=token_valuation.get("flow_classification", "NOISE"),
                    price_change_24h=token_valuation.get("price_change_24h", 0),
                    price_confirmed=token_valuation.get("price_confirmed", False),
                    projection=token_valuation.get("projection", ""),
                ))

            # ── Process SHORT signals ────────────────────────────────
            for token in sm_shorts:
                sym       = (token.get("token_symbol", token.get("symbol", "")) or "").upper()
                name      = token.get("token_name", token.get("name", sym))
                chain     = token.get("chain", "ethereum")
                tok_addr  = token.get("token_address", token.get("address", ""))
                price_raw = token.get("price_usd", token.get("price", 0)) or 0

                cg_coin   = cg_markets.get(sym, {})
                price     = price_raw or (cg_coin.get("current_price") or 0)
                if price <= 0:
                    continue

                # Skip if already in LONG candidates (conflicting signals)
                if any(c.symbol == sym for c in candidates):
                    log.debug(f"   Skipping SHORT {sym} — already a LONG candidate")
                    continue

                # Score the short signal
                sh_pts, sh_notes, token_valuation = self.nansen.score_short(token)

                # Etherscan safety check (scam tokens should not be shorted either)
                etherscan_info = await self.arkham.fetch_token_info(session, tok_addr, chain)
                ark_pts, ark_notes, is_suspicious = self.arkham.score(etherscan_info, chain, tok_addr)
                if is_suspicious:
                    continue

                # Fear & Greed — for shorts, GREED is actually good (overheated = dump incoming)
                fg_val = fg_data["value"] if fg_data else 50
                fg_pts = 0
                fg_notes = []
                if fg_val >= 75:
                    fg_pts = 10; fg_notes.append(f"📉 [Fear&Greed] EXTREME GREED {fg_val}/100 — market overheated, dump likely")
                elif fg_val >= 60:
                    fg_pts = 7;  fg_notes.append(f"📉 [Fear&Greed] Greed {fg_val}/100 — elevated risk of correction")
                elif fg_val >= 45:
                    fg_pts = 4;  fg_notes.append(f"📊 [Fear&Greed] Neutral {fg_val}/100 — uncertain conditions for short")
                else:
                    fg_pts = 1;  fg_notes.append(f"⚠️  [Fear&Greed] Fear {fg_val}/100 — caution shorting in fear market")

                # CoinGecko — for shorts check if price dropping
                cg_pts_s = 0; cg_notes_s = []
                if cg_coin:
                    chg_24h = cg_coin.get("price_change_percentage_24h", 0) or 0
                    if chg_24h <= -10:
                        cg_pts_s = 5; cg_notes_s.append(f"📉 [CoinGecko] -{abs(chg_24h):.1f}% in 24h — confirmed downtrend")
                    elif chg_24h <= -5:
                        cg_pts_s = 3; cg_notes_s.append(f"📉 [CoinGecko] -{abs(chg_24h):.1f}% in 24h — price declining")
                    elif chg_24h <= 0:
                        cg_pts_s = 1; cg_notes_s.append(f"📊 [CoinGecko] {chg_24h:.1f}% in 24h — slight decline")

                # Combine short score
                raw_short = sh_pts + ark_pts + fg_pts + cg_pts_s
                max_short = 30 + (20 if self._etherscan_rot.has_keys() else 10) + 10 + 5
                norm_short = int((raw_short / max_short) * 100)

                if norm_short < active_threshold:
                    continue

                sm_count = token.get("nof_traders", 0) or 0
                netflow  = token.get("netflow", 0) or 0
                risk, tf = self._risk_and_timeframe(norm_short, sm_count, netflow)
                t1, t2, sl = self._trade_levels_short(price, risk)

                all_notes = sh_notes + ark_notes + fg_notes + cg_notes_s

                cg_id   = cg_coin.get("id", "").lower() if cg_coin else ""
                cg_url  = f"https://www.coingecko.com/en/coins/{cg_id}" if cg_id else ""
                exp_url = (self.EXPLORERS.get(chain, "") + tok_addr) if tok_addr else ""

                candidates.append(TokenSignal(
                    symbol=sym, name=name, chain=chain, price=price,
                    token_address=tok_addr,
                    coingecko_id=cg_id,
                    coingecko_url=cg_url,
                    explorer_url=exp_url,
                    score=norm_short,
                    signal_type="SHORT",
                    breakdown=all_notes, timeframe=tf,
                    entry=price,
                    target_1=t1,   # BELOW entry for shorts
                    target_2=t2,   # further BELOW for shorts
                    stop_loss=sl,  # ABOVE entry for shorts
                    risk=risk,
                    smart_money_buyers=sm_count,
                    sm_netflow_usd=netflow,
                    arkham_entity_count=0,
                    liquidity_usd=token_valuation.get("liquidity_usd", 0),
                    market_cap_usd=token_valuation.get("market_cap_usd", 0),
                    liq_mcap_ratio=token_valuation.get("liq_mcap_ratio", 0),
                    flow_impact_pct=token_valuation.get("flow_pct", 0),
                    flow_classification=token_valuation.get("flow_classification", "NOISE"),
                    price_change_24h=token_valuation.get("price_change_24h", 0),
                    price_confirmed=token_valuation.get("price_confirmed", False),
                    projection=token_valuation.get("projection", ""),
                ))

            # ── Sort + Send ───────────────────────────────────────────
            candidates.sort(key=lambda x: x.score, reverse=True)
            sent = 0

            for sig in candidates[:MAX_ALERTS_PER_SCAN]:
                if not self.telegram.can_alert(sig.symbol):
                    log.info(f"   ⏳ {sig.symbol} in cooldown")
                    continue
                msg = self.telegram.build_message(sig)
                await self.telegram.send(session, msg)
                self.telegram.mark_alerted(sig.symbol)
                sent += 1
                log.info(f"   🚨 SIGNAL: {sig.symbol} | {sig.score}/100 | {sig.confidence_label if hasattr(sig,'confidence_label') else sig.risk} | {sig.timeframe}")

            if sent == 0:
                best = candidates[0].score if candidates else 0
                log.info(f"   😴 No signals this scan. Best: {best}/100  (Nansen credits used: {credits_used})")
            else:
                log.info(f"   ✅ {sent} alert(s) sent. Nansen credits used this scan: {credits_used}")

            # ── Write signals.json for dashboard ─────────────────────
            self._write_signals_json(candidates, active_threshold)


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────
async def main():
    scanner = CryptoSignalScannerV2()

    print("\n" + "╔" + "═"*63 + "╗")
    print("║        CryptoSignal Pro v2 — Real Nansen + Arkham             ║")
    print("╚" + "═"*63 + "╝")
    print(f"  Threshold : {SIGNAL_THRESHOLD}/100")
    print(f"  Interval  : every {SCAN_INTERVAL_SEC//60} minutes")
    print(f"  Nansen        : {scanner._nansen_rot.status()}")
    print(f"  Etherscan     : {scanner._etherscan_rot.status()}")
    print(f"  CryptoCompare : {'✅ Social layer connected' if CRYPTOCOMPARE_API_KEY else '⚠️  Add free key at cryptocompare.com'}")
    print(f"  Fear&Greed    : ✅ Free, no key needed (alternative.me)")
    print(f"  CoinGecko     : ✅ Free, no key needed")
    print(f"  Telegram      : {'✅ Alerts enabled' if TELEGRAM_BOT_TOKEN else '⚠️  Not set — alerts go to console'}")
    print(f"  Arkham        : ⚠️  No API response — replaced by Etherscan")
    print()

    if not scanner._nansen_rot.has_keys():
        print("  ℹ️  WITHOUT Nansen: scanner will use CoinGecko volume anomalies as fallback.")
        print("     This gives UNCONFIRMED signals only — use as watchlist, not buy triggers.")
        print()

    while True:
        try:
            await scanner.run_scan()
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        log.info(f"   ⏳ Next scan in {SCAN_INTERVAL_SEC//60} min…\n")
        await asyncio.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
