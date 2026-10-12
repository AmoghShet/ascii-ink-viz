"""
Webcam capture through the ffmpeg binary bundled with imageio-ffmpeg, piped as raw BGR frames.
This bypasses OpenCV's camera backends entirely and lets us request MJPEG explicitly.
Works on Windows (dshow) and macOS (avfoundation).

Usage:
  python cam_ffmpeg.py --list
  python cam_ffmpeg.py --device "Logi C270 HD WebCam" --size 1280x720 --fps 30 --codec mjpeg --seconds 8
  python cam_ffmpeg.py --device 0 --size 1280x720 --fps 30 --seconds 8     (macOS: device index)
"""
import argparse
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def list_devices():
    exe = ffmpeg_exe()
    if sys.platform.startswith("win"):
        cmd = [exe, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    elif sys.platform == "darwin":
        cmd = [exe, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    else:
        cmd = [exe, "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", "/dev/video0"]
    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    print(out.stderr)


def build_cmd(exe, device, w, h, fps, codec):
    common_in = ["-hide_banner", "-loglevel", "warning", "-fflags", "nobuffer", "-flags", "low_delay"]
    if sys.platform.startswith("win"):
        inp = ["-f", "dshow", "-rtbufsize", "64M"]
        if codec == "mjpeg":
            inp += ["-vcodec", "mjpeg"]
        else:
            inp += ["-pixel_format", "yuyv422"]
        inp += ["-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", f"video={device}"]
    elif sys.platform == "darwin":
        inp = ["-f", "avfoundation", "-framerate", str(fps), "-video_size", f"{w}x{h}", "-i", f"{device}:none"]
    else:
        inp = ["-f", "v4l2"]
        if codec == "mjpeg":
            inp += ["-input_format", "mjpeg"]
        inp += ["-video_size", f"{w}x{h}", "-framerate", str(fps), "-i", device]
    out = ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return [exe] + common_in + inp + out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--device", default="Logi C270 HD WebCam" if sys.platform.startswith("win") else "0")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--codec", default="mjpeg", choices=["mjpeg", "raw"])
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--save", default=os.path.join(HERE, "last_ffmpeg_frame.jpg"))
    args = ap.parse_args()
    if args.list:
        list_devices()
        return

    w, h = (int(v) for v in args.size.lower().split("x"))
    exe = ffmpeg_exe()
    cmd = build_cmd(exe, args.device, w, h, args.fps, args.codec)
    print("cmd:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    frame_bytes = w * h * 3
    t_open = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    def read_frame():
        buf = bytearray()
        while len(buf) < frame_bytes:
            chunk = proc.stdout.read(frame_bytes - len(buf))
            if not chunk:
                return None
            buf += chunk
        return np.frombuffer(bytes(buf), np.uint8).reshape(h, w, 3)

    first = read_frame()
    t_first = time.perf_counter()
    if first is None:
        err = proc.stderr.read().decode(errors="replace")
        print("no frames. ffmpeg said:\n", err)
        proc.kill()
        return
    print(f"first frame after {(t_first - t_open)*1000:.0f} ms")

    n = 0
    gaps = []
    bright = []
    last = time.perf_counter()
    t0 = last
    frame = first
    while time.perf_counter() - t0 < args.seconds:
        f = read_frame()
        if f is None:
            break
        now = time.perf_counter()
        gaps.append((now - last) * 1000.0)
        last = now
        n += 1
        if n % 10 == 0:
            bright.append(float(f.mean()))
        frame = f
    wall = time.perf_counter() - t0
    proc.kill()
    try:
        err = proc.stderr.read().decode(errors="replace").strip()
    except Exception:
        err = ""
    g = np.asarray(gaps) if gaps else np.zeros(1)
    print(f"{args.size} {args.codec} requested {args.fps} fps: achieved {n / wall:.1f} fps over {wall:.1f} s; "
          f"frame gap mean={g.mean():.1f} ms p95={np.percentile(g, 95):.1f} ms max={g.max():.1f} ms; "
          f"brightness={np.mean(bright) if bright else float(frame.mean()):.1f}")
    if err:
        print("ffmpeg stderr:", err[:800])
    try:
        import cv2
        cv2.imwrite(args.save, frame)
        print("saved", args.save)
    except Exception as e:  # noqa
        print("could not save frame:", e)


if __name__ == "__main__":
    main()
