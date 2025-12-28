#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import subprocess
import json
import os
import threading
import time
import signal
import logging
from logging.handlers import RotatingFileHandler

# Setup logging
LOG_FILE = "/var/log/DVR.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger(__name__)

logger.info("="*70)
logger.info("DVR Application Starting...")
logger.info("="*70)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'homerun-clone-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Use project directory instead of hardcoded /opt path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CHANNELS_JSON = os.path.join(CONFIG_DIR, "channels.json")

logger.info(f"Base directory: {BASE_DIR}")
logger.info(f"Config directory: {CONFIG_DIR}")
logger.info(f"Channels JSON: {CHANNELS_JSON}")

class TVTuner:
    """
    DVB-based tuner controller for Hauppauge WinTV-dualHD
    Uses:
      - w_scan to scan channels
      - dvbv5-zap to tune a frequency / service
      - ffmpeg to stream from /dev/dvb/.../dvr0
    """
    def __init__(self):
        # Pick tuner 0 by default; you can make this configurable later
        self.frontend = "/dev/dvb/adapter0/frontend0"
        self.dvr = "/dev/dvb/adapter0/dvr0"

        self.channels = {}
        self.channel_order = []
        self.current_channel = None

        self.zap_proc = None
        self.ffmpeg_proc = None

        self.scan_thread = None
        self.scan_proc = None  # Track the w_scan process for cancellation
        self.scanning = False
        self.scan_cancelled = False

        self.load_channels()

    # -------------------------
    # Channels: load/save/sort
    # -------------------------
    def load_channels(self):
        os.makedirs(os.path.dirname(CHANNELS_JSON), exist_ok=True)
        if os.path.exists(CHANNELS_JSON):
            try:
                with open(CHANNELS_JSON, "r") as f:
                    content = f.read().strip()
                    if content:
                        self.channels = json.loads(content)
                    else:
                        self.channels = {}
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load channels.json: {e}")
                self.channels = {}
        else:
            self.channels = {}
        self._rebuild_order()

    def save_channels(self):
        os.makedirs(os.path.dirname(CHANNELS_JSON), exist_ok=True)
        with open(CHANNELS_JSON, "w") as f:
            json.dump(self.channels, f, indent=2)
        self._rebuild_order()

    def _chan_key(self, ch):
        # numeric sort for "2.1", "4.1"
        try:
            major, minor = ch.split(".")
            return (int(major), int(minor))
        except:
            try:
                return (int(float(ch)), 0)
            except:
                return (9999, 9999)

    def _rebuild_order(self):
        self.channel_order = sorted(self.channels.keys(), key=self._chan_key)

    # -------------------------
    # Scan using w_scan
    # -------------------------
    def scan_channels(self):
        """
        Run w_scan and extract channels into channels.json.
        w_scan -A 1 -ft -c US -X  (ATSC)
        We'll parse the generated XML enough to get virtual channel + name.
        For surfing, virtual channel is enough.
        """
        logger.info("="*70)
        logger.info("[SCAN] Starting channel scan...")
        self.scanning = True

        logger.info("[SCAN] Emitting progress: 0%")
        socketio.emit('scan_progress', {'progress': 0, 'channels_found': 0})

        # Write XML to temp then parse
        xml_path = os.path.join(CONFIG_DIR, "channels.xml")

        # Check if w_scan exists
        logger.info("[SCAN] Checking if w_scan is installed...")
        try:
            result = subprocess.run(['which', 'w_scan'], check=True, capture_output=True)
            logger.info(f"[SCAN] w_scan found at: {result.stdout.decode().strip()}")
        except subprocess.CalledProcessError:
            # w_scan not available - use demo mode
            logger.info("[SCAN] w_scan NOT found - switching to DEMO MODE")
            return self._scan_demo_mode()

        logger.info("[SCAN] Emitting progress: 10%")
        socketio.emit('scan_progress', {'progress': 10, 'channels_found': 0, 'status': 'Starting scan...'})

        # Run w_scan with real-time output monitoring
        # w_scan outputs XML to stdout and progress to stderr
        cmd = f"w_scan -A 1 -ft -c US -X"
        logger.info(f"[SCAN] Running w_scan command: {cmd}")
        logger.info(f"[SCAN] Saving XML output to: {xml_path}")
        logger.info("[SCAN] *** This will take 5-10 minutes, monitoring progress... ***")

        # Start w_scan and monitor its output in real-time
        # Redirect stdout to XML file, capture stderr for monitoring
        xml_file = open(xml_path, 'w')
        self.scan_proc = subprocess.Popen(
            cmd.split(),
            stdout=xml_file,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        channels_found = 0
        current_freq = ""
        recent_channels = []  # Track recently found channels

        # Monitor stderr in real-time (w_scan outputs to stderr)
        while True:
            if self.scan_cancelled:
                logger.warning("[SCAN] Scan cancelled by user!")
                self.scan_proc.terminate()
                try:
                    self.scan_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.scan_proc.kill()
                xml_file.close()
                self.scanning = False
                self.scan_cancelled = False
                socketio.emit('scan_complete', {'success': False, 'error': 'Scan cancelled', 'channels_found': 0, 'channels': {}})
                return {"success": False, "error": "Scan cancelled"}

            line = self.scan_proc.stderr.readline()
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            logger.info(f"[SCAN] {line}")

            # Parse w_scan output for frequency info
            # Matches patterns like "177000:" or "f=177000 kHz" or "177000 kHz"
            import re
            freq_match = re.search(r'(?:^|f=)(\d{5,6})\s*:?\s*(?:kHz)?', line)
            if freq_match:
                freq_khz = freq_match.group(1)
                freq_mhz = int(freq_khz) / 1000
                new_freq = f"{freq_mhz:.1f} MHz"
                if new_freq != current_freq:  # Only emit if frequency changed
                    current_freq = new_freq
                    socketio.emit('scan_progress', {
                        'progress': 10,
                        'channels_found': channels_found,
                        'status': f'Scanning {current_freq}',
                        'recent_channels': recent_channels[-5:]  # Last 5 channels
                    })

            # Parse w_scan output for found services/channels
            # Format: "service is running. Channel number: 2:1. Name: 'WJBK   '"
            if "service is running" in line.lower():
                channels_found += 1

                # Extract channel number and name
                channel_match = re.search(r'Channel number:\s*(\d+):(\d+)\.\s*Name:\s*["\']([^"\']+)["\']', line)
                if channel_match:
                    major = channel_match.group(1)
                    minor = channel_match.group(2)
                    name = channel_match.group(3).strip()
                    channel_display = f"{major}.{minor} {name}"
                    recent_channels.append(channel_display)
                    logger.info(f"[SCAN] Found service #{channels_found}: {channel_display}")

                    socketio.emit('scan_progress', {
                        'progress': 10,
                        'channels_found': channels_found,
                        'status': f'Found: {channel_display}',
                        'recent_channels': recent_channels[-5:]  # Last 5 channels
                    })
                else:
                    # Fallback if parsing fails
                    logger.info(f"[SCAN] Found service #{channels_found}")
                    socketio.emit('scan_progress', {
                        'progress': 10,
                        'channels_found': channels_found,
                        'status': f'Found {channels_found} services at {current_freq}',
                        'recent_channels': recent_channels[-5:]
                    })

        # Wait for process to complete
        self.scan_proc.wait()
        returncode = self.scan_proc.returncode
        xml_file.close()  # Close the XML file now that w_scan has finished

        logger.info(f"[SCAN] w_scan completed with return code: {returncode}")
        logger.info(f"[SCAN] Found {channels_found} services during scan")

        # Diagnostic logging for XML file
        if os.path.exists(xml_path):
            file_size = os.path.getsize(xml_path)
            logger.info(f"[SCAN] XML file exists at: {xml_path}")
            logger.info(f"[SCAN] XML file size: {file_size} bytes")
            if file_size > 0:
                with open(xml_path, 'r') as f:
                    first_lines = ''.join(f.readlines()[:5])
                    logger.info(f"[SCAN] First lines of XML:\n{first_lines}")
            else:
                logger.error("[SCAN] XML file is empty!")
        else:
            logger.error(f"[SCAN] XML file does not exist at: {xml_path}")

        logger.info("[SCAN] Emitting progress: 70%")
        socketio.emit('scan_progress', {'progress': 70, 'channels_found': channels_found, 'status': 'Parsing results...'})

        if returncode != 0 and not self.scan_cancelled:
            self.scanning = False
            error_msg = "w_scan failed with non-zero exit code"
            socketio.emit('scan_complete', {'success': False, 'error': error_msg, 'channels_found': 0, 'channels': {}})
            return {"success": False, "error": error_msg}

        # Minimal XML parse without extra deps
        # w_scan XML contains <channel> entries with <name> and sometimes <service_id> etc.
        # We'll build a best-effort mapping: "major.minor" -> {"name": "..."}
        new_channels = {}
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(xml_path)
            root = tree.getroot()

            socketio.emit('scan_progress', {'progress': 80, 'channels_found': 0})

            # Heuristic: many w_scan XMLs have <channel> nodes
            for ch in root.iter():
                if ch.tag.lower().endswith("channel"):
                    name = None
                    vchan = None
                    # Look for children like <name>, <service_name>, <channel_name>
                    for c in list(ch):
                        tag = c.tag.lower()
                        txt = (c.text or "").strip()
                        if not txt:
                            continue
                        if "name" == tag or tag.endswith("name"):
                            # Don't overwrite if we already set a more specific field
                            if name is None:
                                name = txt
                        if "channel" in tag and ("major" in tag or "minor" in tag):
                            # some formats split major/minor; handled below
                            pass

                    # Many w_scan XML formats actually store the channel string in the <name>
                    # like "WXYZ-DT 7.1" or similar. Extract trailing N.N pattern.
                    if name:
                        import re
                        m = re.search(r"(\d{1,3}\.\d{1,3})", name)
                        if m:
                            vchan = m.group(1)

                    # If no vchan extracted, skip (we still keep raw name if you want later)
                    if vchan:
                        # Clean name to station-ish string (optional)
                        display = name
                        new_channels[vchan] = {"name": display}
                        socketio.emit('scan_progress', {'progress': 80 + (len(new_channels) % 10), 'channels_found': len(new_channels)})

        except Exception as e:
            self.scanning = False
            error_msg = f"XML parse failed: {e}"
            socketio.emit('scan_complete', {'success': False, 'error': error_msg, 'channels_found': 0, 'channels': {}})
            return {"success": False, "error": error_msg}

        self.channels = new_channels
        self.save_channels()
        self.scanning = False

        socketio.emit('scan_progress', {'progress': 100, 'channels_found': len(self.channels)})
        socketio.emit('scan_complete', {'success': True, 'channels_found': len(self.channels), 'channels': self.channels})

        return {"success": True, "channels_found": len(self.channels)}

    def scan_channels_background(self):
        """Run scan in background thread"""
        logger.info("[BACKGROUND] Thread started - calling scan_channels()")
        try:
            result = self.scan_channels()
            logger.info(f"[BACKGROUND] scan_channels() returned: {result}")
        except Exception as e:
            logger.error(f"[BACKGROUND] ERROR in background scan: {e}")
            import traceback
            traceback.print_exc()
            self.scanning = False
            socketio.emit('scan_complete', {'success': False, 'error': str(e), 'channels_found': 0, 'channels': {}})

    def _scan_demo_mode(self):
        """Demo mode scan for testing without hardware"""
        import time

        logger.info("="*70)
        logger.info("[DEMO] Entering DEMO MODE - simulating channel scan")

        # Simulate scanning with demo channels
        demo_channels = {
            "2.1": {"name": "WJBK-TV 2.1 (FOX 2)"},
            "4.1": {"name": "WDIV-TV 4.1 (NBC 4)"},
            "7.1": {"name": "WXYZ-TV 7.1 (ABC 7)"},
            "7.2": {"name": "WXYZ-TV 7.2 (Bounce)"},
            "9.1": {"name": "CBET-DT 9.1 (CBC)"},
            "20.1": {"name": "WMYD 20.1 (MyNet)"},
            "50.1": {"name": "WKBD-TV 50.1 (CW)"},
            "56.1": {"name": "WTVS 56.1 (PBS)"},
        }

        logger.info(f"[DEMO] Will simulate finding {len(demo_channels)} channels")

        total = len(demo_channels)
        for i, (channel, info) in enumerate(demo_channels.items()):
            progress = int((i + 1) / total * 100)
            logger.info(f"[DEMO] Progress: {progress}% - Found channel {channel}: {info['name']}")
            logger.info(f"[DEMO] Emitting scan_progress event: progress={progress}, channels_found={i+1}")
            socketio.emit('scan_progress', {'progress': progress, 'channels_found': i + 1})
            time.sleep(0.5)  # Simulate scanning time

        logger.info("[DEMO] Saving channels to JSON...")
        self.channels = demo_channels
        self.save_channels()
        self.scanning = False
        logger.info(f"[DEMO] Saved {len(self.channels)} channels to {CHANNELS_JSON}")

        logger.info("[DEMO] Emitting final progress (100%)")
        socketio.emit('scan_progress', {'progress': 100, 'channels_found': len(self.channels)})

        logger.info("[DEMO] Emitting scan_complete event")
        socketio.emit('scan_complete', {'success': True, 'channels_found': len(self.channels), 'channels': self.channels})

        logger.info("[DEMO] Demo scan COMPLETE!")
        logger.info("="*70)
        return {"success": True, "channels_found": len(self.channels)}

    # -------------------------
    # Tuning
    # -------------------------
    def stop_zap(self):
        if self.zap_proc and self.zap_proc.poll() is None:
            self.zap_proc.terminate()
            try:
                self.zap_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.zap_proc.kill()
        self.zap_proc = None

    def tune_channel(self, channel):
        if channel not in self.channels:
            return False

        # Stop any existing zap (tune) process
        self.stop_zap()

        # We need a channels.conf for dvbv5-zap.
        # Easiest: generate one entry that just identifies the target "virtual channel" label,
        # but dvbv5-zap really wants a real service tuning configuration.
        #
        # Practical workaround: use w_scan output in "zap" format instead of XML.
        #
        # So: require that channels.zap exists in config dir
        zap_path = os.path.join(CONFIG_DIR, "channels.zap")
        if not os.path.exists(zap_path):
            # Generate it (czap format works for dvbv5-zap)
            os.makedirs(CONFIG_DIR, exist_ok=True)
            cmd = f"w_scan -A 1 -ft -c US -o 1 > {zap_path}"
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode != 0:
                print("Failed to generate channels.zap:", r.stderr or r.stdout)

        # Start dvbv5-zap in "record" mode so it holds the tuner
        # NOTE: dvbv5-zap expects a channel NAME as listed in channels.zap.
        # Many entries are station names, not "2.1". We'll search for a matching virtual channel string.
        entry_name = self._find_zap_entry_name(zap_path, channel)
        if not entry_name:
            print(f"Could not map virtual channel {channel} to a zap entry name")
            return False

        cmd = [
            "dvbv5-zap",
            "-a", self.frontend,
            "-c", zap_path,
            entry_name,
            "-r",
            "-q"
        ]
        self.zap_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.current_channel = channel
        return True

    def _find_zap_entry_name(self, zap_path, channel):
        """
        channels.zap lines look like:
          NAME:freq:...
        We try to find a line whose NAME contains "2.1" etc, otherwise fallback to first match by major.
        """
        import re
        try:
            with open(zap_path, "r", errors="ignore") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            # First, exact contains "2.1"
            for ln in lines:
                name = ln.split(":", 1)[0]
                if channel in name:
                    return name
            # Next, try contains "2-1" style
            alt = channel.replace(".", "-")
            for ln in lines:
                name = ln.split(":", 1)[0]
                if alt in name:
                    return name
            # Next, match by major only (dangerous but better than nothing)
            major = channel.split(".", 1)[0]
            for ln in lines:
                name = ln.split(":", 1)[0]
                if re.search(rf"\b{re.escape(major)}\b", name):
                    return name
        except Exception:
            return None
        return None

    # -------------------------
    # Streaming
    # -------------------------
    def stop_stream(self):
        if self.ffmpeg_proc and self.ffmpeg_proc.poll() is None:
            self.ffmpeg_proc.terminate()
            try:
                self.ffmpeg_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.ffmpeg_proc.kill()
        self.ffmpeg_proc = None

    def start_stream(self, channel):
        if not self.tune_channel(channel):
            return False

        # Kill old ffmpeg; start new one that writes MPEG-TS to stdout
        self.stop_stream()
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", self.dvr,
            "-c", "copy",
            "-f", "mpegts",
            "pipe:1"
        ]
        self.ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True

    def surf(self, direction):
        if not self.channel_order:
            self._rebuild_order()
        if not self.channel_order:
            return None

        if self.current_channel not in self.channel_order:
            # pick first if nothing tuned yet
            target = self.channel_order[0]
        else:
            idx = self.channel_order.index(self.current_channel)
            if direction == "next":
                target = self.channel_order[(idx + 1) % len(self.channel_order)]
            else:
                target = self.channel_order[(idx - 1) % len(self.channel_order)]

        return target

