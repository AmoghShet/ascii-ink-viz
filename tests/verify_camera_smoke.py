"""Camera-as-smoke checks that need no light: a synthetic test image goes straight into the GPU
pipeline (no camera process), and the shaders' own buffers are measured.

    python tests/verify_camera_smoke.py

1. clarity: correlation between the fine-cell ramp and the camera image, new settings vs the
   previous ones (blur 0.7, contrast 0.35, relax 0.12, gain 0.18, 240x180 image)
2. gray ghosts: after the lit shape moves, the trail target at the old spot is about
   ghost_gray x the live level and halves every ghost_s seconds
3. dark room: a near-black frame (mean ~4/255) must not turn into bright smoke
4. fine glyphs: share of the lit shape drawn in coarse cells, fine_bias 0 vs the preset
Writes out/verify/report_camera_smoke.json and frames in out/verify/camsmoke_*.png.
"""
import json
import os
import sys

import cv2
import moderngl
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audioviz import preset as presetmod  # noqa: E402
from audioviz.app import apply_overrides  # noqa: E402
from audioviz.config import Tunables  # noqa: E402
from audioviz.engine import Engine  # noqa: E402
from audioviz.gl.context import create_headless  # noqa: E402
from audioviz.gl.pingpong import Target, make_texture  # noqa: E402
from audioviz.inputs.camera import BG_SIZE, MIN_LEVEL_RANGE, CameraStir  # noqa: E402

OUT = os.path.join(ROOT, "out", "verify")
DT = 1.0 / 60.0
W, H = 1920, 1080


def test_image(shift=0, size=BG_SIZE, dark=False):
    w, h = size
    s = w / 480.0
    img = np.full((h, w), 22, np.uint8)
    if dark == "monitor":   # lights off, face lit by a monitor: values ~10-25 of 255
        img = np.clip(np.random.default_rng(2).normal(3, 2 / np.sqrt(3), (h, w)), 0, 255).astype(np.uint8)
        cv2.ellipse(img, (int(200 * s), int(180 * s)), (int(70 * s), int(95 * s)), 0, 0, 360, 20, -1)
        return img
    if dark:
        rng = np.random.default_rng(1)
        # sensor noise after the camera process's temporal smoothing (about 3 frames)
        return np.clip(rng.normal(4, 2 / np.sqrt(3), (h, w)), 0, 255).astype(np.uint8)
    cx, cy = int((200 + shift) * s), int(180 * s)
    cv2.ellipse(img, (cx, cy), (int(70 * s), int(95 * s)), 0, 0, 360, 200, -1)          # lit face
    for ex in (-25, 25):
        cv2.circle(img, (cx + int(ex * s), cy - int(20 * s)), int(9 * s), 60, -1)       # eyes
    cv2.line(img, (cx - int(25 * s), cy + int(40 * s)), (cx + int(25 * s), cy + int(40 * s)), 70, max(1, int(5 * s)))
    for y in range(h):                                                                   # gradient bar
        img[y, int(20 * s):int(60 * s)] = int(255 * y / (h - 1))
    cv2.putText(img, "AUDIOVIZ", (int(300 * s), int(330 * s)), cv2.FONT_HERSHEY_SIMPLEX, 1.1 * s, 235, max(1, int(3 * s)))
    return img


class FakeCam:
    """Uploads a numpy image the way CameraStir.background() does (flip, mipmaps, auto-levels)."""

    def __init__(self, ctx, size):
        self.tex = make_texture(ctx, size, 1, "f1", "linear")
        self.tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        self.size = size

    def set(self, img, eng):
        if img.shape[::-1] != self.size:
            img = cv2.resize(img, self.size, interpolation=cv2.INTER_AREA)
        self.tex.write(np.flipud(img).copy().tobytes())
        self.tex.build_mipmaps()
        lo, hi = np.percentile(img[::2, ::2], (5, 95)) / 255.0
        eng.set_camera_background(self.tex, CameraStir.cover_scale(eng.aspect), lo, max(hi, lo + MIN_LEVEL_RANGE))
        return lo, max(hi, lo + MIN_LEVEL_RANGE)


