#!/bin/bash
cd ~/arkhsen
python3 scan_once.py 2>&1 | tail -6
./push.sh "scan $(date -u '+%H:%M UTC')"
