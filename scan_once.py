#!/usr/bin/env python3
"""
scan_once.py — runs ONE scan then exits.
Called by GitHub Actions every 5 minutes.
API keys come from environment variables (GitHub Secrets).
"""
import asyncio
import sys
import os
from pathlib import Path

# Make sure we can import scanner_v2
sys.path.insert(0, str(Path(__file__).parent))

async def main():
    from scanner_v2 import CryptoSignalScannerV2
    scanner = CryptoSignalScannerV2()
    await scanner.run_scan()

asyncio.run(main())
