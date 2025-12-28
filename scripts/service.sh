#!/bin/bash
case "$1" in
    start)
        echo "Starting HomeRun Clone..."
        sudo systemctl start homerun-clone
        ;;
    stop)
        echo "Stopping HomeRun Clone..."
        sudo systemctl stop homerun-clone
        ;;
    restart)
        echo "Restarting HomeRun Clone..."
        sudo systemctl restart homerun-clone
        ;;
    status)
        sudo systemctl status homerun-clone
        ;;
    enable)
        echo "Enabling HomeRun Clone to start on boot..."
        sudo systemctl enable homerun-clone
        ;;
    logs)
        sudo journalctl -u homerun-clone -f
        ;;
    scan)
        echo "Running channel scanner..."
        /opt/homerun-clone/scripts/scan_channels.sh
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|enable|logs|scan}"
        exit 1
        ;;
esac
