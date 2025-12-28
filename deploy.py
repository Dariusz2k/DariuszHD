#!/usr/bin/env python3
"""
HomeRunHD Clone Setup Script
Creates a network TV tuner system using Hauppauge WinTV-dualHD
Author: Setup Script for Raspberry Pi
"""

import os
import subprocess
import sys
import json
from pathlib import Path

class HomeRunCloneSetup:
    def __init__(self):
        self.project_root = "/opt/homerun-clone"
        self.web_port = 5000
        self.streaming_port = 8000
        self.config_file = f"{self.project_root}/config/config.json"
        
    def run_command(self, command, description=""):
        """Execute shell command with error handling"""
        print(f"{'='*50}")
        print(f"Running: {description or command}")
        print(f"{'='*50}")
        
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
            if result.stdout:
                print(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error: {e}")
            if e.stderr:
                print(f"Error output: {e.stderr}")
            return False

    def create_directory_structure(self):
        """Create the project directory structure"""
        directories = [
            f"{self.project_root}",
            f"{self.project_root}/app",
            f"{self.project_root}/app/templates",
            f"{self.project_root}/app/static/css",
            f"{self.project_root}/app/static/js",
            f"{self.project_root}/config",
            f"{self.project_root}/logs",
            f"{self.project_root}/recordings",
            f"{self.project_root}/scripts",
            "/var/log/homerun-clone"
        ]
        
        print("Creating directory structure...")
        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)
            print(f"Created: {directory}")

    def install_system_dependencies(self):
        """Install required system packages and drivers"""
        print("Updating system packages...")
        if not self.run_command("sudo apt update && sudo apt upgrade -y", "Updating system"):
            return False
        
        packages = [
            "python3-pip",
            "python3-venv", 
            "v4l-utils",
            "dvb-tools",
            "dvb-apps",
            "ffmpeg",
            "vlc",
            "nginx",
            "supervisor",
            "build-essential",
            "linux-headers-$(uname -r)",
            "git",
            "curl",
            "wget"
        ]
        
        package_list = " ".join(packages)
        if not self.run_command(f"sudo apt install -y {package_list}", "Installing system packages"):
            return False
            
        # Install Hauppauge drivers
        print("Installing Hauppauge WinTV drivers...")
        driver_commands = [
            "sudo modprobe em28xx",
            "sudo modprobe em28xx-dvb",
            "sudo modprobe lgdt3306a",
            "sudo modprobe si2157"
        ]
        
        for cmd in driver_commands:
            self.run_command(cmd, f"Loading driver module: {cmd}")
            
        return True

    def setup_python_environment(self):
        """Setup Python virtual environment and install packages"""
        venv_path = f"{self.project_root}/venv"
        
        print("Creating Python virtual environment...")
        if not self.run_command(f"python3 -m venv {venv_path}", "Creating virtual environment"):
            return False
        
        pip_command = f"{venv_path}/bin/pip"
        python_packages = [
            "flask",
            "flask-socketio",
            "psutil",
            "subprocess32",
            "python-vlc",
            "requests",
            "schedule"
        ]
        
        for package in python_packages:
            if not self.run_command(f"{pip_command} install {package}", f"Installing {package}"):
                return False
                
        return True

    def create_main_application(self):
        """Create the main Flask application"""
        app_content = '''#!/usr/bin/env python3
"""
HomeRunHD Clone - Main Application
Network TV Tuner Web Interface
"""

from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import subprocess
import json
import os
import threading
import time
import psutil
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'homerun-clone-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

class TVTuner:
    def __init__(self):
        self.device_path = "/dev/video0"  # Adjust based on your device
        self.current_channel = None
        self.is_streaming = False
        self.stream_process = None
        self.channels = {}
        self.load_channels()
    
    def load_channels(self):
        """Load channel configuration"""
        try:
            with open('/opt/homerun-clone/config/channels.json', 'r') as f:
                self.channels = json.load(f)
        except FileNotFoundError:
            self.channels = {
                "2.1": {"name": "CBS", "frequency": "177000000"},
                "4.1": {"name": "NBC", "frequency": "193000000"},
                "7.1": {"name": "ABC", "frequency": "175000000"},
                "13.1": {"name": "PBS", "frequency": "213000000"}
            }
            self.save_channels()
    
    def save_channels(self):
        """Save channel configuration"""
        os.makedirs('/opt/homerun-clone/config', exist_ok=True)
        with open('/opt/homerun-clone/config/channels.json', 'w') as f:
            json.dump(self.channels, f, indent=2)
    
    def scan_channels(self):
        """Scan for available channels"""
        try:
            cmd = ["w_scan", "-f", "t", "-c", "US", "-X"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            # Parse w_scan output and update channels
            # This is a simplified parser - you may need to adjust
            channels = {}
            for line in result.stdout.split('\\n'):
                if line.startswith('T'):
                    parts = line.split(':')
                    if len(parts) >= 3:
                        freq = parts[1]
                        name = parts[2] if len(parts) > 2 else f"Channel {freq}"
                        channels[freq] = {"name": name, "frequency": freq}
            
            self.channels.update(channels)
            self.save_channels()
            return True
        except Exception as e:
            print(f"Channel scan error: {e}")
            return False
    
    def tune_channel(self, channel):
        """Tune to specific channel"""
        if channel not in self.channels:
            return False
        
        try:
            # Stop current stream
            self.stop_stream()
            
            # Tune using v4l2-ctl
            freq = self.channels[channel]['frequency']
            cmd = f"v4l2-ctl -d {self.device_path} --set-freq={freq}"
            result = subprocess.run(cmd, shell=True, capture_output=True)
            
            if result.returncode == 0:
                self.current_channel = channel
                return True
            return False
        except Exception as e:
            print(f"Tuning error: {e}")
            return False
    
    def start_stream(self, channel, output_port=8000):
        """Start streaming the tuned channel"""
        if not self.tune_channel(channel):
            return False
        
        try:
            # FFmpeg command to stream via HTTP
            cmd = [
                'ffmpeg',
                '-f', 'v4l2',
                '-i', self.device_path,
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-c:a', 'aac',
                '-f', 'mpegts',
                f'http://localhost:{output_port}/stream.ts'
            ]
            
            self.stream_process = subprocess.Popen(cmd)
            self.is_streaming = True
            return True
        except Exception as e:
            print(f"Streaming error: {e}")
            return False
    
    def stop_stream(self):
        """Stop current stream"""
        if self.stream_process:
            self.stream_process.terminate()
            self.stream_process = None
        self.is_streaming = False
    
    def get_signal_strength(self):
        """Get signal strength information"""
        try:
            cmd = f"v4l2-ctl -d {self.device_path} --get-tuner"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            # Parse signal strength from output
            # This is device-specific and may need adjustment
            return {"strength": 75, "quality": 80}  # Placeholder
        except:
            return {"strength": 0, "quality": 0}

tuner = TVTuner()

@app.route('/')
def index():
    return render_template('index.html', channels=tuner.channels)

@app.route('/api/channels')
def get_channels():
    return jsonify(tuner.channels)

@app.route('/api/scan', methods=['POST'])
def scan_channels():
    success = tuner.scan_channels()
    return jsonify({"success": success, "channels": tuner.channels})

@app.route('/api/tune/<channel>')
def tune_channel(channel):
    success = tuner.tune_channel(channel)
    return jsonify({"success": success, "channel": channel})

@app.route('/api/stream/<channel>')
def start_stream(channel):
    success = tuner.start_stream(channel)
    return jsonify({"success": success, "streaming": tuner.is_streaming})

@app.route('/api/stop')
def stop_stream():
    tuner.stop_stream()
    return jsonify({"success": True, "streaming": False})

@app.route('/api/status')
def get_status():
    signal = tuner.get_signal_strength()
    return jsonify({
        "current_channel": tuner.current_channel,
        "is_streaming": tuner.is_streaming,
        "signal_strength": signal["strength"],
        "signal_quality": signal["quality"]
    })

@app.route('/stream')
def stream_video():
    """Serve video stream"""
    def generate():
        # This would serve the actual video stream
        # For now, this is a placeholder
        pass
    
    return Response(generate(), mimetype='video/mp2t')

@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('status', {
        "current_channel": tuner.current_channel,
        "is_streaming": tuner.is_streaming,
        "channels": tuner.channels
    })

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
'''
        
        with open(f"{self.project_root}/app/app.py", 'w') as f:
            f.write(app_content)
        
        os.chmod(f"{self.project_root}/app/app.py", 0o755)

    def create_web_templates(self):
        """Create HTML templates for the web interface"""
        # Main template
        index_html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HomeRun Clone - TV Tuner</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="{{ url_for('static', filename='css/style.css') }}" rel="stylesheet">
