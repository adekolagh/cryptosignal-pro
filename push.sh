#!/bin/bash
cd ~/arkhsen
git add -A
git commit -m "${1:-update}" 2>/dev/null
git pull origin main --rebase --autostash 2>/dev/null
git push origin main
echo "Done"
