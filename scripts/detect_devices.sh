#!/bin/bash
echo "Detecting Hauppauge devices..."
lsusb | grep -i hauppauge
echo ""
echo "Video devices:"
ls -la /dev/video*
echo ""
echo "DVB devices:"
ls -la /dev/dvb/ 2>/dev/null || echo "No DVB devices found"
echo ""
echo "V4L2 devices info:"
v4l2-ctl --list-devices
