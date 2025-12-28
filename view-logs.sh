#!/bin/bash
# Helper script to view DVR logs

echo "DVR Application Logs"
echo "===================="
echo ""

if [ "$1" == "follow" ] || [ "$1" == "-f" ]; then
    echo "Following logs (Ctrl+C to stop)..."
    sudo tail -f /var/log/DVR.log
elif [ "$1" == "clear" ]; then
    echo "Clearing logs..."
    sudo truncate -s 0 /var/log/DVR.log
    echo "Logs cleared!"
else
    echo "Last 50 lines:"
    echo ""
    sudo tail -n 50 /var/log/DVR.log
    echo ""
    echo "Usage:"
    echo "  ./view-logs.sh          - Show last 50 lines"
    echo "  ./view-logs.sh follow   - Follow logs in real-time"
    echo "  ./view-logs.sh clear    - Clear log file"
fi
