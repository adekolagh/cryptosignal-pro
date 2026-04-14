# 🖥️ CryptoSignal Pro — Complete MacBook Setup Guide
# Every single step. Nothing assumed.

═══════════════════════════════════════════════════════════
WHAT YOU NEED BEFORE STARTING
═══════════════════════════════════════════════════════════
✅ MacBook (any model, any year — Intel or Apple Silicon M1/M2/M3/M4)
✅ Internet connection
✅ About 20 minutes
✅ A Telegram account

YOU DO NOT NEED:
❌ Any coding experience
❌ A server or VPS (runs on your Mac)
❌ Any paid software

═══════════════════════════════════════════════════════════
STEP 1 — INSTALL HOMEBREW  (Mac's app installer, like an App Store for developers)
═══════════════════════════════════════════════════════════

1. Open "Terminal" on your Mac
   → Press Cmd+Space → type "Terminal" → press Enter

2. Copy and paste this ENTIRE line, then press Enter:
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

3. It will ask for your Mac password. Type it (nothing shows — that's normal). Press Enter.

4. Wait about 2-5 minutes. When it's done you'll see:
   "Installation successful!"

   ⚠️ Apple Silicon Mac (M1/M2/M3/M4) ONLY — run this extra step:
   echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
   eval "$(/opt/homebrew/bin/brew shellenv)"

5. Verify it worked:
   brew --version
   Should show something like: "Homebrew 4.x.x"

═══════════════════════════════════════════════════════════
STEP 2 — INSTALL PYTHON 3.11
═══════════════════════════════════════════════════════════

1. In Terminal, type:
   brew install python@3.11

2. Wait 1-2 minutes.

3. Verify:
   python3 --version
   Should show: "Python 3.11.x"

   If you see an older version, run:
   echo 'export PATH="/opt/homebrew/opt/python@3.11/bin:$PATH"' >> ~/.zprofile
   source ~/.zprofile

═══════════════════════════════════════════════════════════
STEP 3 — SET UP THE SCANNER FILES
═══════════════════════════════════════════════════════════

1. Create a folder for the scanner on your Desktop:
   mkdir ~/Desktop/CryptoSignal
   cd ~/Desktop/CryptoSignal

2. Copy scanner_v2.py into this folder
   (Drag it from Downloads/wherever Claude saved it, or copy-paste the file)

3. Install the required Python libraries:
   pip3 install aiohttp python-dotenv

   Wait for: "Successfully installed aiohttp-x.x.x python-dotenv-x.x.x"

═══════════════════════════════════════════════════════════
STEP 4 — CREATE YOUR TELEGRAM BOT  (10 minutes, completely free)
═══════════════════════════════════════════════════════════

A) CREATE THE BOT:
   1. Open Telegram on your phone or Mac
   2. Search for "@BotFather" (official Telegram bot creator)
   3. Tap/click START
   4. Type: /newbot
   5. It asks for a name → type anything, e.g.: CryptoSignalPro
   6. It asks for a username → must end in "bot", e.g.: mycsignals_bot
   7. BotFather gives you a TOKEN that looks like:
      7123456789:AAHdkfjhsdkfjhsJHKSDFkjhsdf
   8. COPY THIS TOKEN — you'll need it in Step 6

B) GET YOUR CHAT ID:
   1. In Telegram, search for your bot by its username (e.g., @mycsignals_bot)
   2. Click START or send any message to it
   3. Open this URL in your browser (replace YOUR_TOKEN with your actual token):
      https://api.telegram.org/botYOUR_TOKEN/getUpdates
   4. Look for "chat":{"id": followed by a number, e.g.: "id":987654321
   5. COPY THAT NUMBER — it's your Chat ID

C) TEST IT:
   In your browser paste this (fill in your token and chat id):
   https://api.telegram.org/botYOUR_TOKEN/sendMessage?chat_id=YOUR_CHAT_ID&text=Hello+from+CryptoSignal!
   
   If you see {"ok":true} — Telegram is working ✅

═══════════════════════════════════════════════════════════
STEP 5 — GET YOUR FREE API KEYS
═══════════════════════════════════════════════════════════

A) LUNARCRUSH (Social signals — Layer 4):
   1. Go to: https://lunarcrush.com/developers
   2. Click "Get Free API Key"
   3. Sign up with email
   4. Dashboard → API Keys → "Create New Key"
   5. Copy the key (looks like: lc_abc123xyz...)

B) SANTIMENT (On-chain signals — Layer 5):
   1. Go to: https://santiment.net
   2. Sign up free
   3. Go to Account → API Keys
   4. Click "Generate API Key"
   5. Copy the key

