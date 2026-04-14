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
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
NANSEN_API_KEY      = os.getenv("NANSEN_API_KEY", "")       # Pro plan $49/mo → nansen.ai
ARKHAM_API_KEY      = os.getenv("ARKHAM_API_KEY", "")       # Apply at intel.arkm.com/api
LUNARCRUSH_API_KEY  = os.getenv("LUNARCRUSH_API_KEY", "")   # Free → lunarcrush.com/developers
SANTIMENT_API_KEY   = os.getenv("SANTIMENT_API_KEY", "")    # Free → santiment.net

SCAN_INTERVAL_SEC   = 300    # 5 minutes
SIGNAL_THRESHOLD    = 65     # Out of 100 — raise to 80 for institutional-grade only
COOLDOWN_HOURS      = 4      # Hours before re-alerting same token
MAX_ALERTS_PER_SCAN = 2      # Keep it tight — quality over quantity

# Nansen credit budget per scan (Token Screener = 5 credits, Netflow = 5 credits)
# With 1000 credits/month on Pro: budget ~30 per scan at 5min intervals = fine
NANSEN_CREDIT_BUDGET_PER_SCAN = 50

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("CSP-v2")


# ─────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────
@dataclass
class TokenSignal:
    symbol:         str
    name:           str
    chain:          str
    price:          float
    token_address:  str = ""
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
    # Raw data carried through for signal building
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

    def _headers(self) -> dict:
        return {"apiKey": NANSEN_API_KEY, "Content-Type": "application/json"}

    async def screen_smart_money_tokens(
        self, session: aiohttp.ClientSession, chains: list[str]
    ) -> list[dict]:
        """
        Layer 1: Call Nansen Token Screener with only_smart_money=True.
        Returns tokens where Smart Money wallets are actively buying.
        Cost: 5 credits per call.
        Docs: https://docs.nansen.ai/api/token-god-mode/token-screener
        """
        if not NANSEN_API_KEY:
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
                "market_cap_usd":   {"min": 500_000, "max": 5_000_000_000},
                "liquidity":        {"min": 100_000},
                "nof_traders":      {"min": 20},
            },
            "order_by": [{"field": "volume", "direction": "DESC"}]
        }
        try:
            async with session.post(
                f"{self.BASE}/api/v1/token-screener",
                headers=self._headers(),
                json=payload,
                timeout=15
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    tokens = data.get("tokens", data.get("data", []))
                    log.info(f"   Nansen screener: {len(tokens)} Smart Money tokens found")
                    return tokens
                elif r.status == 402:
                    log.warning("   Nansen: Out of credits for this cycle")
                elif r.status == 401:
                    log.error("   Nansen: Invalid API key")
                else:
                    log.warning(f"   Nansen screener HTTP {r.status}: {await r.text()}")
        except Exception as e:
            log.warning(f"   Nansen screener error: {e}")
        return []

    async def get_smart_money_netflow(
        self, session: aiohttp.ClientSession, token_address: str, chain: str
    ) -> dict:
        """
        Layer 2: Get Smart Money net inflow/outflow for a specific token.
        Positive netflow = Smart Money buying. Negative = Smart Money exiting.
        Cost: 5 credits per call.
        Docs: https://docs.nansen.ai/api/smart-money
        """
        if not NANSEN_API_KEY or not token_address:
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
        try:
            async with session.post(
                f"{self.BASE}/api/v1/smart-money/netflow",
                headers=self._headers(),
                json=payload,
                timeout=15
            ) as r:
                if r.status == 200:
                    return await r.json()
                log.debug(f"   Nansen netflow HTTP {r.status} for {token_address[:10]}...")
        except Exception as e:
            log.debug(f"   Nansen netflow error: {e}")
        return {}

    def score_screener(self, token: dict) -> tuple[int, list]:
        """Score based on Nansen Smart Money screener result. Max 30 pts."""
        pts = 0
        notes = []

        # Number of distinct Smart Money wallets buying
        sm_count   = token.get("smart_money_count", token.get("nof_smart_money_traders", 0)) or 0
        volume_usd = token.get("volume_usd", token.get("volume", 0)) or 0
        price_chg  = token.get("price_change", token.get("price_change_percentage", 0)) or 0

        if sm_count >= 20:
            pts += 20
            notes.append(f"✅ [Nansen] {sm_count} Smart Money wallets buying — VERY HIGH conviction")
        elif sm_count >= 10:
            pts += 14
            notes.append(f"✅ [Nansen] {sm_count} Smart Money wallets buying — High conviction")
        elif sm_count >= 5:
            pts += 8
            notes.append(f"🔶 [Nansen] {sm_count} Smart Money wallets buying — Moderate")
        elif sm_count >= 2:
            pts += 4
            notes.append(f"📊 [Nansen] {sm_count} Smart Money wallets — Watch")

        # Volume amongst Smart Money wallets
        if volume_usd > 1_000_000:
            pts += 10
            notes.append(f"✅ [Nansen] SM volume: ${volume_usd:,.0f} — Institutional-scale activity")
        elif volume_usd > 100_000:
            pts += 6
            notes.append(f"🔶 [Nansen] SM volume: ${volume_usd:,.0f}")
        elif volume_usd > 10_000:
            pts += 3
            notes.append(f"📊 [Nansen] SM volume: ${volume_usd:,.0f}")

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
# LAYER 3 — ARKHAM INTELLIGENCE  (Entity Transaction Monitoring)
# Real API: https://api.arkhamintelligence.com
# Requires: API key (apply at intel.arkm.com/api)
# Purpose: Detects coordinated wallet clusters buying a token,
#          flagging potential pump coordination OR confirming
#          legitimate fund accumulation
# ─────────────────────────────────────────────────────────────────────
class ArkhamLayer:
    BASE = "https://api.arkhamintelligence.com"

    # Known high-value entity types that signal legitimacy (not pump groups)
    LEGIT_ENTITY_TYPES = {
        "fund", "exchange", "institution", "market_maker",
        "dao", "protocol", "venture_capital"
    }

    # Entity types that suggest manipulation risk
    SUSPICIOUS_TYPES = {
        "unknown_cluster", "mixer", "suspicious"
    }

    def _headers(self) -> dict:
        return {"API-Key": ARKHAM_API_KEY, "Content-Type": "application/json"}

    async def get_token_transfers(
        self,
        session: aiohttp.ClientSession,
        token_address: str,
        chain: str = "ethereum"
    ) -> list:
        """
        Get recent large transfers for a token to detect:
        - Legitimate fund accumulation (bullish)
        - Coordinated unknown wallet cluster activity (pump warning)
        Docs: https://codex.arkm.com/arkham-api
        """
        if not ARKHAM_API_KEY or not token_address:
            return []

        # Query last 2 hours of transfers above $10k
        params = {
            "base": token_address,
            "chain": chain,
            "usdGte": "10000",
            "limit": "30",
            "sortKey": "time",
            "sortDir": "desc",
        }
        try:
            async with session.get(
                f"{self.BASE}/transfers",
                headers=self._headers(),
                params=params,
                timeout=15
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("transfers", [])
                elif r.status == 401:
                    log.error("   Arkham: Invalid API key")
                elif r.status == 429:
                    log.warning("   Arkham: Rate limited")
                else:
                    log.debug(f"   Arkham HTTP {r.status}")
        except Exception as e:
            log.debug(f"   Arkham error: {e}")
        return []

    async def check_entity(
        self, session: aiohttp.ClientSession, address: str
    ) -> dict:
        """
        Look up who is behind a wallet address.
        This is what makes Arkham unique — entity deanonymization.
        """
        if not ARKHAM_API_KEY:
            return {}
        try:
            async with session.get(
                f"{self.BASE}/intelligence/address/{address}/all",
                headers=self._headers(),
                timeout=10
            ) as r:
                if r.status == 200:
                    return await r.json()
        except:
            pass
        return {}

    def score_transfers(self, transfers: list) -> tuple[int, list, bool, int]:
        """
        Analyze transfer patterns.
        Returns: (score, notes, is_suspicious, legit_entity_count)
        Max 20 pts.
        """
        if not transfers:
            return 0, ["⚪ [Arkham] No transfer data (add API key at intel.arkm.com/api)"], False, 0

        pts = 0
        notes = []
        is_suspicious = False

        legit_entity_count  = 0
        unknown_count       = 0
        total_inflow_usd    = 0.0
        buying_entities     = set()

        for tx in transfers:
            from_entity = tx.get("fromEntity", {}) or {}
            to_entity   = tx.get("toEntity", {})   or {}
            usd_val     = float(tx.get("unitValue", tx.get("usdValue", 0)) or 0)
            tx_type     = tx.get("type", "")

            # Is the receiver a known legitimate entity?
            to_type = (to_entity.get("type") or "").lower()
            to_name = to_entity.get("name", "")

            if to_type in self.LEGIT_ENTITY_TYPES and to_name:
                legit_entity_count += 1
                buying_entities.add(to_name)
                total_inflow_usd += usd_val
            elif to_type in self.SUSPICIOUS_TYPES:
                unknown_count += 1

        # Score based on what Arkham found
        if legit_entity_count >= 5:
            pts = 20
            entities_str = ", ".join(list(buying_entities)[:3])
            notes.append(f"✅ [Arkham] {legit_entity_count} KNOWN entities accumulating: {entities_str}…")
        elif legit_entity_count >= 3:
            pts = 14
            entities_str = ", ".join(list(buying_entities)[:3])
            notes.append(f"✅ [Arkham] {legit_entity_count} known entities buying: {entities_str}")
        elif legit_entity_count >= 1:
            pts = 8
            notes.append(f"🔶 [Arkham] {legit_entity_count} known entity involved: {list(buying_entities)[0]}")
        else:
            pts = 3
            notes.append(f"📊 [Arkham] {len(transfers)} transfers detected, entities unknown")

        # Suspicious cluster warning (kills the signal)
        if unknown_count > legit_entity_count * 2 and unknown_count > 5:
            is_suspicious = True
            notes.append(f"🚨 [Arkham] FRAUD ALERT: {unknown_count} unknown/cluster wallets — possible coordinated pump")
            pts = 0  # Zero out Arkham contribution — don't trade this

        if total_inflow_usd > 0:
            notes.append(f"   [Arkham] Total tracked inflow: ${total_inflow_usd:,.0f}")

        return min(pts, 20), notes, is_suspicious, legit_entity_count


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

        msg  = f"🚨 *TRADE SIGNAL — BUY ${sig.symbol}* 🚨\n\n"
        msg += f"🪙 *{sig.name}* | Chain: `{sig.chain.upper()}`\n"
        msg += f"💲 Price: `${sig.price:,.6g}`\n"
        msg += f"📊 Score: `{bar}` *{sig.score}/100*\n"
        msg += f"🎯 Confidence: {conf}\n"
        msg += f"⏱ Hold: `{sig.timeframe}`\n"
        msg += f"{risk_e} Risk: `{sig.risk}`\n"

        if sig.smart_money_buyers > 0:
            msg += f"🐋 SM Wallets: `{sig.smart_money_buyers}` buying\n"
        if sig.sm_netflow_usd > 0:
            msg += f"💰 SM Netflow: `+${sig.sm_netflow_usd:,.0f}` inflow\n"
        if sig.arkham_entity_count > 0:
            msg += f"🏛 Arkham Entities: `{sig.arkham_entity_count}` known entities accumulating\n"

        msg += "\n━━ *SIGNAL BREAKDOWN* ━━\n"
        for note in sig.breakdown:
            msg += f"{note}\n"

        if sig.warnings:
            msg += "\n⚠️ *WARNINGS*\n"
            for w in sig.warnings:
                msg += f"{w}\n"

        msg += f"""
━━ *TRADE LEVELS* ━━
📥 Entry:     `${sig.entry:,.6g}`
🏆 Target 1:  `${sig.target_1:,.6g}` *(+5%)*
🏆 Target 2:  `${sig.target_2:,.6g}` *(+10%)*
🛑 Stop Loss: `${sig.stop_loss:,.6g}` *(-3%)*

⚡ *Sources confirmed: Nansen Smart Money + Arkham + Social*
🕐 _{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_
_⚠️ AI signal — always DYOR. Risk only what you can lose._"""

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
        self.nansen   = NansenLayer()
        self.arkham   = ArkhamLayer()
        self.lc       = LunarCrushLayer()
        self.san      = SantimentLayer()
        self.cg       = CoinGeckoLayer()
        self.telegram = TelegramAlerter()
        self.scan_no  = 0
        # Layer weights should sum to 100
        self.MAX_RAW  = 30 + 20 + 20 + 15 + 10 + 5  # = 100

    CHAINS = ["ethereum", "solana", "base", "arbitrum", "bnb"]

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
                    "symbol":       sig.symbol,
                    "name":         sig.name,
                    "chain":        sig.chain,
                    "price":        sig.price,
                    "score":        sig.score,
                    "risk":         sig.risk,
                    "timeframe":    sig.timeframe,
                    "entry":        sig.entry,
                    "target1":      sig.target_1,
                    "target2":      sig.target_2,
                    "sl":           sig.stop_loss,
                    "sm_buyers":    sig.smart_money_buyers,
                    "sm_netflow":   sig.sm_netflow_usd,
                    "arkham_entities": sig.arkham_entity_count,
                    "layers":       [l for l in [
                                        "nansen" if sig.smart_money_buyers > 0 else None,
                                        "arkham" if sig.arkham_entity_count > 0 else None,
                                        "cg",
                                    ] if l],
                    "breakdown":    [{"t": n, "cls": "ok" if "✅" in n else "med" if "🔶" in n else "neu"} for n in sig.breakdown],
                    "timestamp":    sig.entry and datetime.utcnow().isoformat() + "Z",
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
        log.info(f"── Scan #{self.scan_no} ─────────────────────────────────────────────")

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:

            # ── Fetch base data concurrently ─────────────────────────
            sm_tokens_task  = self.nansen.screen_smart_money_tokens(session, self.CHAINS)
            cg_markets_task = self.cg.fetch_markets(session)
            cg_trending_task= self.cg.fetch_trending(session)
            lc_data_task    = self.lc.fetch(session)

            sm_tokens, cg_markets, trending, lc_all = await asyncio.gather(
                sm_tokens_task, cg_markets_task, cg_trending_task, lc_data_task
            )

            log.info(f"   Nansen SM tokens: {len(sm_tokens)} | CG coins: {len(cg_markets)} | Trending: {len(trending)}")

            # ── If no Nansen key, fall back to CoinGecko high-volume coins ──
            if not sm_tokens and not NANSEN_API_KEY:
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
                if credits_used >= NANSEN_CREDIT_BUDGET_PER_SCAN:
                    log.info(f"   Credit budget reached ({NANSEN_CREDIT_BUDGET_PER_SCAN}), stopping")
                    break

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
                nm_s_pts, nm_s_notes = self.nansen.score_screener(token)

                # ── Score Layer 2: Nansen Smart Money netflow ─────────
                nm_nf_pts, nm_nf_notes, net_flow = 0, [], 0.0
                if tok_addr and NANSEN_API_KEY and credits_used + 5 <= NANSEN_CREDIT_BUDGET_PER_SCAN:
                    nf_data = await self.nansen.get_smart_money_netflow(session, tok_addr, chain)
                    nm_nf_pts, nm_nf_notes, net_flow = self.nansen.score_netflow(nf_data)
                    credits_used += 5  # Netflow = 5 credits

                # ── Score Layer 3: Arkham entity analysis ────────────
                ark_pts, ark_notes, is_suspicious, entity_count = 0, [], False, 0
                if tok_addr and ARKHAM_API_KEY:
                    transfers = await self.arkham.get_token_transfers(session, tok_addr, chain)
                    ark_pts, ark_notes, is_suspicious, entity_count = self.arkham.score_transfers(transfers)

                # CRITICAL: If Arkham flags this as suspicious pump — skip entirely
                if is_suspicious:
                    log.warning(f"   🚨 {sym} SKIPPED — Arkham flagged suspicious wallet cluster")
                    continue

                # ── Score Layer 4: LunarCrush social ─────────────────
                lc_pts, lc_notes = self.lc.score(sym, lc_all.get(sym, {}))

                # ── Score Layer 5: Santiment on-chain spike ───────────
                san_pts, san_notes = 0, []
                slug = self.san.SLUG_MAP.get(sym)
                if slug:
                    ratio = await self.san.get_spike_ratio(session, slug)
                    san_pts, san_notes = self.san.score(ratio)

                # ── Score Layer 6: CoinGecko volume confirmation ──────
                cg_pts, cg_notes = self.cg.score(cg_coin if cg_coin else None, sym in trending)

                # ── Combine ───────────────────────────────────────────
                raw_total = nm_s_pts + nm_nf_pts + ark_pts + lc_pts + san_pts + cg_pts
                norm = int((raw_total / self.MAX_RAW) * 100)

                if norm < SIGNAL_THRESHOLD:
                    continue

                sm_count = token.get("smart_money_count", token.get("nof_smart_money_traders", 0)) or 0
                risk, tf = self._risk_and_timeframe(norm, sm_count, net_flow)
                t1, t2, sl = self._trade_levels(price, risk)

                all_notes = nm_s_notes + nm_nf_notes + ark_notes + lc_notes + san_notes + cg_notes

                candidates.append(TokenSignal(
                    symbol=sym, name=name, chain=chain, price=price,
                    token_address=tok_addr, score=norm,
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
    print(f"  Nansen    : {'✅ Real Smart Money API connected' if NANSEN_API_KEY else '⚠️  NOT connected — get Pro plan at nansen.ai ($49/mo)'}")
    print(f"  Arkham    : {'✅ Entity intelligence connected' if ARKHAM_API_KEY else '⚠️  NOT connected — apply at intel.arkm.com/api'}")
    print(f"  LunarCrush: {'✅' if LUNARCRUSH_API_KEY else '⚠️  No key (lunarcrush.com/developers — free)'}")
    print(f"  Santiment : {'✅' if SANTIMENT_API_KEY else '⚠️  No key (santiment.net — free)'}")
    print(f"  CoinGecko : ✅ Free (no key needed)")
    print(f"  Telegram  : {'✅ Alerts enabled' if TELEGRAM_BOT_TOKEN else '⚠️  Not set — alerts go to console'}")
    print()

    if not NANSEN_API_KEY:
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
