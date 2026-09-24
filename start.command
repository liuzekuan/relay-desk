#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ is required. Install Python, then open this launcher again."
  read -r reply
  exit 1
fi
if ! python3 -c 'import sys; assert sys.version_info >= (3, 11)' >/dev/null 2>&1; then
  echo "Python 3.11+ is required."
  read -r reply
  exit 1
fi
mkdir -p meow-local-logs
nohup python3 -B meow-local-tools/dashboard.py >meow-local-logs/dashboard.log 2>&1 </dev/null &
echo "Relay Desk is opening. No tests start automatically."