tuner = TVTuner()

@app.route("/")
def index():
    return render_template("index.html", channels=tuner.channels)

@app.route("/api/channels")
def api_channels():
    return jsonify(tuner.channels)

@app.route("/api/scan", methods=["POST"])
def api_scan():
    """Start a channel scan in background"""
    logger.info("="*70)
    logger.info("[API] /api/scan endpoint called")

    if tuner.scanning:
        logger.warning("[API] ERROR: Scan already in progress")
        return jsonify({"success": False, "error": "Scan already in progress", "can_cancel": True})

    # Start scan in background thread
    logger.info("[API] Starting background thread for scan...")
    tuner.scan_cancelled = False
    tuner.scan_thread = threading.Thread(target=tuner.scan_channels_background, daemon=True)
    tuner.scan_thread.start()
    logger.info(f"[API] Background thread started: {tuner.scan_thread}")
    logger.info("="*70)

    return jsonify({"success": True, "message": "Scan started"})

@app.route("/api/scan/cancel", methods=["POST"])
def api_scan_cancel():
    """Cancel an active scan"""
    logger.warning("="*70)
    logger.warning("[API] /api/scan/cancel endpoint called")

    if not tuner.scanning:
        logger.warning("[API] No scan in progress to cancel")
        return jsonify({"success": False, "error": "No scan in progress"})

    logger.warning("[API] Cancelling active scan...")
    tuner.scan_cancelled = True

    return jsonify({"success": True, "message": "Scan cancellation requested"})