</head>
<body>
    <div class="container-fluid">
        <nav class="navbar navbar-dark bg-dark">
            <div class="container-fluid">
                <span class="navbar-brand mb-0 h1">HomeRun Clone TV Tuner</span>
                <div class="d-flex">
                    <span id="status" class="badge bg-secondary me-2">Disconnected</span>
                    <span id="signal" class="badge bg-info">Signal: --</span>
                </div>
            </div>
        </nav>

        <div class="row mt-3">
            <div class="col-md-3">
                <div class="card">
                    <div class="card-header">
                        <h5>Channels</h5>
                        <button class="btn btn-sm btn-outline-primary" onclick="scanChannels()">Scan</button>
                    </div>
                    <div class="card-body">
                        <div id="channelList" class="list-group">
                            {% for channel, info in channels.items() %}
                            <button class="list-group-item list-group-item-action" 
                                    onclick="tuneChannel('{{ channel }}')">
                                {{ channel }} - {{ info.name }}
                            </button>
                            {% endfor %}
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="col-md-9">
                <div class="card">
                    <div class="card-header d-flex justify-content-between">
                        <h5 id="currentChannel">No Channel Selected</h5>
                        <div>
                            <button id="playBtn" class="btn btn-success" onclick="startStream()">Play</button>
                            <button id="stopBtn" class="btn btn-danger" onclick="stopStream()">Stop</button>
                        </div>
                    </div>
                    <div class="card-body">
                        <div id="videoContainer" class="ratio ratio-16x9">
                            <video id="videoPlayer" class="w-100" controls>
                                <source src="/stream" type="video/mp2t">
                                Your browser does not support the video tag.
                            </video>
                        </div>
                    </div>
                </div>
                
                <div class="card mt-3">
                    <div class="card-header">
                        <h6>System Information</h6>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6">
                                <small class="text-muted">Signal Strength: <span id="signalStrength">--</span>%</small>
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">Signal Quality: <span id="signalQuality">--</span>%</small>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.socket.io/4.0.0/socket.io.min.js"></script>
    <script src="{{ url_for('static', filename='js/app.js') }}"></script>