def expected_cells(img, eng, grid, cell, cb, lo, hi):
    """CPU version of cam_level() at each fine cell centre (for the clarity correlation)."""
    cols, rows = grid
    sx, sy = CameraStir.cover_scale(eng.aspect)
    ih, iw = img.shape
    blur = cv2.GaussianBlur(img.astype(np.float32) / 255.0, (0, 0), max(0.5, 2 ** cb["blur"] * 0.6))
    out = np.zeros((rows, cols), np.float32)
    for r in range(rows):
        for c in range(cols):
            u = ((c + 0.5) * cell[0]) / W
            v = 1.0 - ((r + 0.5) * cell[1]) / H
            cu = (u - 0.5) * sx + 0.5
            cv_ = (v - 0.5) * sy + 0.5
            x = int(np.clip(cu * iw, 0, iw - 1))
            y = int(np.clip((1.0 - cv_) * ih, 0, ih - 1))
            g = np.clip((blur[y, x] - lo) / max(hi - lo, 0.02), 0, 1)
            c2 = cb["contrast"]
            t = np.clip((g - 0.5 * c2) / max((1 - 0.15 * c2) - 0.5 * c2, 1e-4), 0, 1)
            g = t * t * (3 - 2 * t)
            out[r, c] = g ** cb["gamma"]
    return out


def glyph_corr(frame_rgb, exp, mask, cell):
    """What the viewer sees: mean brightness of the drawn glyphs per fine cell vs the camera image."""
    lum = frame_rgb.astype(np.float32).mean(axis=2)
    cw, ch = cell
    rows, cols = exp.shape
    lum = lum[:rows * ch, :cols * cw]
    if lum.shape != (rows * ch, cols * cw):
        pad = np.zeros((rows * ch, cols * cw), np.float32)
        pad[:lum.shape[0], :lum.shape[1]] = lum
        lum = pad
    cells = lum.reshape(rows, ch, cols, cw).mean(axis=(1, 3))
    # 3x3 cell neighbourhood average: perceived tone, as seen at arm's length
    cells = cv2.blur(cells, (3, 3))
    e = cv2.blur(exp.astype(np.float32), (3, 3))
    return round(float(np.corrcoef(cells[mask].ravel(), e[mask].ravel())[0, 1]), 3)


