"""Headless milestone checks. Writes out/verify/report_<milestone>.json plus images.

  python tests/verify.py m1        seeds differ, no collapse over 60 s, GPU timer sum (1M particles)
  python tests/verify.py m2        density bounded over 60 s, portrait 1080x1920 looks tall
  python tests/verify.py m3        glyph orientation follows a synthetic flow; P6 timer
  python tests/verify.py dummy     splat through the Apple dummy-attribute path draws the same image
"""
import json
import os
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audioviz import preset as presetmod  # noqa: E402
from audioviz.config import Tunables  # noqa: E402
from audioviz.engine import Engine  # noqa: E402
from audioviz.gl.context import create_headless, gl_info  # noqa: E402
from audioviz.gl.pingpong import Target  # noqa: E402

OUT = os.path.join(ROOT, "out", "verify")
os.makedirs(OUT, exist_ok=True)
DT = 1.0 / 60.0


def grab(eng, tgt, view=0):
    eng.view = view
    eng.render(tgt.fbo)
    W, H = tgt.size
    img = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def dens(eng):
    return np.frombuffer(eng.trails.tex.read(), np.float16).astype(np.float32).reshape(eng.trails.size[1], eng.trails.size[0])


def run_for(eng, tgt, t0, seconds, every=None, cb=None):
    n = int(round(seconds * 60))
    for i in range(n):
        t = t0 + (i + 1) * DT
        eng.sim_step(t, DT)
        if i % 2 == 0:
            eng.render(tgt.fbo)
        if every and cb and (i + 1) % int(every * 60) == 0:
            cb(t)
    return t0 + n * DT


