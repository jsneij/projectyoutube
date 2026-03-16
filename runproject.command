#!/bin/bash
cd "$(dirname "$0")"

lsof -ti :8000 | xargs kill -9 2>/dev/null

python3 -m http.server 8000 &
SERVER_PID=$!

sleep 1

open -a "Google Chrome" "http://localhost:8000/dashboard/dshb_youtube.html"

echo ""
echo "  Dashboard running at http://localhost:8000/dashboard/dshb_youtube.html"
echo "  Server PID: $SERVER_PID"
echo ""
echo "  Press any key to stop the server..."
read -n 1

kill $SERVER_PID 2>/dev/null
echo "  Server stopped."
