#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import subprocess
import json
import os
import threading
import time
import signal

app = Flask(__name__)
app.config['SECRET_KEY'] = 'homerun-clone-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

CHANNELS_JSON = "/opt/homerun-clone/config/channels.json"

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
        self.adapter_index = self._get_adapter_index()

        self.channels = {}
        self.channel_order = []
        self.current_channel = None

        self.zap_proc = None
        self.ffmpeg_proc = None

        self.load_channels()
        self.scan_status = {"status": "idle"}

    # -------------------------
    # Channels: load/save/sort
    # -------------------------
    def load_channels(self):
        os.makedirs(os.path.dirname(CHANNELS_JSON), exist_ok=True)
        if os.path.exists(CHANNELS_JSON):
            with open(CHANNELS_JSON, "r") as f:
                self.channels = json.load(f)
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
    def _get_adapter_index(self):
        env_adapter = os.environ.get("HOMERUN_ADAPTER")
        if env_adapter is not None:
            try:
                return int(env_adapter)
            except ValueError:
                pass
        import re
        match = re.search(r"adapter(\d+)", self.frontend)
        if match:
            return int(match.group(1))
        return 0

    def scan_channels(self):
        """
        Run w_scan and extract channels into channels.json.
        w_scan -A 1 -ft -c US -X  (ATSC)
        We'll parse the generated XML enough to get virtual channel + name.
        For surfing, virtual channel is enough.
        """
        # Write XML to temp then parse
        xml_path = "/opt/homerun-clone/config/channels.xml"
        cmd = ["w_scan", "-A", "1", "-ft", "-c", "US", "-X", "-a", str(self.adapter_index)]
        try:
            with open(xml_path, "w") as xml_file:
                r = subprocess.run(cmd, stdout=xml_file, stderr=subprocess.PIPE, text=True)
        except OSError as e:
            return {"success": False, "error": f"Failed to run w_scan: {e}"}
        if r.returncode != 0:
            return {"success": False, "error": r.stderr or "w_scan failed"}

        # Minimal XML parse without extra deps
        # w_scan XML contains <channel> entries with <name> and sometimes <service_id> etc.
        # We'll build a best-effort mapping: "major.minor" -> {"name": "..."}
        new_channels = {}
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(xml_path)
            root = tree.getroot()

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

        except Exception as e:
            return {"success": False, "error": f"XML parse failed: {e}"}

        self.channels = new_channels
        self.save_channels()
        return {"success": True, "channels_found": len(self.channels)}

    def run_scan(self):
        self.scan_status = {"status": "running"}
        socketio.emit("scan_progress", {"progress": 0, "channels_found": 0})
        result = self.scan_channels()
        if result.get("success"):
            self.scan_status = {"status": "complete"}
            result["channels"] = self.channels
            socketio.emit("scan_complete", result)
        else:
            self.scan_status = {"status": "error", "message": result.get("error")}
            socketio.emit("scan_complete", {"success": False, "error": result.get("error")})
        return result

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
        # So: require that /opt/homerun-clone/config/channels.zap exists.
        zap_path = "/opt/homerun-clone/config/channels.zap"
        if not os.path.exists(zap_path):
            # Generate it (czap format works for dvbv5-zap)
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

@app.route("/api/status")
def api_status():
    return jsonify({
        "current_channel": tuner.current_channel,
        "is_streaming": tuner.ffmpeg_proc is not None and tuner.ffmpeg_proc.poll() is None,
        "channels": tuner.channels,
        "scan_status": tuner.scan_status,
    })

@app.route("/api/scan", methods=["POST"])
def api_scan():
    payload = request.get_json(silent=True) or {}
    background = payload.get("background", False)
    if background:
        thread = threading.Thread(target=tuner.run_scan, daemon=True)
        thread.start()
        return jsonify({"success": True, "background": True})
    result = tuner.run_scan()
    tuner.load_channels()
    return jsonify(result)

@app.route("/api/tune/<channel>", methods=["POST"])
def api_tune(channel):
    ok = tuner.tune_channel(channel)
    return jsonify({"success": ok, "channel": channel})

@app.route("/api/play/<channel>", methods=["POST"])
def api_play(channel):
    ok = tuner.start_stream(channel)
    return jsonify({"success": ok, "channel": channel})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    tuner.stop_stream()
    tuner.stop_zap()
    return jsonify({"success": True})

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
    emit("status", {
        "current_channel": tuner.current_channel,
        "channels": tuner.channels,
        "scan_status": tuner.scan_status,
    })

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