</body>
</html>'''
        
        with open(f"{self.project_root}/app/templates/index.html", 'w') as f:
            f.write(index_html)

    def create_static_files(self):
        """Create CSS and JavaScript files"""
        # CSS
        css_content = '''/* HomeRun Clone Styles */
.signal-meter {
    height: 10px;
    background-color: #e9ecef;
    border-radius: 5px;
    overflow: hidden;
}

.signal-bar {
    height: 100%;
    background: linear-gradient(90deg, #dc3545 0%, #ffc107 50%, #28a745 100%);
    transition: width 0.3s ease;
}

.channel-item.active {
    background-color: #007bff !important;
    color: white;
}

#videoContainer {
    background-color: #000;
    border-radius: 8px;
    overflow: hidden;
}

.card {
    border: 1px solid #dee2e6;
    border-radius: 8px;
}

.navbar-brand {
    font-weight: 600;
}

.status-connected {
    background-color: #28a745 !important;
}

.status-disconnected {
    background-color: #dc3545 !important;
}
'''
        
        with open(f"{self.project_root}/app/static/css/style.css", 'w') as f:
            f.write(css_content)

        # JavaScript
        js_content = '''// HomeRun Clone JavaScript
let socket;
let currentChannel = null;
let isStreaming = false;

// Initialize WebSocket connection
function initSocket() {
    socket = io();
    
    socket.on('connect', function() {
        updateStatus('Connected', 'status-connected');
    });
    
    socket.on('disconnect', function() {
        updateStatus('Disconnected', 'status-disconnected');
    });
    
    socket.on('status', function(data) {
        updateChannelInfo(data);
    });
}