C) NANSEN (Smart Money — Layers 1+2, OPTIONAL but recommended):
   1. Go to: https://nansen.ai
   2. Subscribe to Pro plan ($49/month)
   3. Go to Account → API → Generate API Key
   Note: Without this, scanner uses CoinGecko as fallback (still works
   but signals are unconfirmed — good for watching, careful for trading)

D) ARKHAM (Entity intelligence — Layer 3, OPTIONAL):
   1. Go to: https://intel.arkm.com/api
   2. Apply for API access (institutional)
   Note: Without this, fraud detection layer is disabled

═══════════════════════════════════════════════════════════
STEP 6 — CONFIGURE YOUR .env FILE
═══════════════════════════════════════════════════════════

1. In Terminal (make sure you're in ~/Desktop/CryptoSignal):
   nano .env

2. The nano editor opens. Type/paste exactly this (fill in your values):

   TELEGRAM_BOT_TOKEN=7123456789:AAHdkfjhsdkfjhsJHKSDFkjhsdf
   TELEGRAM_CHAT_ID=987654321
   LUNARCRUSH_API_KEY=lc_your_key_here
   SANTIMENT_API_KEY=your_santiment_key_here
   NANSEN_API_KEY=
   ARKHAM_API_KEY=

   (Leave NANSEN and ARKHAM blank if you don't have them yet)

3. Press Ctrl+X, then Y, then Enter to save

4. Verify the file was saved:
   cat .env
   You should see all your keys printed

═══════════════════════════════════════════════════════════
STEP 7 — RUN THE SCANNER
═══════════════════════════════════════════════════════════

1. In Terminal:
   cd ~/Desktop/CryptoSignal
   python3 scanner_v2.py

2. You should see:
   ╔═══════════════════════════════════════════════════════════════╗
   ║        CryptoSignal Pro v2 — Real Nansen + Arkham             ║
   ╚═══════════════════════════════════════════════════════════════╝
     Nansen    : ⚠️  NOT connected — get Pro plan...
     Telegram  : ✅ Alerts enabled
   
   Then every 5 minutes: "── Scan #1 ──────"

3. When a signal fires, you get a Telegram message instantly.

═══════════════════════════════════════════════════════════
STEP 8 — KEEP IT RUNNING WHILE YOU SLEEP  (Optional)
═══════════════════════════════════════════════════════════

Option A — Keep Terminal open (simplest):
   Just don't close the Terminal window.
   Mac's Energy Saver might stop it — go to:
   System Preferences → Energy → prevent sleep when plugged in

Option B — Use screen (stays running even if Terminal closes):
   Install: brew install screen
   Start:   screen -S crypto
   Run:     python3 scanner_v2.py
   Detach:  Press Ctrl+A then D
   Return:  screen -r crypto
   Stop:    screen -r crypto → then Ctrl+C

Option C — Run as background service with launchd (Mac's scheduler):
   See AUTOSTART.md for the full service setup

═══════════════════════════════════════════════════════════
STEP 9 — OPEN THE DASHBOARD
═══════════════════════════════════════════════════════════

1. Find dashboard.html in your CryptoSignal folder
2. Double-click it — it opens in Chrome/Safari
3. It shows: Live market overview, signal history, layer status

The dashboard reads from signals.json that the scanner writes.
Both files need to be in the same folder.

═══════════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════════

"python3: command not found"
→ Run: brew install python@3.11
→ Then: echo 'export PATH="/opt/homebrew/opt/python@3.11/bin:$PATH"' >> ~/.zprofile

"No module named 'aiohttp'"
→ Run: pip3 install aiohttp python-dotenv

"ModuleNotFoundError: No module named 'dotenv'"
→ Run: pip3 install python-dotenv

"Telegram: error 401"
→ Your TELEGRAM_BOT_TOKEN is wrong. Re-copy from BotFather.

"Telegram: error 400 chat not found"
→ Your TELEGRAM_CHAT_ID is wrong. Redo Step 4B.

"Nansen 402 Out of credits"
→ Your 1000 monthly credits ran out. Either wait for reset or
  reduce scan frequency: change SCAN_INTERVAL_SEC=600 (10 min)

"LunarCrush 401"
→ Your LUNARCRUSH_API_KEY is wrong or expired. Get a new one.

Scan runs but NO signals ever fire:
→ Normal! Good signals are rare. Lower threshold temporarily:
  In scanner_v2.py, change: SIGNAL_THRESHOLD = 50
  This will show you what scores coins are getting.
