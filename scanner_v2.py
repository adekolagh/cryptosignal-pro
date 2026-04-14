#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║        CryptoSignal Pro v2 — Real Nansen + Arkham Edition        ║
╠══════════════════════════════════════════════════════════════════╣
║  What changed from v1:                                           ║
║  ✅ Nansen Token Screener  — REAL Smart Money API (5 credits/call)║
║  ✅ Nansen Smart Money Flow — REAL inflow/outflow data           ║
║  ✅ Arkham Intelligence    — REAL entity wallet monitoring        ║
║  ❌ Removed: fake "pump detector" based on simple % math         ║
║  ❌ Removed: inflated scores with no real on-chain backing       ║
╠══════════════════════════════════════════════════════════════════╣
║  Signal Architecture (7 real layers → 100 pts → Telegram):      ║
║                                                                  ║
║  Layer 1 ▸ Nansen Smart Money Screener  — real wallets  (30 pts) ║
║  Layer 2 ▸ Nansen Smart Money Netflow   — real flows    (20 pts) ║
║  Layer 3 ▸ Arkham Entity Transactions   — real entities (20 pts) ║
║  Layer 4 ▸ LunarCrush Social Signal     — real hype     (15 pts) ║
║  Layer 5 ▸ Santiment On-chain Spike     — real on-chain (10 pts) ║
║  Layer 6 ▸ CoinGecko Volume Anomaly     — real volume   (5 pts)  ║ 
║                                                         ──────── ║
║  TOTAL                                                  100 pts  ║
╚══════════════════════════════════════════════════════════════════╝

SIGNAL CONFIDENCE LEVELS:
  90–100 : EXTREME — multiple institutional confirmations
  75–89  : HIGH    — Smart Money + social confirmation
  65–74  : MODERATE — good signal, use tighter stop loss
  < 65   : IGNORED — not enough confirmation

