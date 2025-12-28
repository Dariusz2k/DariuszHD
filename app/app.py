#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit
import subprocess
import json
import os
import threading
import time
import signal
import re
import bisect
import math
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone

app = Flask(__name__)
app.config['SECRET_KEY'] = 'homerun-clone-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

CHANNELS_JSON = "/opt/homerun-clone/config/channels.json"
REGION_DATA_PATH = os.path.join(os.path.dirname(__file__), "scan_regions.json")
GUIDE_XML = "/opt/homerun-clone/config/guide.xml"
DVR_SCHEDULE_JSON = "/opt/homerun-clone/config/dvr_schedule.json"
RECORDINGS_DIR = "/opt/homerun-clone/recordings"

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
        self.recording_proc = None

        self.load_channels()
        self.scan_status = {"status": "idle"}
        self.scan_proc = None
        self.scan_cancel = threading.Event()
        self.scan_lock = threading.Lock()
        self.keep_partial_scan = False
        self.scan_region_ids = []
        self.scan_frequency_list = []
        self.scan_frequency_set = set()

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
                if ch.startswith("rf-"):
                    return (int(ch.replace("rf-", "")), 0)
                return (int(float(ch)), 0)
            except:
                return (9999, 9999)

    def _rebuild_order(self):
        self.channel_order = sorted(self.channels.keys(), key=self._chan_key)

    # -------------------------
    # Scan using w_scan
    # -------------------------
    def _load_regions(self):
        if not os.path.exists(REGION_DATA_PATH):
            return []
        with open(REGION_DATA_PATH, "r") as f:
            return json.load(f)

    def _get_region_frequencies(self, region_ids):
        regions = self._load_regions()
        freq_set = set()
        for region in regions:
            if region.get("id") in region_ids:
                for freq in region.get("frequencies_khz", []):
                    try:
                        freq_set.add(int(freq))
                    except (TypeError, ValueError):
                        continue
        return sorted(freq_set)

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

    def scan_channels(self, xml_path, frequency_filter=None):
        """
        Parse w_scan XML output and extract channels into channels.json.
        We'll parse the generated XML enough to get virtual channel + name.
        For surfing, virtual channel is enough.
        """
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
                    freq_khz = None
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
                        if "frequency" in tag or tag.endswith("freq"):
                            try:
                                freq_khz = int(float(txt))
                            except ValueError:
                                pass
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
                        if frequency_filter and freq_khz is not None and freq_khz not in frequency_filter:
                            continue
                        # Clean name to station-ish string (optional)
                        display = name or vchan
                        new_channels[vchan] = {"name": display, "frequency_khz": freq_khz}
                    elif freq_khz is not None:
                        if frequency_filter and freq_khz not in frequency_filter:
                            continue
                        vchan = f"rf-{freq_khz}"
                        display = name or f"RF {freq_khz / 1000:.3f} MHz"
                        new_channels[vchan] = {"name": display, "frequency_khz": freq_khz}

        except Exception as e:
            return {"success": False, "error": f"XML parse failed: {e}"}

        self.channels = new_channels
        self.save_channels()
        return {"success": True, "channels_found": len(self.channels)}

    def refresh_channels_from_xml(self, xml_path, frequency_filter=None):
        result = self.scan_channels(xml_path, frequency_filter)
        if result.get("success"):
            socketio.emit("scan_progress", {
                "channels_found": result.get("channels_found", 0),
                "channels": self.channels,
                "progress": self.scan_status.get("progress", 0),
                "frequency_khz": self.scan_status.get("frequency_khz"),
            })
        return result

    def add_frequency_channel(self, frequency_khz):
        channel_key = f"rf-{frequency_khz}"
        if channel_key in self.channels:
            return False
        display = f"RF {frequency_khz / 1000:.3f} MHz"
        self.channels[channel_key] = {
            "name": display,
            "frequency_khz": frequency_khz
        }
        self.save_channels()
        return True

    def cancel_scan(self, keep_channels=False):
        self.keep_partial_scan = keep_channels
        self.scan_cancel.set()
        if self.scan_proc and self.scan_proc.poll() is None:
            self.scan_proc.terminate()
            try:
                self.scan_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.scan_proc.kill()
        # Let run_scan finalize cleanup, emit events, and persist channels.

    def _emit_scan_progress(self, frequency_khz, channels_found, progress):
        self.scan_status = {
            "status": "running",
            "frequency_khz": frequency_khz,
            "channels_found": channels_found,
            "progress": progress,
        }
        socketio.emit("scan_progress", {
            "frequency_khz": frequency_khz,
            "channels_found": channels_found,
            "progress": progress
        })

    def run_scan(self):
        with self.scan_lock:
            self.scan_status = {"status": "running"}
            self.scan_cancel.clear()
            socketio.emit("scan_progress", {"progress": 0, "channels_found": 0})

            xml_path = "/opt/homerun-clone/config/channels.xml"
            cmd = ["w_scan", "-A", "1", "-ft", "-c", "US", "-X", "-a", str(self.adapter_index)]
            channels_found = 0
            frequency_khz = None
            stderr_output = []
            freq_min_khz = 54000
            freq_max_khz = 858000
            active_frequencies = self.scan_frequency_list

            try:
                with open(xml_path, "w") as xml_file:
                    self.scan_proc = subprocess.Popen(
                        cmd,
                        stdout=xml_file,
                        stderr=subprocess.PIPE,
                        text=True
                    )
                    if self.scan_proc.stderr:
                        for line in self.scan_proc.stderr:
                            stderr_output.append(line)
                            if self.scan_cancel.is_set():
                                break
                            line = line.strip()
                            if not line:
                                continue
                            match = re.search(r"(\d+):", line)
                            if match:
                                frequency_khz = int(match.group(1))
                                if active_frequencies:
                                    index = bisect.bisect_right(active_frequencies, frequency_khz)
                                    progress = int((index / len(active_frequencies)) * 100)
                                else:
                                    progress = int(
                                        max(
                                            0,
                                            min(
                                                100,
                                                ((frequency_khz - freq_min_khz) / (freq_max_khz - freq_min_khz)) * 100,
                                            ),
                                        )
                                    )
                                if "signal ok" in line:
                                    channels_found += 1
                                    self.add_frequency_channel(frequency_khz)
                                    self.refresh_channels_from_xml(xml_path, self.scan_frequency_set or None)
                                self._emit_scan_progress(frequency_khz, channels_found, progress)
                    self.scan_proc.wait()
            except OSError as e:
                self.scan_proc = None
                return {"success": False, "error": f"Failed to run w_scan: {e}"}

        if self.scan_cancel.is_set():
            self.scan_status = {"status": "canceled"}
            self.scan_proc = None
            if self.keep_partial_scan:
                result = self.scan_channels(xml_path, self.scan_frequency_set or None)
                if result.get("success"):
                    self.scan_status = {
                        "status": "canceled",
                        "channels_found": result.get("channels_found", 0),
                    }
                    socketio.emit(
                        "scan_complete",
                        {
                            "success": True,
                            "canceled": True,
                            "channels_found": result.get("channels_found", 0),
                            "channels": self.channels,
                        },
                    )
                    return {
                        "success": True,
                        "canceled": True,
                        "channels_found": result.get("channels_found", 0),
                        "channels": self.channels,
                    }
            return {"success": False, "error": "Scan canceled"}

        if not self.scan_proc or self.scan_proc.returncode != 0:
            stderr_text = "".join(stderr_output).strip()
            self.scan_proc = None
            return {"success": False, "error": stderr_text or "w_scan failed"}

        self.scan_proc = None
        result = self.scan_channels(xml_path, self.scan_frequency_set or None)
        if result.get("success"):
            result_channels_found = result.get("channels_found", 0)
            total_found = max(channels_found, result_channels_found)
            self.scan_status = {"status": "complete"}
            self._emit_scan_progress(frequency_khz, total_found, 100)
            result["channels"] = self.channels
            socketio.emit(
                "scan_complete",
                {
                    "success": True,
                    "channels_found": total_found,
                    "channels": self.channels,
                },
            )
        else:
            self.scan_status = {"status": "error", "message": result.get("error")}
            socketio.emit("scan_complete", {"success": False, "error": result.get("error")})
        return result

    def is_scan_active(self):
        return self.scan_proc is not None and self.scan_proc.poll() is None

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
        entry_name = self._find_zap_entry_name(zap_path, channel, self.channels[channel])
        if not entry_name:
            print(f"Could not map virtual channel {channel} to a zap entry name")
            return False

        cmd = [
            "dvbv5-zap",
            "-a", str(self.adapter_index),
            "-c", zap_path,
            entry_name,
            "-r",
            "-q"
        ]
        self.zap_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.current_channel = channel
        return True

    def _find_zap_entry_name(self, zap_path, channel, channel_info):
        """
        channels.zap lines look like:
          NAME:freq:...
        We try to find a line whose NAME contains "2.1" etc, otherwise fallback to first match by major.
        """
        import re
        try:
            with open(zap_path, "r", errors="ignore") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            frequency_khz = channel_info.get("frequency_khz")
            if frequency_khz is not None:
                for ln in lines:
                    parts = ln.split(":")
                    if len(parts) > 1:
                        try:
                            if int(parts[1]) == int(frequency_khz):
                                return parts[0]
                        except ValueError:
                            continue
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

    def start_recording(self, channel, duration_seconds, title):
        if self.recording_proc and self.recording_proc.poll() is None:
            return False, "Recording already in progress"
        if not self.tune_channel(channel):
            return False, "Failed to tune channel"

        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        safe_title = re.sub(r"[^a-zA-Z0-9_\-]+", "_", title or channel)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{safe_title}_{timestamp}.ts"
        file_path = os.path.join(RECORDINGS_DIR, filename)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", self.dvr,
            "-t", str(duration_seconds),
            "-c", "copy",
            file_path
        ]
        self.recording_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True, file_path

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
schedule_lock = threading.Lock()
schedule_items = []

