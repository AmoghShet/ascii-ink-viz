"""M5: determinism and export checks.

    python tests/verify_export.py "D:/Downloads/Myxomatosis.mp3"

1. `export --seed 7` twice -> frame 300 md5 identical (and the PNGs byte-identical)
2. re-run from the first run's resolved_params.json -> same md5
3. 1080p60 export speed vs real time (frames per wall second > 60)
4. audio and video stream durations in the mp4 match (decoded with the bundled ffmpeg; there is
   no ffprobe in imageio-ffmpeg, so `ffmpeg -map 0:v|0:a -f null -` reports the decoded length)
5. 9:16 export (1080x1920 @ 30 fps) works
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audioviz.export.recorder import export, export_from_params, ffmpeg_exe  # noqa: E402

OUT = os.path.join(ROOT, "out", "verify", "export")


def stream_duration(path, sel):
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path, "-map", sel, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    times = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not times:
        return None
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def frame_count(path):
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path, "-map", "0:v", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    fr = re.findall(r"frame=\s*(\d+)", r.stderr)
    return int(fr[-1]) if fr else None


def main(song):
    os.makedirs(OUT, exist_ok=True)
    rep = {"song": song, "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    start, dur = 40.0, 6.0
    runs = []
    for k in (1, 2):
        r = export(song, 7, "ink_cold", (1920, 1080), 60, out=os.path.join(OUT, f"det_run{k}.mp4"),
                   start=start, duration=dur, dump_frames=[300], verbose=False)
        runs.append(r)
    rep["md5_run1"] = runs[0]["dumps"].get(300)
    rep["md5_run2"] = runs[1]["dumps"].get(300)
    rep["pass_deterministic"] = rep["md5_run1"] is not None and rep["md5_run1"] == rep["md5_run2"]

    r3 = export_from_params(runs[0]["params"], out=os.path.join(OUT, "det_from_params.mp4"), dump_frames=[300],
                            verbose=False)
    rep["md5_from_params"] = r3["dumps"].get(300)
    rep["pass_params_reproduce"] = rep["md5_from_params"] == rep["md5_run1"]

    # speed: 20 s of 1080p60
    t0 = time.perf_counter()
    r4 = export(song, 3, "ink_cold", (1920, 1080), 60, out=os.path.join(OUT, "speed_1080p60.mp4"),
                start=60.0, duration=20.0, verbose=False)
    rep["speed_1080p60"] = {"frames": r4["frames"], "wall_s": round(r4["wall_s"], 2),
                            "fps": round(r4["fps_achieved"], 1), "pipe_write_s": round(r4["pipe_write_s"], 2),
                            "render_loop_s": round(r4["render_s"], 2)}
    rep["pass_faster_than_realtime"] = r4["fps_achieved"] > 60.0
    _ = t0

    mp4 = r4["out"]
    v, a = stream_duration(mp4, "0:v"), stream_duration(mp4, "0:a")
    rep["durations"] = {"video_s": v, "audio_s": a, "frames": frame_count(mp4), "expected_s": 20.0}
    rep["pass_av_durations_equal"] = v is not None and a is not None and abs(v - a) <= 0.05

    r5 = export(song, 5, "ink_cold", (1080, 1920), 30, out=os.path.join(OUT, "portrait_1080x1920_30.mp4"),
                start=90.0, duration=8.0, dump_frames=[120], verbose=False)
    rep["portrait"] = {"ret": r5["ffmpeg_ret"], "fps": round(r5["fps_achieved"], 1), "frames": frame_count(r5["out"])}
    rep["pass_portrait_export"] = r5["ffmpeg_ret"] == 0 and rep["portrait"]["frames"] == 240

    errs = [r["ffmpeg_err"] for r in runs + [r3, r4, r5] if r["ffmpeg_err"]]
    rep["ffmpeg_errors"] = errs
    path = os.path.join(OUT, "report_m5.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
