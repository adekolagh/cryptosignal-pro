#!/usr/bin/env python3
"""
CryptoSignal Pro — Dashboard Server
Run this to open your dashboard in the browser.

Usage:
  python3 serve.py

Then open: http://localhost:8888
"""

import http.server
import socketserver
import webbrowser
import threading
import os
from pathlib import Path

PORT = 8888
BASE_DIR = Path(__file__).parent

# Serve from arkhsen/ folder so dashboard can read templates/signals.json
os.chdir(BASE_DIR)

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Only log errors, not every request
        if args[1] not in ('200', '304'):
            print(f"  [{args[1]}] {args[0].split()[1]}")

def open_browser():
    import time
    time.sleep(0.8)
    url = f"http://localhost:{PORT}/templates/dashboard.html"
    print(f"\n  ✅ Opening dashboard: {url}")
    print(f"  Keep this terminal open while using the dashboard.")
    print(f"  Press Ctrl+C to stop the server.\n")
    webbrowser.open(url)

print(f"""
╔═══════════════════════════════════════════╗
║   CryptoSignal Dashboard Server           ║
╚═══════════════════════════════════════════╝
  Serving from: {BASE_DIR}
  Dashboard:    http://localhost:{PORT}/templates/dashboard.html
""")

threading.Thread(target=open_browser, daemon=True).start()

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
