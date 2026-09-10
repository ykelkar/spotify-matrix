#!/bin/bash
set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# Start the fetcher in the background
python3 fetcher.py --poll-seconds 3 > fetcher.log 2>&1 &
FETCHER_PID=$!
echo "Fetcher started (PID $FETCHER_PID), logging to fetcher.log"

# Make sure the fetcher gets killed when this script exits, however it exits
cleanup() {
    echo "Stopping fetcher (PID $FETCHER_PID)..."
    kill "$FETCHER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Give the fetcher a moment to write its first files
sleep 2

# Run the display in the foreground
sudo .venv/bin/python3 display.py --rows 64 --cols 64 --chain-length 1 --parallel 1 --hardware-mapping regular --gpio-slowdown 2 --brightness 65
