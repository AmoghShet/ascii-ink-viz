"""
Webcam probe with hard timeouts. Opens each (backend, mode) in a fresh VideoCapture,
reads on a worker thread so a hung read() cannot block the probe, and reports
open time, negotiated format, achieved fps, mean read() time and mean frame brightness
(low brightness = dim room = webcams drop their frame rate for longer exposure).

Usage: python cam_probe.py [--budget-s 4] [--skip-msmf] [--save frame.jpg]
"""
import argparse
import json
import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def fourcc_str(v):
    v = int(v)
    s = "".join(chr((v >> (8 * k)) & 0xFF) for k in range(4))
    return s if s.isprintable() else repr(s)


class Reader(threading.Thread):
    """Continuously reads frames; keeps only the latest (the pattern the real app will use)."""

    def __init__(self, cap):
        super().__init__(daemon=True)
        self.cap = cap
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.count = 0
        self.fails = 0
        self.read_times = []

    def run(self):
        while not self.stop.is_set():
            t0 = time.perf_counter()
            ok, frame = self.cap.read()
            dt = (time.perf_counter() - t0) * 1000.0
            self.read_times.append(dt)
            if ok and frame is not None:
                with self.lock:
                    self.latest = frame
                    self.count += 1
            else:
                self.fails += 1
                if self.fails > 5:
                    break


def probe(api_name, api, w, h, fourcc, order, budget_s):
    r = {"backend": api_name, "requested": [w, h], "fourcc_req": fourcc, "set_order": order}
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(0, api)
    r["open_ms"] = round((time.perf_counter() - t0) * 1000.0, 0)
    r["opened"] = bool(cap.isOpened())
    if not r["opened"]:
        cap.release()
        return r, None
    def set_size():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    def set_fcc():
        if fourcc:
            r["fourcc_set_ok"] = bool(cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc)))
    if order == "fourcc_first":
        set_fcc(); set_size()
    else:
        set_size(); set_fcc()
    cap.set(cv2.CAP_PROP_FPS, 30)
    r["negotiated"] = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
    r["fourcc_got"] = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))
    r["reported_fps"] = cap.get(cv2.CAP_PROP_FPS)

    rd = Reader(cap)
    t_start = time.perf_counter()
    rd.start()
    rd.join(budget_s)  # wait at most budget_s for the reader to run
    wall = time.perf_counter() - t_start
    rd.stop.set()
    frame = None
    with rd.lock:
        if rd.latest is not None:
            frame = rd.latest.copy()
    r["frames_in_budget"] = rd.count
    r["read_fails"] = rd.fails
    r["achieved_fps"] = round(rd.count / wall, 1)
    if rd.read_times:
        a = np.asarray(rd.read_times)
        r["read_ms_mean"] = round(float(a.mean()), 1)
        r["read_ms_max"] = round(float(a.max()), 1)
    if frame is not None:
        r["frame_shape"] = list(frame.shape)
        r["mean_brightness_0_255"] = round(float(frame.mean()), 1)
    # release on a thread too, in case release() hangs on a wedged backend
    rel = threading.Thread(target=cap.release, daemon=True)
    rel.start()
    rel.join(5.0)
    r["release_hung"] = rel.is_alive()
    return r, frame


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-s", type=float, default=4.0)
    ap.add_argument("--skip-msmf", action="store_true")
    ap.add_argument("--allow-720p-yuy2", action="store_true", help="also try 1280x720 without MJPG (wedges the C270)")
    ap.add_argument("--save", default=os.path.join(HERE, "last_camera_frame.jpg"))
    ap.add_argument("--json", default=os.path.join(HERE, "cam_probe_results.json"))
    args = ap.parse_args()

    print(f"OpenCV {cv2.__version__}  budget per mode {args.budget_s}s")
    is_win = sys.platform.startswith("win")
    is_mac = sys.platform == "darwin"
    if is_win:
        backends = [("DSHOW", cv2.CAP_DSHOW)] + ([] if args.skip_msmf else [("MSMF", cv2.CAP_MSMF)])
    elif is_mac:
        backends = [("AVFOUNDATION", cv2.CAP_AVFOUNDATION)]
    else:
        backends = [("V4L2", cv2.CAP_V4L2)]

    # Order matters: on the Logitech C270 a 1280x720 YUY2 request wedges the camera for every later
    # process until it is unplugged. So: safe modes first, 720p only with an MJPG request, and stop
    # the whole probe at the first mode that delivers no frames.
    modes = [
        (640, 480, None, "size_first"),
        (640, 480, "MJPG", "fourcc_first"),
        (1280, 720, "MJPG", "fourcc_first"),
        (1280, 720, "MJPG", "size_first"),
    ]
    if args.allow_720p_yuy2:
        modes.append((1280, 720, None, "size_first"))
    results = []
    best = None
    dead = False
    for bname, api in backends:
        if dead:
            break
        print(f"\n== backend {bname} ==")
        for (w, h, fcc, order) in modes:
            r, frame = probe(bname, api, w, h, fcc, order, args.budget_s)
            results.append(r)
            if not r["opened"]:
                print(f"  {w}x{h} {fcc or 'default'} ({order}): open FAILED after {r['open_ms']:.0f} ms")
                continue
            print(f"  {w}x{h} {fcc or 'default':>7} ({order:>12}): open={r['open_ms']:.0f}ms  got {r['negotiated'][0]}x{r['negotiated'][1]} {r['fourcc_got']}  fps={r['achieved_fps']} (frames={r['frames_in_budget']}, fails={r['read_fails']})  read mean={r.get('read_ms_mean')}ms max={r.get('read_ms_max')}ms  brightness={r.get('mean_brightness_0_255')}  release_hung={r['release_hung']}")
            sys.stdout.flush()
            if frame is not None and r["achieved_fps"] >= 10 and (best is None or frame.shape[0] > best.shape[0]):
                best = frame
            if r["release_hung"]:
                print("  !! release() hung; skipping the rest of this backend")
                break
            if r["achieved_fps"] < 2 or (r.get("mean_brightness_0_255", 0) == 0.0):
                print("  !! this mode delivered no real frames. Stopping here so the camera is not wedged further.")
                print("     If later modes also fail with black frames, unplug and replug the camera.")
                dead = True
                break
    with open(args.json, "w") as f:
        json.dump(results, f, indent=2)
    if best is not None:
        cv2.imwrite(args.save, best)
        print(f"\nsaved best frame {best.shape} -> {args.save}")
    else:
        print("\nno usable frame captured")
    print(f"results -> {args.json}")


if __name__ == "__main__":
    main()
