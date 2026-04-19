#!/bin/bash
cd ~/arkhsen
echo "Running scanner..."
python3 scan_once.py 2>&1 | tail -6

echo "Pushing signals to GitHub..."
git add -f templates/signals.json
git diff --staged --quiet || git commit -m "signals $(date -u '+%H:%M UTC')"
git pull origin main --rebase --autostash 2>/dev/null
git push origin main
echo "Done — dashboard updated"