def load_schedule():
    global schedule_items
    if os.path.exists(DVR_SCHEDULE_JSON):
        with open(DVR_SCHEDULE_JSON, "r") as f:
            schedule_items = json.load(f)
    else:
        schedule_items = []

def save_schedule():
    os.makedirs(os.path.dirname(DVR_SCHEDULE_JSON), exist_ok=True)
    with open(DVR_SCHEDULE_JSON, "w") as f:
        json.dump(schedule_items, f, indent=2)

def parse_xmltv_datetime(value):
    if not value:
        return None
    try:
        value = value.strip()
        dt = datetime.strptime(value[:14], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None

def load_guide():
    if not os.path.exists(GUIDE_XML):
        return {"channels": [], "programs": [], "error": "Guide file not found"}
    import xml.etree.ElementTree as ET
    tree = ET.parse(GUIDE_XML)
    root = tree.getroot()
    channels = []
    programs = []
    for ch in root.findall("channel"):
        ch_id = ch.get("id")
        display = ch.findtext("display-name") or ch_id
        channels.append({"id": ch_id, "name": display})
    for prog in root.findall("programme"):
        channel_id = prog.get("channel")
        start = parse_xmltv_datetime(prog.get("start"))
        stop = parse_xmltv_datetime(prog.get("stop"))
        title = prog.findtext("title") or "Untitled"
        desc = prog.findtext("desc") or ""
        programs.append({
            "channel_id": channel_id,
            "start": start,
            "stop": stop,
            "title": title,
            "description": desc
        })
    return {"channels": channels, "programs": programs}

def schedule_worker():
    while True:
        now = datetime.now(timezone.utc)
        with schedule_lock:
            for item in schedule_items:
                if item.get("status") != "scheduled":
                    continue
                start_time = item.get("start_time")
                if not start_time:
                    continue
                try:
                    start_dt = datetime.fromisoformat(start_time)
                except ValueError:
                    continue
                if start_dt <= now:
                    ok, result = tuner.start_recording(
                        item.get("channel"),
                        int(item.get("duration_seconds", 0)),
                        item.get("title", "")
                    )
                    if ok:
                        item["status"] = "recording"
                        item["recording_path"] = result
                        item["started_at"] = now.isoformat()
                    else:
                        item["status"] = "error"
                        item["error"] = result
            save_schedule()
        time.sleep(5)

load_schedule()
threading.Thread(target=schedule_worker, daemon=True).start()

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

@app.route("/api/scan/regions")
def api_scan_regions():
    return jsonify({"regions": tuner._load_regions()})

@app.route("/api/location")
def api_location():
    try:
        with urllib.request.urlopen("https://ipapi.co/json/", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            return jsonify({
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "city": data.get("city"),
                "region": data.get("region"),
                "country": data.get("country_name"),
            })
    except (urllib.error.URLError, json.JSONDecodeError):
        return jsonify({"error": "Unable to determine location"}), 502

@app.route("/api/guide")
def api_guide():
    return jsonify(load_guide())

@app.route("/api/dvr/schedule", methods=["GET", "POST"])
def api_dvr_schedule():
    if request.method == "GET":
        return jsonify({"schedule": schedule_items})
    payload = request.get_json(silent=True) or {}
    channel = payload.get("channel")
    start_time = payload.get("start_time")
    duration_seconds = int(payload.get("duration_seconds", 0))
    title = payload.get("title", channel)
    if not channel or not start_time or duration_seconds <= 0:
        return jsonify({"success": False, "error": "channel, start_time, duration_seconds required"}), 400
    item = {
        "id": str(uuid.uuid4()),
        "channel": channel,
        "title": title,
        "start_time": start_time,
        "duration_seconds": duration_seconds,
        "status": "scheduled"
    }
    with schedule_lock:
        schedule_items.append(item)
        save_schedule()
    return jsonify({"success": True, "item": item})

@app.route("/api/dvr/schedule/<schedule_id>", methods=["DELETE"])
def api_dvr_schedule_delete(schedule_id):
    with schedule_lock:
        before = len(schedule_items)
        schedule_items[:] = [item for item in schedule_items if item.get("id") != schedule_id]
        if len(schedule_items) == before:
            return jsonify({"success": False, "error": "not found"}), 404
        save_schedule()
    return jsonify({"success": True})

@app.route("/favicon.ico")
def favicon():
    return ("", 204)

@app.route("/css/fonts/<path:filename>")
def missing_fonts(filename):
    return ("", 204)

@app.route("/api/scan", methods=["POST"])
def api_scan():
    payload = request.get_json(silent=True) or {}
    background = payload.get("background", False)
    force = payload.get("force", False)
    keep_channels = payload.get("keep_channels", False)
    region_ids = payload.get("regions", [])
    if tuner.is_scan_active() and not force:
        return jsonify({
            "success": False,
            "conflict": True,
            "message": "Another scan is active and will be canceled if you start a new scan."
        }), 409
    if tuner.is_scan_active() and force:
        tuner.cancel_scan(keep_channels=keep_channels)
    tuner.keep_partial_scan = False
    tuner.scan_region_ids = region_ids
    tuner.scan_frequency_list = tuner._get_region_frequencies(region_ids)
    tuner.scan_frequency_set = set(tuner.scan_frequency_list)
    if background:
        thread = threading.Thread(target=tuner.run_scan, daemon=True)
        thread.start()
        return jsonify({"success": True, "background": True})
    result = tuner.run_scan()
    tuner.load_channels()
    return jsonify(result)

@app.route("/api/scan/cancel", methods=["POST"])
def api_scan_cancel():
    payload = request.get_json(silent=True) or {}
    keep_channels = payload.get("keep_channels", False)
    if tuner.is_scan_active():
        tuner.cancel_scan(keep_channels=keep_channels)
        return jsonify({"success": True, "canceled": True, "kept": keep_channels})
    return jsonify({"success": False, "error": "no active scan"}), 400

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