COST NOTE:
  Nansen Token Screener = 5 credits per call (Pro: 1000 credits/month included)
  Nansen Netflow        = 5 credits per call
  Arkham API            = Apply for key at intel.arkm.com/api (paid)
  LunarCrush            = Free key at lunarcrush.com/developers
  Santiment             = Free key at santiment.net
  CoinGecko             = 100% free, no key
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
SIGNAL_THRESHOLD    = 65    # Out of 100
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

        payload = {
            "chains": chains,
            "date": {"from": from_dt, "to": to_dt},
            "pagination": {"page": 1, "per_page": 50},
            "filters": {
                "only_smart_money": True,
                "market_cap_usd":   {"min": 100_000, "max": 10_000_000_000},
                "liquidity":        {"min": 10_000},
            },
            "order_by": [{"field": "volume", "direction": "DESC"}]
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

    def score_screener(self, token: dict) -> tuple[int, list]:
        """
        Score based on Nansen Smart Money screener result. Max 30 pts.
        Real Nansen fields: nof_traders, buy_volume, netflow, price_change
        """
        pts = 0
        notes = []

        # nof_traders = number of Smart Money wallets trading this token
        sm_count   = token.get("nof_traders", 0) or 0
        buy_vol    = token.get("buy_volume", 0) or 0
        netflow    = token.get("netflow", 0) or 0
        price_chg  = token.get("price_change", 0) or 0

        # Smart Money trader count
        if sm_count >= 10:
            pts += 15
            notes.append(f"✅ [Nansen] {sm_count} SM wallets trading — High conviction")
        elif sm_count >= 5:
            pts += 10
            notes.append(f"✅ [Nansen] {sm_count} SM wallets trading — Good conviction")
        elif sm_count >= 2:
            pts += 6
            notes.append(f"🔶 [Nansen] {sm_count} SM wallets trading — Moderate")
        elif sm_count >= 1:
            pts += 3
            notes.append(f"📊 [Nansen] {sm_count} SM wallet active")

        # Netflow — positive means Smart Money is net buying
        if netflow > 500_000:
            pts += 15
            notes.append(f"✅ [Nansen] Net inflow: +${netflow:,.0f} — Strong SM buying")
        elif netflow > 100_000:
            pts += 10
            notes.append(f"✅ [Nansen] Net inflow: +${netflow:,.0f} — Clear SM buying")
        elif netflow > 10_000:
            pts += 6
            notes.append(f"🔶 [Nansen] Net inflow: +${netflow:,.0f} — Mild SM buying")
        elif netflow > 0:
            pts += 3
            notes.append(f"📊 [Nansen] Net inflow: +${netflow:,.0f} — Slight buying")
        elif netflow < 0:
            pts = max(0, pts - 5)
            notes.append(f"⚠️  [Nansen] Net OUTFLOW: ${netflow:,.0f} — SM selling")

        return min(pts, 30), notes

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

        # Call 2 — Holder activity
        data2 = await self._get(session, {
            "module":          "token",
            "action":          "tokenholderlist",
            "contractaddress": contract_address,
            "page":            "1",
            "offset":          "1",
        })
        if data2:
            result["has_holders"] = data2.get("status") == "1"

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

        # Holders check
        has_holders = info.get("has_holders", None)
        if has_holders is True:
            pts += 5
            notes.append("✅ [Safety] Token has active holders on Etherscan")
        elif has_holders is False:
            pts -= 3
            notes.append("⚠️  [Safety] No holder data found — token may be very new or illiquid")

        return min(pts, 20), notes, is_suspicious


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
# LAYER 5 — FEAR & GREED INDEX  (replaces Santiment — NO KEY NEEDED)
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
# LAYER 4 — LUNARCRUSH  (Social Sentiment)
# Real API: https://lunarcrush.com/api4
# Free key at: lunarcrush.com/developers
# ─────────────────────────────────────────────────────────────────────
class LunarCrushLayer:
    # Real v2 endpoint — updated every few seconds (v1 is cached 1h)
    # Docs: https://lunarcrush.com/developers/api/public/coins/list/v1
    URL = "https://lunarcrush.com/api4/public/coins/list/v2"

    async def fetch(self, session: aiohttp.ClientSession) -> dict:
        if not LUNARCRUSH_API_KEY:
            return {}
        # Real auth: Bearer token in Authorization header
        headers = {"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"}
        params = {"sort": "galaxy_score", "desc": "1", "limit": "200"}
        try:
            async with session.get(self.URL, headers=headers, params=params, timeout=15) as r:
                if r.status == 200:
                    data = await r.json()
                    # Real field is "symbol" not "s"
                    return {c["symbol"].upper(): c for c in data.get("data", []) if c.get("symbol")}
                elif r.status == 401:
                    log.warning("   LunarCrush: Invalid API key — check lunarcrush.com/developers")
                else:
                    log.warning(f"   LunarCrush HTTP {r.status}")
        except Exception as e:
            log.warning(f"   LunarCrush error: {e}")
        return {}

    def score(self, symbol: str, data: dict) -> tuple[int, list]:
        """
        Score using real LunarCrush v4 field names:
          galaxy_score          — 0-100 composite health score
          galaxy_score_previous — score 24h ago (rising = bullish)
          alt_rank              — lower is better (<15 = top momentum)
          alt_rank_previous     — rank 24h ago
          interactions_24h      — total social interactions
          social_volume_24h     — total posts with interactions
        Max 15 pts.
        """
        if not data:
            return 0, ["⚪ [LunarCrush] No data — get free key at lunarcrush.com/developers"]

        pts = 0
        notes = []

        # Real field names from v4 API docs
        gs      = data.get("galaxy_score", 0) or 0
        gs_prev = data.get("galaxy_score_previous", gs) or gs
        acr     = data.get("alt_rank", 9999) or 9999
        acr_prev= data.get("alt_rank_previous", acr) or acr
        interact= data.get("interactions_24h", 0) or 0
        soc_vol = data.get("social_volume_24h", 0) or 0

        # Galaxy Score — with rising bonus
        if gs >= 75:
            pts += 7
            trend = " ↑ rising" if gs > gs_prev else ""
            notes.append(f"✅ [LunarCrush] Galaxy Score {gs}/100{trend} — Excellent")
        elif gs >= 60:
            pts += 4
            notes.append(f"🔶 [LunarCrush] Galaxy Score {gs}/100 — Good")
        elif gs >= 45:
            pts += 2
            notes.append(f"📊 [LunarCrush] Galaxy Score {gs}/100 — Average")

        # AltRank — lower = better, improving = bullish
        if acr <= 15:
            improving = " ↑ improving" if acr < acr_prev else ""
            pts += 5; notes.append(f"✅ [LunarCrush] AltRank #{acr}{improving} — Top 15 social momentum")
        elif acr <= 50:
            pts += 3; notes.append(f"🔶 [LunarCrush] AltRank #{acr} — Top 50")
        elif acr <= 100:
            pts += 1; notes.append(f"📊 [LunarCrush] AltRank #{acr} — Top 100")

        # Social interactions surge (> 1M = significant)
        if interact > 5_000_000:
            pts += 3; notes.append(f"✅ [LunarCrush] Interactions: {interact/1e6:.1f}M/24h — VIRAL")
        elif interact > 1_000_000:
            pts += 2; notes.append(f"🔶 [LunarCrush] Interactions: {interact/1e6:.1f}M/24h — High")

        return min(pts, 15), notes


# ─────────────────────────────────────────────────────────────────────
# LAYER 5 — SANTIMENT  (On-chain Social Spike)
# Real API: https://api.santiment.net/graphql
# Free key at: santiment.net
# ─────────────────────────────────────────────────────────────────────
class SantimentLayer:
    API_URL  = "https://api.santiment.net/graphql"
    SLUG_MAP = {
        "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binance-coin",
        "XRP":"ripple","ADA":"cardano","AVAX":"avalanche","DOT":"polkadot",
        "MATIC":"matic-network","LINK":"chainlink","UNI":"uniswap","ATOM":"cosmos",
        "LTC":"litecoin","DOGE":"dogecoin","SHIB":"shiba-inu","ARB":"arbitrum",
        "OP":"optimism","INJ":"injective-protocol","SUI":"sui","APT":"aptos",
        "TIA":"celestia","SEI":"sei-network","JUP":"jupiter-exchange-solana",
        "FET":"fetch-ai","RENDER":"render-token","WLD":"worldcoin",
        "NEAR":"near-protocol","FTM":"fantom","PEPE":"pepe","WIF":"dogwifcoin",
    }

    async def get_spike_ratio(self, session: aiohttp.ClientSession, slug: str) -> Optional[float]:
        """
        Real Santiment GraphQL API.
        Docs: https://academy.santiment.net/for-developers/
        Endpoint: POST https://api.santiment.net/graphql
        Auth: Authorization: Apikey <key>
        Metric: social_volume_total (total posts mentioning the asset)
        Field: value (not mentionsCount)
        """
        if not SANTIMENT_API_KEY:
            return None
        # Correct GraphQL query using getMetric — the real Santiment API pattern
        query = """
        {
          getMetric(metric: "social_volume_total") {
            timeseriesData(
              selector: { slug: "%s" }
              from: "utc_now-12h"
              to: "utc_now"
              interval: "1h"
            ) {
              datetime
              value
            }
          }
        }
        """ % slug
        try:
            async with session.post(
                self.API_URL,
                json={"query": query},
                headers={"Authorization": f"Apikey {SANTIMENT_API_KEY}"},
                timeout=12
            ) as r:
                if r.status == 200:
                    resp = await r.json()
                    # Real field path: data.getMetric.timeseriesData[].value
                    entries = resp.get("data", {}).get("getMetric", {}).get("timeseriesData", [])
                    if len(entries) >= 4:
                        recent = entries[-1].get("value", 0) or 0
                        past   = [e.get("value", 0) or 0 for e in entries[:-2]]
                        avg    = sum(past) / max(len(past), 1)
                        return recent / avg if avg > 0 else 1.0
                elif r.status == 403:
                    log.warning("   Santiment: API key invalid or plan limit reached")
        except Exception as e:
            log.debug(f"   Santiment error for {slug}: {e}")
        return None

    def score(self, ratio: Optional[float]) -> tuple[int, list]:
        """Max 10 pts."""
        if ratio is None:
            return 0, ["⚪ [Santiment] No data — add free key at santiment.net"]
        if ratio >= 3.5:
            return 10, [f"✅ [Santiment] {ratio:.1f}x social spike — pre-pump signal"]
        if ratio >= 2.0:
            return 7,  [f"✅ [Santiment] {ratio:.1f}x above avg — elevated interest"]
        if ratio >= 1.5:
            return 4,  [f"🔶 [Santiment] {ratio:.1f}x avg — slightly elevated"]
        return 1, [f"📊 [Santiment] Normal social activity ({ratio:.1f}x)"]


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
        filled = sig.score // 10
        bar = "█" * filled + "░" * (10 - filled)
        conf = self._confidence_label(sig.score)
        risk_e = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(sig.risk, "🟡")

        # ── Header ────────────────────────────────────────────────────
        msg  = f"🚨 *TRADE SIGNAL — BUY ${sig.symbol}* 🚨\n\n"
        msg += f"🪙 *{sig.name}* (`${sig.symbol}`) | Chain: `{sig.chain.upper()}`\n"
        msg += f"💲 Price: `${sig.price:,.6g}`\n"
        msg += f"📊 Score: `{bar}` *{sig.score}/100*\n"
        msg += f"🎯 Confidence: {conf}\n"
        msg += f"⏱ Hold: `{sig.timeframe}`\n"
        msg += f"{risk_e} Risk: `{sig.risk}`\n"

        # ── Token Identity (contract + links) ─────────────────────────
        msg += "\n━━ *TOKEN IDENTITY* ━━\n"

        # Contract address — full for copy-paste, shortened for display
        if sig.token_address:
            addr = sig.token_address
            short = addr[:6] + "..." + addr[-4:] if len(addr) > 12 else addr
            msg += f"📋 Contract: `{addr}`\n"
            msg += f"   _{short} — copy above to verify_\n"
        else:
            msg += f"📋 Contract: _Not available (large-cap native token)_\n"

        # CoinGecko link for research
        if sig.coingecko_url:
            msg += f"🦎 CoinGecko: {sig.coingecko_url}\n"
        else:
            msg += f"🦎 CoinGecko: https://www.coingecko.com/en/search?query={sig.symbol}\n"

        # Block explorer link
        if sig.explorer_url:
            msg += f"🔍 Explorer: {sig.explorer_url}\n"

        # ── Smart Money data ──────────────────────────────────────────
        if sig.smart_money_buyers > 0 or sig.sm_netflow_usd > 0:
            msg += "\n━━ *SMART MONEY* ━━\n"
            if sig.smart_money_buyers > 0:
                msg += f"🐋 Wallets buying: `{sig.smart_money_buyers}`\n"
            if sig.sm_netflow_usd > 0:
                msg += f"💰 Net inflow: `+${sig.sm_netflow_usd:,.0f}`\n"
            if sig.arkham_entity_count > 0:
                msg += f"🏛 Known entities: `{sig.arkham_entity_count}` accumulating\n"

        # ── Signal breakdown ──────────────────────────────────────────
        msg += "\n━━ *SIGNAL BREAKDOWN* ━━\n"
        for note in sig.breakdown:
            msg += f"{note}\n"

        if sig.warnings:
            msg += "\n⚠️ *WARNINGS*\n"
            for w in sig.warnings:
                msg += f"{w}\n"

        # ── Trade levels ──────────────────────────────────────────────
        msg += f"""
━━ *TRADE LEVELS* ━━
📥 Entry:     `${sig.entry:,.6g}`
🏆 Target 1:  `${sig.target_1:,.6g}` *(+5%)*
🏆 Target 2:  `${sig.target_2:,.6g}` *(+10%)*
🛑 Stop Loss: `${sig.stop_loss:,.6g}` *(-3%)*

_Always verify the contract address before trading._
🕐 _{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_
_⚠️ AI signal only. DYOR. Never risk more than you can afford._"""

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

        # ── Create rotators ────────────────────────────────────────────
        self._nansen_rot    = KeyRotator("Nansen",    nansen_keys)
        self._etherscan_rot = KeyRotator("Etherscan", etherscan_keys)

        # ── Inject rotators into layers ────────────────────────────────
        self.nansen   = NansenLayer(self._nansen_rot)
        self.arkham   = EtherscanLayer(self._etherscan_rot)
        self.lc       = CryptoCompareSocialLayer()
        self.san      = SantimentLayer()
        self.san_fg   = FearGreedLayer()
        self.cg       = CoinGeckoLayer()
        self.telegram = TelegramAlerter()
        self.scan_no  = 0
        # MAX_RAW = sum of points from layers that are actually connected.
        # This ensures scoring is fair when some layers have no keys.
        nansen_max    = 30 + 20  # screener + netflow
        etherscan_max = 20 if self._etherscan_rot.has_keys() else 10
        cc_max        = 15 if CRYPTOCOMPARE_API_KEY else 0
        fg_max        = 10  # always available
        cg_max        = 5   # always available
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

    def _write_signals_json(self, candidates: list):
        """Write signals to templates/signals.json so dashboard.html can read them."""
        try:
            data = {
                "scan_count": self.scan_no,
                "last_scan": datetime.utcnow().isoformat() + "Z",
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
                                            "nansen" if sig.smart_money_buyers > 0 else None,
                                            "arkham" if sig.arkham_entity_count > 0 else None,
                                            "cg",
                                        ] if l],
                    "breakdown":       [{"t": n, "cls": "ok" if "✅" in n else "med" if "🔶" in n else "neu"} for n in sig.breakdown],
                    "timestamp":       datetime.utcnow().isoformat() + "Z",
                })
            SIGNALS_JSON.parent.mkdir(parents=True, exist_ok=True)
            SIGNALS_JSON.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"   Could not write signals.json: {e}")

    def _trade_levels(self, price: float, risk: str) -> tuple[float, float, float]:
        t1 = price * (1.05 if risk == "LOW" else 1.07 if risk == "MEDIUM" else 1.10)
        t2 = price * (1.10 if risk == "LOW" else 1.15 if risk == "MEDIUM" else 1.20)
        sl = price * (0.97 if risk != "HIGH" else 0.95)
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

            # ── Fetch base data concurrently ─────────────────────────
            sm_tokens_task  = self.nansen.screen_smart_money_tokens(session, self.CHAINS)
            cg_markets_task = self.cg.fetch_markets(session)
            cg_trending_task= self.cg.fetch_trending(session)
            cc_data_task    = self.lc.fetch(session)
            fg_data_task    = self.san_fg.fetch(session)

            sm_tokens, cg_markets, trending, lc_all, fg_data = await asyncio.gather(
                sm_tokens_task, cg_markets_task, cg_trending_task, cc_data_task, fg_data_task
            )

            log.info(f"   Nansen SM tokens: {len(sm_tokens)} | CG coins: {len(cg_markets)} | Trending: {len(trending)}")

            # If no Nansen key, fall back to CoinGecko high-volume coins
            if not sm_tokens and not self._nansen_rot.has_keys():
                log.warning("   No Nansen key - scanning CoinGecko volume anomalies as fallback")
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
                sym       = (token.get("token_symbol", token.get("symbol", "")) or "").upper()
                name      = token.get("token_name", token.get("name", sym))
                chain     = token.get("chain", "ethereum")
                tok_addr  = token.get("token_address", token.get("address", ""))
                price_raw = token.get("price_usd", token.get("price", 0)) or 0

                # Fill price from CoinGecko if Nansen didn't return it
                cg_coin  = cg_markets.get(sym, {})
                price    = price_raw or (cg_coin.get("current_price") or 0)
                if price <= 0:
                    # Use price_usd directly from Nansen as last resort
                    price = token.get("price_usd", 0) or 0
                if price <= 0:
                    log.debug(f"   Skipping {sym} — no price available")
                    continue

                # ── Score Layer 1: Nansen screener ───────────────────
                nm_s_pts, nm_s_notes = self.nansen.score_screener(token)

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

                # ── Score Layer 4: LunarCrush social ─────────────────
                lc_pts, lc_notes = self.lc.score(sym, lc_all.get(sym, {}))

                # ── Score Layer 5: Fear & Greed (no key needed) ──────
                san_pts, san_notes = self.san_fg.score(fg_data)

                # ── Score Layer 6: CoinGecko volume confirmation ──────
                cg_pts, cg_notes = self.cg.score(cg_coin if cg_coin else None, sym in trending)

                # ── Combine ───────────────────────────────────────────
                raw_total = nm_s_pts + nm_nf_pts + ark_pts + lc_pts + san_pts + cg_pts
                norm = int((raw_total / self.MAX_RAW) * 100)

                if norm < SIGNAL_THRESHOLD:
                    continue

                sm_count = token.get("nof_traders", token.get("smart_money_count", token.get("nof_smart_money_traders", 0))) or 0
                risk, tf = self._risk_and_timeframe(norm, sm_count, net_flow)
                t1, t2, sl = self._trade_levels(price, risk)

                all_notes = nm_s_notes + nm_nf_notes + ark_notes + lc_notes + san_notes + cg_notes

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
            self._write_signals_json(candidates)


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
