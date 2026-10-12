"""Unattended checks of live-window features (each run is muted; nothing plays out loud).

    python tests/live_features.py [which ...]     which: reload presets export resize tier portrait camera

Each check launches `python -m audioviz play ... --mute --quit-after N --auto-keys ...` as a
subprocess, does its outside action (e.g. edits a shader) while it runs, and inspects stdout and
out/live_stats.json. Results -> out/verify/report_live_features.json.
"""
import glob
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SONG = "D:/Downloads/Sit Down. Stand Up.mp3"
OUT = os.path.join(ROOT, "out", "verify")


def play(extra, seconds, during=None):
    cmd = [PY, "-u", "-m", "audioviz", "play", SONG, "--mute", "--quit-after", str(seconds)] + extra
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(iter(p.stdout.readline, "")), daemon=True)
    reader.start()
    if during:
        during()
    p.wait(timeout=seconds + 120)
    reader.join(timeout=5)
    try:
        with open(os.path.join(ROOT, "out", "live_stats.json"), encoding="utf-8") as fh:
            stats = json.load(fh)
    except Exception:
        stats = {}
    return p.returncode, [ln.rstrip() for ln in lines], stats


def check_reload():
    shader = os.path.join(ROOT, "audioviz", "shaders", "ascii.frag")
    with open(shader, encoding="utf-8") as fh:
        original = fh.read()

    def during():
        time.sleep(14)   # window open, prewarmed, playing
        with open(shader, "w", encoding="utf-8") as fh:
            fh.write(original.replace("f = vec4(col, 1.0);", "f = vec4(col, 1.0) BROKEN;"))
        time.sleep(3)
        with open(shader, "w", encoding="utf-8") as fh:
            fh.write(original.replace("f = vec4(col, 1.0);", "f = vec4(col.bgr, 1.0);"))   # visible change
        time.sleep(3)
        with open(shader, "w", encoding="utf-8") as fh:
            fh.write(original)

    try:
        rc, out, stats = play(["--seed", "3", "--auto-keys", "16:S,19:S"], 26, during)
    finally:
        with open(shader, "w", encoding="utf-8") as fh:
            fh.write(original)
    failed = any("failed, keeping the old program" in ln for ln in out)
    recompiled = sum("shaders recompiled" in ln for ln in out)
    cleared = any("errors cleared" in ln for ln in out)
    return {"rc": rc, "error_reported_and_kept_running": failed and rc == 0, "recompiles": recompiled,
            "errors_cleared": cleared, "log": [ln for ln in out if "hot-reload" in ln or "auto-key" in ln],
            "pass": rc == 0 and failed and recompiled >= 1 and cleared}


def check_presets():
    before = set(glob.glob(os.path.join(ROOT, "audioviz", "presets", "user_*.json")))
    rc, out, stats = play(["--seed", "4", "--auto-keys", "8:ctrl+S,10:2,12:1,13:R,15:],16:["], 20)
    after = set(glob.glob(os.path.join(ROOT, "audioviz", "presets", "user_*.json")))
    new = sorted(after - before)
    keys = [ln for ln in out if "auto-key" in ln]
    for f in new:   # test artefact
        os.remove(f)
    ok = rc == 0 and len(new) == 1 and any("preset user_" in ln for ln in keys) and any("preset ink_cold" in ln for ln in keys)
    return {"rc": rc, "saved": [os.path.basename(f) for f in new], "keys": keys, "pass": ok}


def check_export():
    before = set(glob.glob(os.path.join(ROOT, "out", "exports", "*.mp4")))
    rc, out, stats = play(["--seed", "8", "--auto-keys", "6:E,8:E"], 12)
    keys = [ln for ln in out if "auto-key" in ln]
    t0 = time.time()
    new = []
    while time.time() - t0 < 600:   # the background export renders the whole song
        new = sorted(set(glob.glob(os.path.join(ROOT, "out", "exports", "*.mp4"))) - before)
        if new and os.path.isfile(os.path.splitext(new[0])[0] + "_resolved_params.json"):
            break
        time.sleep(5)
    size = os.path.getsize(new[0]) if new else 0
    return {"rc": rc, "keys": keys, "export": new, "bytes": size, "waited_s": round(time.time() - t0),
            "pass": rc == 0 and bool(new) and size > 1_000_000 and any("already running" in k for k in keys)}


def check_resize():
    rc, out, stats = play(["--seed", "5", "--auto-keys", "6:F,10:S,12:F,15:S"], 18)
    shots = sorted(glob.glob(os.path.join(ROOT, "out", "screens", "*seed5_ascii.png")))[-2:]
    import cv2

    sizes = [cv2.imread(s).shape[:2][::-1] for s in shots]
    return {"rc": rc, "screens": [os.path.basename(s) for s in shots], "sizes": sizes,
            "pass": rc == 0 and len(sizes) == 2 and sizes[0] != sizes[1]}


def check_tier():
    rc, out, stats = play(["--seed", "6", "--tier-drop-ms", "2.0"], 30)
    drops = [ln for ln in out if "auto tier drop" in ln]
    return {"rc": rc, "drops": drops, "tier_end": stats.get("tier_end"), "pass": rc == 0 and len(drops) >= 1}


def check_portrait():
    rc, out, stats = play(["--seed", "2", "--aspect", "9:16", "--auto-keys", "8:S"], 12)
    return {"rc": rc, "fb_size": stats.get("fb_size"), "gpu_ms_p95": stats.get("gpu_ms_p95"),
            "pass": rc == 0 and stats.get("fb_size", [1, 0])[1] > stats.get("fb_size", [1, 0])[0]}


def check_camera():
    rc, out, stats = play(["--seed", "2", "--camera", "--auto-keys", "12:S"], 16)
    rc2, out2, stats2 = play(["--seed", "2", "--camera", "synthetic", "--auto-keys", "10:S"], 14)
    return {"device_rc": rc, "synthetic_rc": rc2, "device_frame_ms_p95": stats.get("frame_ms_p95"),
            "synthetic_frame_ms_p95": stats2.get("frame_ms_p95"), "device_drops": stats.get("dropped_steps"),
            "pass": rc == 0 and rc2 == 0}


CHECKS = {"reload": check_reload, "presets": check_presets, "resize": check_resize, "tier": check_tier,
          "portrait": check_portrait, "camera": check_camera, "export": check_export}

if __name__ == "__main__":
    which = sys.argv[1:] or list(CHECKS)
    path = os.path.join(OUT, "report_live_features.json")
    try:
        with open(path, encoding="utf-8") as fh:
            rep = json.load(fh)
    except Exception:
        rep = {}
    for w in which:
        t0 = time.time()
        r = CHECKS[w]()
        r["elapsed_s"] = round(time.time() - t0)
        r["when"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rep[w] = r
        print(w, "PASS" if r.get("pass") else "FAIL", json.dumps({k: v for k, v in r.items() if k not in ("log",)})[:600])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2)
