"""
Does the C270's auto-exposure cap the frame rate? Opens 640x480 via DirectShow (the safe mode),
measures fps under auto exposure, then under manual exposures. DirectShow exposure values are
log2 seconds: -4 = 1/16 s (62 ms, caps at ~16 fps), -5 = 1/32 s, -6 = 1/64 s.
Only uses 640x480 so it cannot wedge the camera.
"""
import sys
import time

import cv2
import numpy as np


def measure(cap, secs, label):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 2.0:  # let the new exposure settle
        cap.read()
    n = 0
    bright = []
    maxdt = 0.0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < secs:
        t1 = time.perf_counter()
        ok, f = cap.read()
        dt = time.perf_counter() - t1
        maxdt = max(maxdt, dt)
        if ok and f is not None:
            n += 1
            bright.append(float(f.mean()))
    wall = time.perf_counter() - t0
    print(f"  {label:>34}: fps={n / wall:5.1f} max_read={maxdt * 1000:4.0f} ms brightness={np.mean(bright) if bright else 0:5.1f} "
          f"exposure={cap.get(cv2.CAP_PROP_EXPOSURE)} autoexp={cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)} gain={cap.get(cv2.CAP_PROP_GAIN)}", flush=True)
    return n / wall


def main():
    api = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
    cap = cv2.VideoCapture(0, api)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    print(f"OpenCV {cv2.__version__}, 640x480 via backend {cap.getBackendName() if cap.isOpened() else 'FAILED'}")
    if not cap.isOpened():
        return
    fps_auto = measure(cap, 5, "auto exposure (camera default)")
    if fps_auto < 2:
        print("  !! no frames even at 640x480: camera is wedged, unplug and replug it")
        cap.release()
        return
    for exp in (-5, -6, -7):
        ok1 = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # DirectShow: 0.25 = manual, 0.75 = auto
        ok2 = cap.set(cv2.CAP_PROP_EXPOSURE, exp)
        measure(cap, 5, f"manual exposure {exp} (set ok {ok1},{ok2})")
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)  # hand the camera back to auto
    measure(cap, 3, "auto exposure restored")
    cap.release()


if __name__ == "__main__":
    main()
