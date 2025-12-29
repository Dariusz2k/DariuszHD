import os
import re
import shlex
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("OCPANEL_SECRET", "change-me")

CONFIG_PATH = "/boot/config.txt"
MARK_BEGIN = "# === OCPANEL BEGIN ==="
MARK_END   = "# === OCPANEL END ==="

PROFILES = {
    "default": {
        "label": "Default (remove OC block)",
        "lines": []
    },
    "safe": {
        "label": "Safe (1800 / 600, ov=4)",
        "lines": [
            "arm_freq=1800",
            "gpu_freq=600",
            "over_voltage=4",
            "force_turbo=0",
        ]
    },
    "high": {
        "label": "High (2000 / 600, ov=5)",
        "lines": [
            "arm_freq=2000",
            "gpu_freq=600",
            "over_voltage=5",
            "force_turbo=0",
        ]
    },
    "max": {
        "label": "Max (2147 / 750, ov=6, force_turbo=1)",
        "lines": [
            "arm_freq=2147",
            "gpu_freq=750",
            "over_voltage=6",
            "force_turbo=1",
        ]
    },
}

def run(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.stdout.strip()

def vcgencmd(arg: str) -> str:
    return run(f"vcgencmd {shlex.quote(arg)}")

def parse_measure_clock(out: str) -> int:
    # frequency(48)=1500000000
    m = re.search(r"frequency\(\d+\)=(\d+)", out)
    return int(m.group(1)) if m else 0

def parse_temperature(temp_str: str) -> float:
    """Parse temperature from vcgencmd output like 'temp=39.9'C'"""
    m = re.search(r"temp=([0-9.]+)", temp_str)
    return float(m.group(1)) if m else 0.0

def get_argon_fan_speed() -> str:
    """
    Read Argon ONE fan speed using the official Argon status script.
    Falls back to N/A if the script is not available.
    """
    try:
        # Use the official Argon status script
        argonstatusscript = "/etc/argon/argonstatus.py"
        if os.path.exists(argonstatusscript):
            result = run(f'sudo /usr/bin/python3 {argonstatusscript} "fan speed"')
            if result:
                # The script returns something like "Fan Speed: 50%"
                # Extract just the percentage
                lines = result.strip().split('\n')
                for line in lines:
                    if 'Fan Speed' in line or '%' in line:
                        # Extract percentage value
                        import re
                        match = re.search(r'(\d+)\s*%', line)
                        if match:
                            return f"{match.group(1)}%"
                        # If no percentage found, return the whole line
                        return line.split(':')[-1].strip()
    except:
        pass

    return "N/A"

def get_status():
    temp_str = vcgencmd("measure_temp")          # temp=38.4'C
    temp_c = parse_temperature(temp_str)
    volts = vcgencmd("measure_volts")            # volt=0.8625V
    arm_hz = parse_measure_clock(vcgencmd("measure_clock arm"))
    core_hz = parse_measure_clock(vcgencmd("measure_clock core"))
    throttled = vcgencmd("get_throttled")        # throttled=0x0
    fan_speed = get_argon_fan_speed()

    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "temp": temp_str,
        "temp_c": temp_c,
        "volts": volts,
        "arm_mhz": arm_hz / 1_000_000 if arm_hz else 0,
        "core_mhz": core_hz / 1_000_000 if core_hz else 0,
        "throttled": throttled,
        "profile": detect_profile(),
        "fan_speed": fan_speed,
    }

def read_config() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return f.read()

def write_config(text: str):
    # backup
    backup = CONFIG_PATH + ".bak.ocpanel"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as b:
            b.write(read_config())

    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CONFIG_PATH)

def strip_block(cfg: str) -> str:
    pattern = re.compile(rf"\n?{re.escape(MARK_BEGIN)}.*?{re.escape(MARK_END)}\n?", re.S)
    return re.sub(pattern, "\n", cfg).strip() + "\n"

def set_profile(name: str):
    if name not in PROFILES:
        raise ValueError("Unknown profile")

    cfg = read_config()
    cfg = strip_block(cfg)

    if name == "default":
        write_config(cfg)
        return

    block_lines = [MARK_BEGIN] + PROFILES[name]["lines"] + [MARK_END]
    new_cfg = cfg.rstrip("\n") + "\n\n" + "\n".join(block_lines) + "\n"
    write_config(new_cfg)

def detect_profile() -> str:
    cfg = read_config()
    if MARK_BEGIN not in cfg or MARK_END not in cfg:
        return "default"

    m = re.search(rf"{re.escape(MARK_BEGIN)}(.*?){re.escape(MARK_END)}", cfg, re.S)
    if not m:
        return "default"

    block = [ln.strip() for ln in m.group(1).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for k, v in PROFILES.items():
        if k == "default":
            continue
        if block == v["lines"]:
            return k
    return "custom"

@app.get("/")
def index():
    st = get_status()
    return render_template("index.html", status=st, profiles=PROFILES)

@app.get("/api/status")
def api_status():
    """JSON endpoint for AJAX updates"""
    return jsonify(get_status())

@app.post("/apply")
def apply():
    name = request.form.get("profile", "").strip()
    reboot = request.form.get("reboot", "0") == "1"
    try:
        set_profile(name)
        flash(f"Applied profile: {name}.", "ok")
        if reboot:
            flash("Rebooting now...", "ok")
            subprocess.Popen(["/sbin/reboot"])
        return redirect(url_for("index"))
    except Exception as e:
        flash(f"Error: {e}", "err")
        return redirect(url_for("index"))

@app.post("/reboot")
def do_reboot():
    flash("Rebooting now...", "ok")
    subprocess.Popen(["/sbin/reboot"])
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Bind on all interfaces so you can open it from another device on your LAN.
    app.run(host="0.0.0.0", port=8088, debug=False)
