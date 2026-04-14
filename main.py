#!/usr/bin/env python3
"""
CryptoSignal Pro — Main Entry Point
Runs scanner + dashboard server together in one process.
Works on Railway, Render, Fly.io, or your local Mac.

Environment variables (set in Railway dashboard, never commit .env):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  NANSEN_API_KEY, LUNARCRUSH_API_KEY, SANTIMENT_API_KEY, ARKHAM_API_KEY
  PORT (set automatically by Railway)
"""

import asyncio
import threading
import http.server
import socketserver
import os
import logging
from pathlib import Path

log = logging.getLogger("main")

# ── Web server (serves dashboard + signals.json) ──────────────────────
def run_server():
    PORT = int(os.environ.get("PORT", 8888))
    BASE_DIR = Path(__file__).parent
    os.chdir(BASE_DIR)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress access logs

        def end_headers(self):
            # Allow dashboard to read signals.json (CORS for local dev)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

    # Allow reuse so Railway restarts don't fail with "address in use"
    socketserver.TCPServer.allow_reuse_address = True

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"\n  🌐 Dashboard: http://localhost:{PORT}/templates/dashboard.html")
        print(f"  📡 Signals:   http://localhost:{PORT}/templates/signals.json\n")
        httpd.serve_forever()

# ── Scanner (runs async every 5 minutes) ─────────────────────────────
async def run_scanner():
    # Import scanner after server is ready
    from scanner_v2 import CryptoSignalScannerV2, SCAN_INTERVAL_SEC

    scanner = CryptoSignalScannerV2()
    print("  🔍 Scanner started\n")

    while True:
        try:
            await scanner.run_scan()
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL_SEC)

# ── Entry point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S"
    )

    print("""
╔══════════════════════════════════════════════════════╗
║         CryptoSignal Pro — Starting Up               ║
╚══════════════════════════════════════════════════════╝""")

    # Start web server in background thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Run scanner in main async loop
    asyncio.run(run_scanner())
