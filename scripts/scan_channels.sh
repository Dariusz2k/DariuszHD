#!/bin/bash
echo "HomeRun Clone - Standalone Channel Scanner"
echo "========================================="

# Copy channel scanner to project directory
cp /home/claude/channel_scanner.py /opt/homerun-clone/
chmod +x /opt/homerun-clone/channel_scanner.py

# Create Python activation script
cd /opt/homerun-clone

echo "Running enhanced channel scanner..."
./venv/bin/python channel_scanner.py --device /dev/video0 --threshold 20

echo ""
echo "Scan complete! Check the results at:"
echo "  Channel file: /opt/homerun-clone/config/channels.json"
echo "  Scan results: /opt/homerun-clone/config/scan_results.json"
echo ""
echo "Start the web interface to view channels:"
echo "  /opt/homerun-clone/scripts/service.sh start"