@app.route("/api/tune/<channel>", methods=["GET", "POST"])
def api_tune(channel):
    ok = tuner.tune_channel(channel)
    return jsonify({"success": ok, "channel": channel})

@app.route("/api/play/<channel>", methods=["POST"])
def api_play(channel):
    ok = tuner.start_stream(channel)
    return jsonify({"success": ok, "channel": channel})

@app.route("/api/stream/<channel>", methods=["POST"])
def api_stream(channel):
    """Start streaming a channel - called by frontend"""
    ok = tuner.start_stream(channel)
    return jsonify({"success": ok, "channel": channel})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    tuner.stop_stream()
    tuner.stop_zap()
    return jsonify({"success": True})

@app.route("/api/status", methods=["GET"])
def api_status():
    """Return current tuner status"""
    return jsonify({
        "current_channel": tuner.current_channel,
        "is_streaming": tuner.ffmpeg_proc is not None and tuner.ffmpeg_proc.poll() is None,
        "channels": tuner.channels,
        "signal_strength": 0,  # TODO: implement real signal strength reading
        "signal_quality": 0
    })

@app.route("/api/quick-scan", methods=["POST"])
def api_quick_scan():
    """Quick scan of popular channels (same as full scan for now)"""
    if tuner.scanning:
        return jsonify({"success": False, "error": "Scan already in progress"})

    # Start scan in background thread
    tuner.scan_thread = threading.Thread(target=tuner.scan_channels_background, daemon=True)
    tuner.scan_thread.start()

    return jsonify({"success": True, "message": "Quick scan started"})