def run_case(ctx, overrides, label, size=BG_SIZE):
    pre = apply_overrides(presetmod.load("ink_cold"), overrides)
    cb = pre["tone"]["camera_bg"]
    eng = Engine(ctx, (W, H), pre, 5, "high", Tunables(timers=False))
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    cam = FakeCam(ctx, size)
    img = test_image(size=size)
    lo, hi = cam.set(img, eng)
    eng.prewarm(2.0)
    t = 2.0
    for i in range(120):
        t += DT
        eng.sim_step(t, DT)
        if i % 2 == 0:
            eng.render(tgt.fbo)
    eng.render(tgt.fbo)
    cellF, gridF, cellC, gridC = eng.grids()
    cells = np.frombuffer(eng.grade.targets["F"].tex.read(), np.float16).astype(np.float32).reshape(gridF[1], gridF[0], 4)
    exp = expected_cells(img, eng, gridF, cellF, cb, lo, hi)
    # cells inside the camera frame and where the camera is most of the density
    r = cells[..., 0]
    share = cells[..., 3]
    mask = share > 0.5
    corr = float(np.corrcoef(r[mask].ravel(), exp[mask].ravel())[0, 1]) if mask.sum() > 50 else float("nan")
    frame = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
    cv2.imwrite(os.path.join(OUT, f"camsmoke_{label}.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    # coarse share inside the lit face
    cc = np.frombuffer(eng.grade.targets["C"].tex.read(), np.float16).astype(np.float32).reshape(gridC[1], gridC[0], 4)
    d = eng.ubo.data
    T, B = float(d["lookB"][0]), float(d["lookB"][1])
    fb = float(d["camC"][3]) if d["camA"][3] > 0.5 and d["camA"][0] <= 0 else 0.0
    Tc = T + fb * cc[..., 3]
    wC = np.clip((cc[..., 0] - (Tc - B)) / (2 * B), 0, 1)
    face = cc[..., 3] > 0.5
    coarse_share = float((wC[face] > 0.5).mean()) if face.any() else float("nan")
    res = {"clarity_corr": round(corr, 3), "camera_cells": int(mask.sum()), "coarse_share_in_camera": round(coarse_share, 3),
           "glyph_clarity_corr": glyph_corr(frame, exp, mask, cellF)}
    prev = os.path.join(OUT, "camsmoke_prev_shader.png")
    if label == "new_settings" and os.path.isfile(prev):
        res["glyph_clarity_corr_previous_render"] = glyph_corr(cv2.cvtColor(cv2.imread(prev), cv2.COLOR_BGR2RGB), exp, mask, cellF)
    return eng, tgt, cam, res, cb


def ghost_case(ctx):
    eng, tgt, cam, _, cb = run_case(ctx, [], "ghost_before")
    # live level of the face centre in the ghost buffer before the move
    gw, gh = eng.ghost.size

    def ghost_at(px_cam):
        buf = np.frombuffer(eng.ghost.tex.read(), np.float16).astype(np.float32).reshape(gh, gw, 2)
        # face centre: camera x=200 (of 480), y=180 (of 360) -> screen uv through the cover crop
        sx, sy = CameraStir.cover_scale(eng.aspect)
        cu, cvv = px_cam[0] / 480.0, 1.0 - px_cam[1] / 360.0
        u, v = (cu - 0.5) / sx + 0.5, (cvv - 0.5) / sy + 0.5
        x, y = int(u * gw), int(v * gh)
        patch = buf[y - 2:y + 3, x - 2:x + 3]
        return float(patch[..., 0].mean()), float(patch[..., 1].mean())

    live0, hold0 = ghost_at((200, 180))
    cam.set(test_image(shift=200), eng)        # the face moves 200 px to the right
    samples = []
    t = 4.0
    for i in range(int(3.0 * 60)):
        t += DT
        eng.sim_step(t, DT)
        if i % 2 == 0:
            eng.render(tgt.fbo)
        if (i + 1) in (15, 54, 108, 162):
            live, hold = ghost_at((200, 180))
            samples.append({"t_after_move_s": round((i + 1) * DT, 2), "live": round(live, 3), "hold": round(hold, 3),
                            "trail_target_over_old_live": round(max(live, cb["ghost_gray"] * hold) / max(live0, 1e-4), 3)})
        if (i + 1) == 30:
            frame = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
            cv2.imwrite(os.path.join(OUT, "camsmoke_ghost_0p5s_after_move.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    halves = [s["hold"] for s in samples]
    eng.release()
    return {"live_before": round(live0, 3), "hold_before": round(hold0, 3), "after_move": samples,
            "ghost_gray": cb["ghost_gray"], "ghost_s": cb["ghost_s"],
            "expected_hold_ratio_at_0.9s": 0.5, "measured_hold_ratio_0.25_to_1.8s": round(halves[2] / max(halves[0], 1e-4), 3)}


def monitor_case(ctx):
    """Lights off, monitor-lit face: the face must still read (camera level high on the face,
    low around it)."""
    pre = presetmod.load("ink_cold")
    eng = Engine(ctx, (W, H), pre, 5, "high", Tunables(timers=False))
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    cam = FakeCam(ctx, BG_SIZE)
    cam.set(test_image(dark="monitor"), eng)
    eng.prewarm(1.0)
    for i in range(90):
        eng.sim_step(1.0 + i * DT, DT)
    eng.render(tgt.fbo)
    gw, gh = eng.ghost.size
    g = np.frombuffer(eng.ghost.tex.read(), np.float16).astype(np.float32).reshape(gh, gw, 2)[..., 0]
    sx, sy = CameraStir.cover_scale(eng.aspect)
    u, v = (200 / 480.0 - 0.5) / sx + 0.5, ((1 - 180 / 360.0) - 0.5) / sy + 0.5
    x, y = int(u * gw), int(v * gh)
    face = float(g[y - 3:y + 4, x - 3:x + 4].mean())
    around = float(np.median(g))
    frame = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
    cv2.imwrite(os.path.join(OUT, "camsmoke_monitor_lit.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    eng.release()
    return {"face_level": round(face, 3), "surround_median": round(around, 3)}


def dark_case(ctx, old_rule):
    pre = presetmod.load("ink_cold")
    eng = Engine(ctx, (W, H), pre, 5, "high", Tunables(timers=False))
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    cam = FakeCam(ctx, BG_SIZE)
    img = test_image(dark=True)
    cam.set(img, eng)
    if old_rule:   # the earlier stretch (white >= black + 0.05)
        lo, hi = np.percentile(img[::2, ::2], (5, 95)) / 255.0
        eng.set_camera_background(cam.tex, CameraStir.cover_scale(eng.aspect), lo, max(hi, lo + 0.05))
    eng.prewarm(1.0)
    for i in range(90):
        eng.sim_step(1.0 + i * DT, DT)
    eng.render(tgt.fbo)
    gw, gh = eng.ghost.size
    g = np.frombuffer(eng.ghost.tex.read(), np.float16).astype(np.float32).reshape(gh, gw, 2)
    frame = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
    cv2.imwrite(os.path.join(OUT, f"camsmoke_dark_{'oldrule' if old_rule else 'new'}.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    eng.release()
    return {"camera_level_mean": round(float(g[..., 0].mean()), 3), "camera_level_p99": round(float(np.percentile(g[..., 0], 99)), 3)}


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    ctx = create_headless()
    rep = {}
    old = ["tone.camera_bg.blur=0.7", "tone.camera_bg.contrast=0.35", "tone.camera_bg.relax=0.12",
           "tone.camera_bg.gain=0.18", "tone.camera_bg.gamma=1.3", "tone.camera_bg.fine_bias=0.0"]
    e, t_, c, r_old, _ = run_case(ctx, old, "old_settings", size=(240, 180))
    e.release()
    e, t_, c, r_new, _ = run_case(ctx, [], "new_settings")
    e.release()
    e, t_, c, r_nobias, _ = run_case(ctx, ["tone.camera_bg.fine_bias=0.0"], "new_no_fine_bias")
    e.release()
    rep["clarity"] = {"old": r_old, "new": r_new, "new_without_fine_bias": r_nobias}
    rep["ghost"] = ghost_case(ctx)
    rep["dark"] = {"new_rule": dark_case(ctx, False), "old_rule": dark_case(ctx, True)}
    rep["monitor_lit"] = monitor_case(ctx)
    g = rep["ghost"]["after_move"]
    rep["pass_clearer"] = r_new["clarity_corr"] > r_old["clarity_corr"]
    if "glyph_clarity_corr_previous_render" in r_new:
        rep["pass_glyphs_clearer_than_previous_render"] = r_new["glyph_clarity_corr"] >= r_new["glyph_clarity_corr_previous_render"]
    rep["pass_fine_glyphs"] = r_new["coarse_share_in_camera"] <= r_nobias["coarse_share_in_camera"]
    rep["pass_gray_trails"] = all(s["trail_target_over_old_live"] <= rep["ghost"]["ghost_gray"] + 0.05 for s in g)
    rep["pass_trails_fade"] = g[-1]["hold"] < 0.35 * g[0]["hold"]
    rep["pass_dark_stays_dark"] = rep["dark"]["new_rule"]["camera_level_mean"] < 0.15
    rep["pass_monitor_face_reads"] = (rep["monitor_lit"]["face_level"] > 0.5
                                      and rep["monitor_lit"]["surround_median"] < 0.15)
    with open(os.path.join(OUT, "report_camera_smoke.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    print(json.dumps(rep, indent=1))
