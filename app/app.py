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
        self.cat_proc = None
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
        # We parse channel info from stderr output, and save stdout to channels.zap for tuning
        # Default output format should work with czap tuner
        cmd = f"w_scan -A 1 -ft -c US"
        logger.info(f"[SCAN] Running w_scan command: {cmd}")
        logger.info("[SCAN] *** This will take 5-10 minutes, monitoring progress... ***")

        # Save stdout to channels.zap for later tuning
        zap_path = os.path.join(CONFIG_DIR, "channels.zap")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        zap_file = open(zap_path, 'w')

        # Start w_scan and monitor its stderr output in real-time
        self.scan_proc = subprocess.Popen(
            cmd.split(),
            stdout=zap_file,  # Save to channels.zap for azap
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        channels_found = 0
        current_freq = ""
        current_freq_mhz = 0
        recent_channels = []  # Track recently found channels
        new_channels = {}  # Build channel list from stderr parsing

        # ATSC frequency range for progress calculation (54 MHz to 858 MHz)
        FREQ_MIN = 54
        FREQ_MAX = 858

        # Monitor stderr in real-time (w_scan outputs to stderr)
        while True:
            if self.scan_cancelled:
                logger.warning("[SCAN] Scan cancelled by user!")
                self.scan_proc.terminate()
                try:
                    self.scan_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.scan_proc.kill()
                zap_file.close()
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
                    current_freq_mhz = freq_mhz

                    # Calculate progress based on frequency range (54-858 MHz)
                    # Reserve 10-90% for scanning, 90-100% for parsing
                    if freq_mhz >= FREQ_MIN and freq_mhz <= FREQ_MAX:
                        scan_progress = ((freq_mhz - FREQ_MIN) / (FREQ_MAX - FREQ_MIN)) * 80 + 10
                        progress = int(min(90, max(10, scan_progress)))
                    else:
                        progress = 10

                    socketio.emit('scan_progress', {
                        'progress': progress,
                        'channels_found': channels_found,
                        'status': f'Scanning {current_freq}',
                        'recent_channels': recent_channels[-5:]  # Last 5 channels
                    })

            # Parse w_scan output for found services/channels
            # Format: "service is running. Channel number: 2:1. Name: 'WJBK   '"
            if "service is running" in line.lower():
                channels_found += 1

                # Calculate current progress based on last known frequency
                if current_freq_mhz >= FREQ_MIN and current_freq_mhz <= FREQ_MAX:
                    scan_progress = ((current_freq_mhz - FREQ_MIN) / (FREQ_MAX - FREQ_MIN)) * 80 + 10
                    progress = int(min(90, max(10, scan_progress)))
                else:
                    progress = 10

                # Extract channel number and name
                channel_match = re.search(r'Channel number:\s*(\d+):(\d+)\.\s*Name:\s*["\']([^"\']+)["\']', line)
                if channel_match:
                    major = channel_match.group(1)
                    minor = channel_match.group(2)
                    name = channel_match.group(3).strip()
                    channel_id = f"{major}.{minor}"
                    channel_display = f"{channel_id} {name}"

                    # Add to channels dict for later saving
                    new_channels[channel_id] = {"name": name}

                    recent_channels.append(channel_display)
                    logger.info(f"[SCAN] Found service #{channels_found}: {channel_display}")

                    socketio.emit('scan_progress', {
                        'progress': progress,
                        'channels_found': channels_found,
                        'status': f'Found: {channel_display}',
                        'recent_channels': recent_channels[-5:]  # Last 5 channels
                    })
                else:
                    # Fallback if parsing fails
                    logger.info(f"[SCAN] Found service #{channels_found}")
                    socketio.emit('scan_progress', {
                        'progress': progress,
                        'channels_found': channels_found,
                        'status': f'Found {channels_found} services at {current_freq}',
                        'recent_channels': recent_channels[-5:]
                    })

        # Wait for process to complete
        self.scan_proc.wait()
        returncode = self.scan_proc.returncode
        zap_file.close()  # Close the zap file now that w_scan has finished

        # Convert w_scan format to azap-compatible format
        logger.info("[SCAN] Converting channels.zap to azap-compatible format...")
        self._convert_to_azap_format(zap_path)

        logger.info(f"[SCAN] w_scan completed with return code: {returncode}")
        logger.info(f"[SCAN] Found {channels_found} services during scan")
        logger.info(f"[SCAN] Parsed {len(new_channels)} channels from scan output")
        logger.info(f"[SCAN] Saved tuning data to {zap_path}")

        if returncode != 0 and not self.scan_cancelled:
            self.scanning = False
            error_msg = "w_scan failed with non-zero exit code"
            socketio.emit('scan_complete', {'success': False, 'error': error_msg, 'channels_found': 0, 'channels': {}})
            return {"success": False, "error": error_msg}

        logger.info("[SCAN] Emitting progress: 95%")
        socketio.emit('scan_progress', {'progress': 95, 'channels_found': len(new_channels), 'status': 'Saving channels...'})

        # Save the channels we parsed from stderr
        self.channels = new_channels
        self.save_channels()
        self.scanning = False

        logger.info(f"[SCAN] Saved {len(self.channels)} channels to {CHANNELS_JSON}")
        socketio.emit('scan_progress', {'progress': 100, 'channels_found': len(self.channels), 'status': 'Complete!'})
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
        logger.info("="*70)
        logger.info(f"[TUNE] Attempting to tune to channel: {channel}")
        logger.info(f"[TUNE] Available channels: {list(self.channels.keys())}")

        if channel not in self.channels:
            logger.error(f"[TUNE] Channel {channel} not found in channels list!")
            return False

        logger.info(f"[TUNE] Channel {channel} found: {self.channels[channel]}")

        # Stop any existing zap (tune) process
        logger.info("[TUNE] Stopping any existing zap process...")
        self.stop_zap()

        # We need a channels.conf file for azap tuner (ATSC).
        # The file should be generated by w_scan in default format.
        # Each line contains: STATION_NAME:frequency:modulation
        zap_path = os.path.join(CONFIG_DIR, "channels.zap")
        logger.info(f"[TUNE] Looking for channels.zap at: {zap_path}")

        if not os.path.exists(zap_path):
            logger.warning("[TUNE] channels.zap not found! Generating with w_scan...")
            # Generate it with default output format
            os.makedirs(CONFIG_DIR, exist_ok=True)
            cmd = f"w_scan -A 1 -ft -c US > {zap_path}"
            logger.info(f"[TUNE] Running: {cmd}")
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if r.returncode != 0:
                logger.error(f"[TUNE] Failed to generate channels.zap: {r.stderr or r.stdout}")
                return False
            logger.info("[TUNE] channels.zap generated successfully")
        else:
            logger.info(f"[TUNE] channels.zap exists ({os.path.getsize(zap_path)} bytes)")

        # Start dvbv5-zap in "record" mode so it holds the tuner
        # NOTE: dvbv5-zap expects a channel NAME as listed in channels.zap.
        # The zap file has station names like "WJBK", not virtual channels like "2.1"
        # So we look up the station name from our channels dict
        station_name = self.channels[channel].get("name", "")
        logger.info(f"[TUNE] Channel {channel} has station name: {station_name}")
        logger.info(f"[TUNE] Searching for zap entry matching station: {station_name}")
        entry_name = self._find_zap_entry_by_name(zap_path, station_name)

        if not entry_name:
            logger.error(f"[TUNE] Could not map virtual channel {channel} to a zap entry name")
            logger.info("[TUNE] Showing first 10 lines of channels.zap:")
            try:
                with open(zap_path, 'r') as f:
                    for i, line in enumerate(f):
                        if i >= 10:
                            break
                        logger.info(f"[TUNE]   {line.strip()}")
            except Exception as e:
                logger.error(f"[TUNE] Could not read channels.zap: {e}")
            return False

        logger.info(f"[TUNE] Found zap entry: {entry_name}")

        # Extract adapter number from frontend path (e.g., /dev/dvb/adapter0/frontend0 -> 0)
        import re
        adapter_match = re.search(r'adapter(\d+)', self.frontend)
        adapter_num = adapter_match.group(1) if adapter_match else "0"

        cmd = [
            "azap",
            "-a", adapter_num,
            "-r",  # Record mode - keeps tuner locked
            "-c", zap_path,
            entry_name
        ]
        logger.info(f"[TUNE] Running azap command: {' '.join(cmd)}")
        self.zap_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        # Wait a moment and check if it's still running
        time.sleep(0.5)
        if self.zap_proc.poll() is not None:
            # Process already died
            stdout, stderr = self.zap_proc.communicate()
            logger.error(f"[TUNE] azap exited immediately with code {self.zap_proc.returncode}")
            logger.error(f"[TUNE] stdout: {stdout}")
            logger.error(f"[TUNE] stderr: {stderr}")

            # Debug: Show what's actually in the zap file
            logger.error("[TUNE] Debugging: First 5 lines of channels.zap:")
            try:
                with open(zap_path, 'r') as f:
                    for i, line in enumerate(f):
                        if i >= 5:
                            break
                        logger.error(f"[TUNE]   Line {i+1}: {repr(line.strip())}")
            except Exception as e:
                logger.error(f"[TUNE] Could not read channels.zap: {e}")

            self.zap_proc = None
            return False

        self.current_channel = channel
        logger.info(f"[TUNE] Successfully tuned to channel {channel}")
        logger.info("="*70)
        return True

    def _convert_to_azap_format(self, zap_path):
        """
        Convert w_scan's native output format to azap-compatible format.

        w_scan format:  WJBK   ;(null):177000:M10:A:0:49:52=eng,53=spa;52,53:0:0:3:0:0:0
        azap format:    WJBK:177000000:8VSB:49:52:3

        Fields:
        - Channel name (before semicolon, trimmed)
        - Frequency in Hz (w_scan outputs kHz, multiply by 1000)
        - Modulation (8VSB for ATSC)
        - Video PID (from w_scan field 6)
        - Audio PID (first audio PID from w_scan field 7)
        - Service ID (from w_scan field 10)
        """
        try:
            with open(zap_path, 'r') as f:
                lines = f.readlines()

            converted_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Parse w_scan format: NAME;source:freq:modulation:...:vpid:apid:...:sid:...
                parts = line.split(':')
                if len(parts) < 10:
                    logger.warning(f"[SCAN] Skipping malformed line (not enough fields): {line[:50]}")
                    continue

                # Extract channel name (before semicolon)
                name_part = parts[0].split(';')[0].strip()

                # Extract frequency (in kHz, convert to Hz)
                try:
                    freq_khz = int(parts[1])
                    freq_hz = freq_khz * 1000
                except (ValueError, IndexError):
                    logger.warning(f"[SCAN] Could not parse frequency from: {line[:50]}")
                    continue

                # Extract video PID (field 6)
                try:
                    video_pid = parts[5].strip()
                    # Handle cases like "49=2" - take just the number before =
                    if '=' in video_pid:
                        video_pid = video_pid.split('=')[0]
                    video_pid = int(video_pid)
                except (ValueError, IndexError):
                    logger.warning(f"[SCAN] Could not parse video PID from: {line[:50]}")
                    continue

                # Extract audio PID (field 7, take first one)
                try:
                    audio_field = parts[6].strip()
                    # Format: "52=eng,53=spa;52,53" or just "52"
                    # Take the first number before any = or ; or ,
                    audio_pid = audio_field.split('=')[0].split(';')[0].split(',')[0].strip()
                    audio_pid = int(audio_pid)
                except (ValueError, IndexError):
                    logger.warning(f"[SCAN] Could not parse audio PID from: {line[:50]}")
                    continue

                # Extract service ID (field 10)
                try:
                    service_id = int(parts[9].strip())
                except (ValueError, IndexError):
                    logger.warning(f"[SCAN] Could not parse service ID from: {line[:50]}")
                    continue

                # For ATSC, modulation is always 8VSB
                modulation = "8VSB"

                # Create azap format line: NAME:FREQ:MOD:VPID:APID:SID
                azap_line = f"{name_part}:{freq_hz}:{modulation}:{video_pid}:{audio_pid}:{service_id}"
                converted_lines.append(azap_line)

            # Write back to file
            with open(zap_path, 'w') as f:
                f.write('\n'.join(converted_lines) + '\n')

            logger.info(f"[SCAN] Converted {len(converted_lines)} channels to azap format")

        except Exception as e:
            logger.error(f"[SCAN] Failed to convert to azap format: {e}")

    def _find_zap_entry_by_name(self, zap_path, station_name):
        """
        channels.zap lines in azap format look like:
          WJBK:177000000:8VSB:49:52:3
        Extract the station name before the first colon and match against station_name
        """
        try:
            with open(zap_path, "r", errors="ignore") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

            logger.info(f"[TUNE] Searching through {len(lines)} zap entries")

            # Try exact match first (case-insensitive, stripped)
            for ln in lines:
                # dvbv5 format: NAME;source:freq:...
                # czap format: NAME:freq:...
                if ';' in ln:
                    name = ln.split(";", 1)[0].strip()
                else:
                    name = ln.split(":", 1)[0].strip()

                if name.lower() == station_name.lower():
                    logger.info(f"[TUNE] Exact match found: {name}")
                    return name

            # Try contains match (for cases like "WJBK-DT" vs "WJBK")
            for ln in lines:
                if ';' in ln:
                    name = ln.split(";", 1)[0].strip()
                else:
                    name = ln.split(":", 1)[0].strip()

                if station_name.lower() in name.lower() or name.lower() in station_name.lower():
                    logger.info(f"[TUNE] Partial match found: {name}")
                    return name

            logger.warning(f"[TUNE] No match found for station: {station_name}")
        except Exception as e:
            logger.error(f"[TUNE] Error searching zap file: {e}")
            return None
        return None

    def _find_zap_entry_name(self, zap_path, channel):
        """
        DEPRECATED: Old method that searched by channel number
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

        if self.cat_proc and self.cat_proc.poll() is None:
            self.cat_proc.terminate()
            try:
                self.cat_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.cat_proc.kill()
        self.cat_proc = None

    def start_stream(self, channel):
        """
        Stream a channel using ffmpeg with piped input from azap.
        This eliminates DVR device issues by piping azap output directly to ffmpeg.
        """
        # Stop any existing processes
        self.stop_stream()
        self.stop_zap()

        # Verify channel exists
        if channel not in self.channels:
            logger.error(f"[STREAM] Channel {channel} not found")
            return False

        # Get station name and zap file
        station_name = self.channels[channel].get("name", "")
        zap_path = os.path.join(CONFIG_DIR, "channels.zap")

        if not os.path.exists(zap_path):
            logger.error(f"[STREAM] channels.zap not found at {zap_path}")
            return False

        # Find zap entry
        entry_name = self._find_zap_entry_by_name(zap_path, station_name)
        if not entry_name:
            logger.error(f"[STREAM] Could not find zap entry for {station_name}")
            return False

        logger.info("="*70)
        logger.info(f"[STREAM] Starting stream for channel {channel} ({station_name})")
        logger.info(f"[STREAM] Using zap entry: {entry_name}")

        # Extract adapter number
        import re
        adapter_match = re.search(r'adapter(\d+)', self.frontend)
        adapter_num = adapter_match.group(1) if adapter_match else "0"

        # Create HLS output directory
        hls_dir = os.path.join(CONFIG_DIR, "hls")
        os.makedirs(hls_dir, exist_ok=True)
        hls_playlist = os.path.join(hls_dir, "stream.m3u8")

        # Clean up old HLS files
        import glob
        for old_file in glob.glob(os.path.join(hls_dir, "stream*.ts")) + glob.glob(os.path.join(hls_dir, "*.m3u8")):
            try:
                os.remove(old_file)
            except:
                pass

        # Start azap with -r to write to DVR device
        azap_cmd = [
            "azap",
            "-a", adapter_num,
            "-c", zap_path,
            "-r",  # Record mode - writes to DVR device
            entry_name
        ]

        logger.info(f"[STREAM] Starting azap: {' '.join(azap_cmd)}")
        self.zap_proc = subprocess.Popen(
            azap_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Start monitoring azap's stderr to check for lock
        def monitor_azap():
            if self.zap_proc and self.zap_proc.stderr:
                for line in self.zap_proc.stderr:
                    logger.info(f"[AZAP] {line.strip()}")

        azap_monitor = threading.Thread(target=monitor_azap, daemon=True)
        azap_monitor.start()

        # Wait longer for azap to tune, lock, and stabilize signal
        logger.info("[STREAM] Waiting for azap to lock and stabilize (7 seconds)...")
        time.sleep(7)

        if self.zap_proc.poll() is not None:
            logger.error(f"[STREAM] azap died with code {self.zap_proc.returncode}")
            self.zap_proc = None
            return False

        # Use cat to read from DVR device and pipe to ffmpeg
        # This creates: azap -> /dev/dvb/adapter0/dvr0 -> cat -> ffmpeg
        logger.info(f"[STREAM] Starting cat to read from {self.dvr}")
        self.cat_proc = subprocess.Popen(
            ["cat", self.dvr],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Start ffmpeg reading from cat's stdout
        # Use stream copy (no re-encoding) for instant remuxing to HLS
        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "info",
            "-f", "mpegts",  # Explicitly specify MPEG-TS format
            "-fflags", "+discardcorrupt+genpts",  # Discard corrupt packets, generate PTS
            "-analyzeduration", "2000000",  # 2 seconds to probe stream (reduced from 5s)
            "-probesize", "5000000",  # 5MB probe size (reduced from 10MB)
            "-i", "pipe:0",  # Read from stdin (connected to cat's stdout)
            "-c", "copy",  # Stream copy - no re-encoding!
            "-avoid_negative_ts", "make_zero",  # Avoid negative timestamps
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "10",  # Keep more segments to avoid gaps
            "-hls_flags", "delete_segments+append_list+omit_endlist",  # Delete old segments
            "-hls_segment_filename", os.path.join(hls_dir, "stream%d.ts"),
            hls_playlist
        ]

        logger.info(f"[STREAM] Starting ffmpeg: {' '.join(ffmpeg_cmd)}")
        logger.info(f"[STREAM] Pipeline: azap -> {self.dvr} -> cat -> ffmpeg")

        self.ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=self.cat_proc.stdout,  # Connect to cat's stdout
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Allow cat to write to pipe (close our reference)
        if self.cat_proc.stdout:
            self.cat_proc.stdout.close()

        # Start a thread to monitor ffmpeg stderr
        def monitor_ffmpeg():
            if self.ffmpeg_proc and self.ffmpeg_proc.stderr:
                for line in self.ffmpeg_proc.stderr:
                    logger.info(f"[FFMPEG] {line.strip()}")

        ffmpeg_monitor = threading.Thread(target=monitor_ffmpeg, daemon=True)
        ffmpeg_monitor.start()

        # Wait for ffmpeg to start processing (reduced since no analyzeduration delays)
        time.sleep(1)

        if self.ffmpeg_proc.poll() is not None:
            logger.error(f"[STREAM] ffmpeg died with code {self.ffmpeg_proc.returncode}")
            self.ffmpeg_proc = None
            self.stop_zap()
            return False

        logger.info("[STREAM] ffmpeg started successfully")
        logger.info(f"[STREAM] Building DVR buffer (optimized startup)...")
        logger.info(f"[STREAM] This allows clean playback and rewind capability")

        # Wait for initial playlist creation
        playlist_ready = False
        for i in range(10):
            if os.path.exists(hls_playlist):
                try:
                    with open(hls_playlist, 'r') as f:
                        if f.read().startswith('#EXTM3U'):
                            playlist_ready = True
                            logger.info(f"[STREAM] HLS playlist created after {i+1} seconds")
                            break
                except:
                    pass
            time.sleep(1)

        if not playlist_ready:
            logger.error("[STREAM] HLS playlist not created after 10 seconds")
            return False

        # Wait briefly for first segment (no re-encoding = nearly instant)
        # With stream copy, segments are created in real-time without processing delay
        logger.info("[STREAM] Waiting for first segment...")
        buffer_time = 3
        for i in range(buffer_time):
            time.sleep(1)
            # Log progress at end of buffer
            if (i + 1) == buffer_time:
                try:
                    with open(hls_playlist, 'r') as f:
                        content = f.read()
                        segment_count = content.count('.ts')
                        logger.info(f"[STREAM] Buffer progress: {i+1}/{buffer_time}s - {segment_count} segments")
                except:
                    pass

        # Verify we have segments
        try:
            with open(hls_playlist, 'r') as f:
                content = f.read()
                segment_count = content.count('.ts')
                if segment_count < 5:
                    logger.warning(f"[STREAM] Only {segment_count} segments after buffering")
                else:
                    logger.info(f"[STREAM] DVR buffer ready with {segment_count} segments")
        except Exception as e:
            logger.error(f"[STREAM] Error reading final playlist: {e}")

        # Get first segment info for logging
        try:
            segments = [f for f in os.listdir(hls_dir) if f.startswith('stream') and f.endswith('.ts')]
            if segments:
                first_segment = sorted(segments)[0]
                segment_size = os.path.getsize(os.path.join(hls_dir, first_segment))
                logger.info(f"[STREAM] First segment: {first_segment} ({segment_size} bytes)")
        except:
            pass

        self.current_channel = channel
        logger.info("[STREAM] Stream ready! Playback will start from beginning of buffer")
        logger.info("="*70)
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

@app.route("/hls/<path:filename>")
def serve_hls(filename):
    """Serve HLS playlist and segments"""
    from flask import send_from_directory
    hls_dir = os.path.join(CONFIG_DIR, "hls")
    return send_from_directory(hls_dir, filename)

@app.route("/stream.ts")
def stream_ts():
    """
    Legacy MPEG-TS stream endpoint (deprecated - use HLS instead)
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