@app.route("/api/surf/<direction>", methods=["POST"])
def api_surf(direction):
    if direction not in ("next", "prev"):
        return jsonify({"success": False, "error": "direction must be next or prev"}), 400
    target = tuner.surf("next" if direction == "next" else "prev")
    if not target:
        return jsonify({"success": False, "error": "no channels"}), 400
    ok = tuner.start_stream(target)
    return jsonify({"success": ok, "channel": target})

@app.route("/stream.ts")
def stream_ts():
    """
    Serve the MPEG-TS stream from ffmpeg stdout.
    Client must have started /api/play/<channel> first.
    """
    if not tuner.ffmpeg_proc or tuner.ffmpeg_proc.stdout is None:
        return ("Stream not running", 404)

    def gen():
        try:
            while True:
                chunk = tuner.ffmpeg_proc.stdout.read(188 * 50)
                if not chunk:
                    break
                yield chunk
        except GeneratorExit:
            pass

    return Response(gen(), mimetype="video/mp2t")

@socketio.on("connect")
def on_connect():
    logger.info("[WEBSOCKET] Client connected!")
    emit("status", {"current_channel": tuner.current_channel, "channels": tuner.channels})

@socketio.on("disconnect")
def on_disconnect():
    logger.info("[WEBSOCKET] Client disconnected!")

if __name__ == "__main__":
    logger.info("Starting SocketIO server on 0.0.0.0:5000")
    logger.info("Access the app at: http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)

