#!/bin/bash
git checkout -- templates/signals.json 2>/dev/null
git add -A
git commit -m "${1:-update}" 2>/dev/null
git pull origin main --rebase 2>/dev/null
git checkout --theirs templates/signals.json 2>/dev/null
git add templates/signals.json 2>/dev/null
git rebase --continue 2>/dev/null
git push origin main --force
echo "Done"