function updateStatus(status, className) {
    const statusEl = document.getElementById('status');
    statusEl.textContent = status;
    statusEl.className = `badge bg-secondary me-2 ${className}`;
}

function updateChannelInfo(data) {
    currentChannel = data.current_channel;
    isStreaming = data.is_streaming;
    
    document.getElementById('currentChannel').textContent = 
        currentChannel ? `Channel ${currentChannel}` : 'No Channel Selected';
    
    if (data.signal_strength !== undefined) {
        document.getElementById('signalStrength').textContent = data.signal_strength;
        document.getElementById('signalQuality').textContent = data.signal_quality;
    }
    
    updateUI();
}

function updateUI() {
    const playBtn = document.getElementById('playBtn');
    const stopBtn = document.getElementById('stopBtn');
    
    playBtn.disabled = !currentChannel || isStreaming;
    stopBtn.disabled = !isStreaming;
    
    // Update active channel in list
    document.querySelectorAll('.list-group-item').forEach(item => {
        item.classList.remove('active');
    });
    
    if (currentChannel) {
        const activeItem = document.querySelector(`[onclick="tuneChannel('${currentChannel}')"]`);
        if (activeItem) {
            activeItem.classList.add('active');
        }
    }
}

async function scanChannels() {
    try {
        const response = await fetch('/api/scan', {method: 'POST'});
        const data = await response.json();
        
        if (data.success) {
            location.reload(); // Reload to show new channels
        } else {
            alert('Channel scan failed');
        }
    } catch (error) {
        console.error('Scan error:', error);
    }
}

async function tuneChannel(channel) {
    try {
        const response = await fetch(`/api/tune/${channel}`);
        const data = await response.json();
        
        if (data.success) {
            currentChannel = channel;
            updateChannelInfo({current_channel: channel, is_streaming: false});
        } else {
            alert('Failed to tune channel');
        }
    } catch (error) {
        console.error('Tune error:', error);
    }
}

async function startStream() {
    if (!currentChannel) {
        alert('Please select a channel first');
        return;
    }
    
    try {
        const response = await fetch(`/api/stream/${currentChannel}`);
        const data = await response.json();
        
        if (data.success) {
            isStreaming = true;
            updateUI();
            
            // Update video source
            const video = document.getElementById('videoPlayer');
            video.src = `/stream?channel=${currentChannel}&t=${Date.now()}`;
            video.load();
            video.play();
        } else {
            alert('Failed to start stream');
        }
    } catch (error) {
        console.error('Stream start error:', error);
    }
}

async function stopStream() {
    try {
        const response = await fetch('/api/stop');
        const data = await response.json();
        
        if (data.success) {
            isStreaming = false;
            updateUI();
            
            const video = document.getElementById('videoPlayer');
            video.pause();
            video.src = '';
        }
    } catch (error) {
        console.error('Stream stop error:', error);
    }
}

// Update status periodically
setInterval(async () => {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        updateChannelInfo(data);
    } catch (error) {
        console.error('Status update error:', error);
    }
}, 5000);

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initSocket();
    updateUI();
});
'''
        
        with open(f"{self.project_root}/app/static/js/app.js", 'w') as f:
            f.write(js_content)

    def create_config_files(self):
        """Create configuration files"""
        # Main configuration
        config = {
            "device": {
                "path": "/dev/video0",
                "name": "Hauppauge WinTV-dualHD"
            },
            "streaming": {
                "port": 8000,
                "format": "mpegts",
                "video_codec": "libx264",
                "audio_codec": "aac"
            },
            "web": {
                "port": 5000,
                "host": "0.0.0.0"
            },
            "jellyfin": {
                "enabled": False,
                "url": "",
                "api_key": ""
            }
        }
        
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)

        # Systemd service file
        service_content = f'''[Unit]