def m1(ctx):
    rep = {"gl": gl_info(ctx)["GL_RENDERER"]}
    pre = presetmod.load("ink_cold")
    W, H = 1920, 1080
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    seeds = {}
    for seed in (1, 2):
        eng = Engine(ctx, (W, H), pre, seed, "high", Tunables(timers=True))
        lay = eng.resolved["layout"]["positions"][: eng.resolved["emitter_count"]]
        stats = []

        def cb(t):
            d = dens(eng)
            st = eng.particles.read_state()
            alive = st[..., 2] < 1.0
            pos = st[alive][:, :2]
            # spread of live particles: occupied cells of a 64x36 grid
            occ = 0
            if len(pos):
                gx = np.clip((pos[:, 0] * 64).astype(int), 0, 63)
                gy = np.clip((pos[:, 1] * 36).astype(int), 0, 35)
                occ = len(np.unique(gy * 64 + gx)) / (64 * 36)
            flat = np.sort(d.ravel())[::-1]
            top1 = float(flat[: len(flat) // 100].sum() / max(flat.sum(), 1e-9))
            stats.append({"t": round(t, 1), "alive_frac": round(float(alive.mean()), 3),
                          "occupied_cells": round(occ, 3), "coverage": round(float((d > 0.02).mean()), 3),
                          "mass_in_top1pct": round(top1, 3), "dens_max": round(float(d.max()), 3)})

        run_for(eng, tgt, 0.0, 60.0, every=10.0, cb=cb)
        cv2.imwrite(os.path.join(OUT, f"m1_seed{seed}_60s_density.png"), grab(eng, tgt, 1))
        cv2.imwrite(os.path.join(OUT, f"m1_seed{seed}_60s_ascii.png"), grab(eng, tgt, 0))
        # GPU timers: settle, then average
        for i in range(120):
            eng.sim_step(60.0 + i * DT, DT)
            eng.render(tgt.fbo)
            eng.timers.end_frame()
        summ = eng.timers.summary()
        seeds[seed] = {"emitters": eng.resolved["emitter_count"], "layout": [[round(v, 3) for v in p] for p in lay],
                       "palette": eng.resolved["palette_variant"], "glyphs": eng.resolved["glyph_set"],
                       "over_time": stats, "gpu_ms": summ, "gpu_total_ms": round(sum(summ.values()), 3)}
        eng.release()
    rep["seeds"] = seeds
    l1 = np.array(seeds[1]["layout"][:1])
    l2 = np.array(seeds[2]["layout"][:1])
    rep["pass_seeds_differ"] = bool(seeds[1]["layout"] != seeds[2]["layout"])
    last = [s["over_time"][-1] for s in seeds.values()]
    first = [s["over_time"][0] for s in seeds.values()]
    rep["pass_no_collapse"] = all(b["occupied_cells"] >= 0.6 * a["occupied_cells"] and b["mass_in_top1pct"] < 0.5
                                  for a, b in zip(first, last))
    rep["pass_gpu_under_6ms"] = all(s["gpu_total_ms"] < 6.0 for s in seeds.values())
    _ = l1, l2
    return rep


def m2(ctx):
    rep = {}
    pre = presetmod.load("ink_cold")
    W, H = 1920, 1080
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    eng = Engine(ctx, (W, H), pre, 3, "high", Tunables(timers=True))
    maxes = []

    def cb(t):
        maxes.append(round(float(dens(eng).max()), 3))

    run_for(eng, tgt, 0.0, 60.0, every=5.0, cb=cb)
    ps, splat = eng.point_size_and_splat()
    # analytic bound: a pixel can receive at most `k` overlapping particles per step; with decay
    # it can never exceed k * splat_peak / (1 - decay_min). Use k = particles per pixel at the
    # densest observed block as the empirical k.
    rep["dens_max_over_time"] = maxes
    rep["splat"] = splat
    rep["single_particle_bound"] = splat / (1.0 - 0.992)
    rep["pass_bounded"] = bool(max(maxes[len(maxes) // 2:]) <= 1.5 * max(maxes[: len(maxes) // 2] + [1e-6]) + 0.5)
    for _ in range(90):
        eng.sim_step(60.0, DT)
        eng.render(tgt.fbo)
        eng.timers.end_frame()
    rep["gpu_total_ms"] = round(sum(eng.timers.summary().values()), 3)
    rep["pass_frame_under_7ms"] = rep["gpu_total_ms"] < 7.0
    eng.release()
    tgt.release()
    # portrait
    W, H = 1080, 1920
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    eng = Engine(ctx, (W, H), pre, 3, "high", Tunables(timers=False))
    run_for(eng, tgt, 0.0, 12.0)
    cv2.imwrite(os.path.join(OUT, "m2_portrait_1080x1920_ascii.png"), grab(eng, tgt, 0))
    cv2.imwrite(os.path.join(OUT, "m2_portrait_1080x1920_density.png"), grab(eng, tgt, 1))
    cv2.imwrite(os.path.join(OUT, "m2_portrait_1080x1920_flow.png"), grab(eng, tgt, 2))
    rep["portrait_info"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in eng.info().items()}
    # isotropy check: longitudinal correlation length of the flow (vx along x vs vy along y) must
    # match in pixels; a stretched field would differ by the aspect ratio (1.78x)
    vel = np.frombuffer(eng.flow.tex.read(), np.float16).astype(np.float32).reshape(eng.flow.size[1], eng.flow.size[0], 2)

    def corr_len(a, axis):
        a = a - a.mean()
        n = a.shape[axis]
        c = [np.mean(a * np.roll(a, k, axis=axis)) / np.mean(a * a) for k in range(1, n // 4)]
        below = [i + 1 for i, v in enumerate(c) if v < 0.5]
        return below[0] if below else n // 4

    lx, ly = corr_len(vel[..., 0], 1), corr_len(vel[..., 1], 0)
    rep["portrait_flow_corr_len_px_x_y"] = [lx, ly]
    rep["pass_portrait_not_stretched"] = bool(0.6 < lx / max(ly, 1) < 1.6)
    eng.release()
    tgt.release()
    return rep


def m3(ctx):
    """Synthetic uniform flow at 0/45/90/135 degrees; the drawn glyphs must come from that atlas row."""
    from audioviz.render.ascii import build_atlas

    rep = {}
    pre = presetmod.load("ink_cold")
    pre["tone"]["auto_exposure"]["enabled"] = False
    W, H = 1920, 1080
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    eng = Engine(ctx, (W, H), pre, 1, "high", Tunables(timers=True))
    eng.sim_step(1.0, DT)
    cellF, gridF, cellC, gridC = eng.grids()
    g = eng.resolved["params"]["glyphs"]
    atlas, rows = build_atlas(g["sets"], eng.resolved["glyph_set"], g["dir_rows"], cellF, g["font"])
    cw, ch = cellF
    glyphs = []
    for r in range(5):
        for c in range(1, 7):   # directional rows only differ in columns 1..6
            gimg = atlas[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw].astype(np.float32)
            if gimg.max() > 0:
                glyphs.append((r, c, (gimg - gimg.mean()) / (gimg.std() + 1e-6)))
    # glyphs that look the same as a glyph of another row (centred dots, ':' in two rows) cannot vote
    unique = []
    for (r, c, gi) in glyphs:
        amb = any(r2 != r and float((gi * g2).mean()) > 0.9 for (r2, _, g2) in glyphs)
        if not amb:
            unique.append((r, c, gi))
    rep["voting_glyphs"] = [(r, rows[r][c]) for (r, c, _) in unique]
    expected = {0: 1, 45: 2, 90: 3, 135: 4}
    results = {}
    dW, dH = eng.trails.size
    fW, fH = eng.flow.size
    rng = np.random.default_rng(0)
    for deg, row in expected.items():
        a = np.radians(deg)
        v = np.zeros((fH, fW, 2), np.float32)
        v[..., 0], v[..., 1] = 0.2 * np.cos(a), 0.2 * np.sin(a)
        d = rng.uniform(0.30, 0.42, size=(dH, dW)).astype(np.float32)   # mid tones, below the coarse threshold
        eng.flow.tex.write(v.astype(np.float16).tobytes())
        eng.trails.tex.write(d.astype(np.float16).tobytes())
        eng.render(tgt.fbo)
        fr = np.frombuffer(eng.frame.tex.read(), np.float16).astype(np.float32).reshape(H, W, 4)
        lum = np.flipud(fr[..., :3].mean(axis=2))
        votes = np.zeros(5, int)
        for cy in range(2, gridF[1] - 2, 3):
            for cx in range(2, gridF[0] - 2, 3):
                blk = lum[cy * ch:(cy + 1) * ch, cx * cw:(cx + 1) * cw]
                if blk.max() <= 1e-4 or blk.shape != (ch, cw):
                    continue
                b = (blk - blk.mean()) / (blk.std() + 1e-6)
                best = max(glyphs, key=lambda gl: float((gl[2] * b).mean()))
                if any((best[0], best[1]) == (u[0], u[1]) for u in unique):
                    votes[best[0]] += 1
        results[deg] = {"expected_row": row, "votes_by_row": votes.tolist(),
                        "share_expected": round(float(votes[row] / max(votes.sum(), 1)), 3)}
        eng.grid_overlay = True
        cv2.imwrite(os.path.join(OUT, f"m3_orient_{deg}deg.png"), grab(eng, tgt, 0)[300:600, 600:1100])
        eng.grid_overlay = False
        for _ in range(30):
            eng.render(tgt.fbo)
            eng.timers.end_frame()
    rep["orientation"] = results
    rep["atlas_rows"] = ["".join(r) for r in rows]
    rep["pass_orientation"] = all(r["share_expected"] > 0.6 for r in results.values())
    # P6 cost on real smoke (the synthetic frames above light every cell: worst case)
    rep["p6_ascii_ms_all_cells_lit"] = round(eng.timers.mean("P6 ascii"), 3)
    eng.timers.hist.clear()
    t = run_for(eng, tgt, 1.0, 4.0)
    for i in range(120):
        eng.sim_step(t + i * DT, DT)
        eng.render(tgt.fbo)
        eng.timers.end_frame()
    rep["p6_ascii_ms"] = round(eng.timers.mean("P6 ascii"), 3)
    rep["pass_p6_about_0p3ms"] = rep["p6_ascii_ms"] < 0.6
    eng.release()
    return rep


def dummy(ctx):
    """The Apple fallback (dummy vertex attribute) must draw exactly what the empty-VAO path draws."""
    pre = presetmod.load("ink_cold")
    W, H = 960, 540
    imgs = []
    for flag in (False, True):
        tgt = Target(ctx, (W, H), 4, "f1", "nearest")
        eng = Engine(ctx, (W, H), pre, 4, "low", Tunables(timers=False, dummy_attrib=flag))
        run_for(eng, tgt, 0.0, 4.0)
        imgs.append(grab(eng, tgt, 1))
        eng.release()
        tgt.release()
    same = bool(np.array_equal(imgs[0], imgs[1]))
    return {"identical": same, "nonblank": bool(imgs[1].max() > 20), "pass": same and bool(imgs[1].max() > 20)}


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "m1"
    ctx = create_headless()
    t0 = time.perf_counter()
    rep = {"m1": m1, "m2": m2, "m3": m3, "dummy": dummy}[which](ctx)
    rep["elapsed_s"] = round(time.perf_counter() - t0, 1)
    rep["when"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(OUT, f"report_{which}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in rep.items() if k.startswith("pass") or k in ("elapsed_s",)}, indent=1))
    print("wrote", path)