Description=HomeRun Clone TV Tuner
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory={self.project_root}/app
Environment=PATH={self.project_root}/venv/bin
ExecStart={self.project_root}/venv/bin/python app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
'''
        
        with open(f"{self.project_root}/homerun-clone.service", 'w') as f:
            f.write(service_content)

    def create_utility_scripts(self):
        """Create utility scripts for management"""
        # Device detection script
        device_script = '''#!/bin/bash
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
'''
        
        with open(f"{self.project_root}/scripts/detect_devices.sh", 'w') as f:
            f.write(device_script)
        os.chmod(f"{self.project_root}/scripts/detect_devices.sh", 0o755)

        # Service management script
        service_script = f'''#!/bin/bash
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
    *)
        echo "Usage: $0 {{start|stop|restart|status|enable|logs}}"
        exit 1
        ;;
esac
'''
        
        with open(f"{self.project_root}/scripts/service.sh", 'w') as f:
            f.write(service_script)
        os.chmod(f"{self.project_root}/scripts/service.sh", 0o755)

    def setup_system_service(self):
        """Setup systemd service"""
        service_src = f"{self.project_root}/homerun-clone.service"
        service_dst = "/etc/systemd/system/homerun-clone.service"
        
        if not self.run_command(f"sudo cp {service_src} {service_dst}", "Installing systemd service"):
            return False
        
        if not self.run_command("sudo systemctl daemon-reload", "Reloading systemd"):
            return False
        
        return True

    def detect_tuner_device(self):
        """Detect and verify Hauppauge tuner"""
        print("Detecting Hauppauge WinTV-dualHD...")
        
        # Check USB devices
        result = subprocess.run("lsusb | grep -i hauppauge", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Hauppauge device detected via USB:")
            print(result.stdout)
        else:
            print("⚠ Hauppauge device not detected via USB")
        
        # Check video devices
        video_devices = subprocess.run("ls /dev/video* 2>/dev/null || echo 'No video devices'", 
                                     shell=True, capture_output=True, text=True)
        print(f"Video devices found: {video_devices.stdout}")
        
        # Check V4L2 devices
        v4l2_info = subprocess.run("v4l2-ctl --list-devices 2>/dev/null || echo 'v4l2-ctl not available'", 
                                  shell=True, capture_output=True, text=True)
        print(f"V4L2 device info:\n{v4l2_info.stdout}")

    def run_setup(self):
        """Run the complete setup process"""
        print("="*60)
        print("HomeRunHD Clone Setup Starting...")
        print("="*60)
        
        try:
            self.create_directory_structure()
            
            if not self.install_system_dependencies():
                print("❌ Failed to install system dependencies")
                return False
            
            if not self.setup_python_environment():
                print("❌ Failed to setup Python environment")
                return False
            
            self.create_main_application()
            self.create_web_templates()
            self.create_static_files()
            self.create_config_files()
            self.create_utility_scripts()
            
            if not self.setup_system_service():
                print("❌ Failed to setup system service")
                return False
            
            self.detect_tuner_device()
            
            print("\n" + "="*60)
            print("✅ Setup completed successfully!")
            print("="*60)
            print("\nNext steps:")
            print(f"1. Check device detection: {self.project_root}/scripts/detect_devices.sh")
            print(f"2. Start the service: {self.project_root}/scripts/service.sh start")
            print(f"3. Enable auto-start: {self.project_root}/scripts/service.sh enable")
            print(f"4. Access web interface: http://192.168.1.171:{self.web_port}")
            print(f"5. Check logs: {self.project_root}/scripts/service.sh logs")
            print("\nFor Jellyfin integration:")
            print(f"- Configure Jellyfin to use: http://192.168.1.171:{self.streaming_port}")
            print(f"- Edit config file: {self.config_file}")
            
            return True
            
        except Exception as e:
            print(f"❌ Setup failed with error: {e}")
            return False

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("This script needs to be run with sudo privileges for system package installation")
        print("Run: sudo python3 homerun_clone_setup.py")
        sys.exit(1)
    
    setup = HomeRunCloneSetup()
    success = setup.run_setup()
    sys.exit(0 if success else 1)
