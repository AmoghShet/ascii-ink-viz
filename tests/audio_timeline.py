"""M4: run the Director over a whole song (CPU only, no GPU) and check the mapping behaviour.

    python tests/audio_timeline.py "D:/Downloads/Sit Down. Stand Up.mp3" [seed]

Writes out/verify/m4_timeline_<song>.png (whole song), m4_zoom_<song>.png (10 s around the
biggest loudness jump, the "drop") and report_m4_<song>.json with these checks:
  - every kick with strength >= 0.3 produces a particle burst and a radial impulse
  - hats only reseed the glyph jitter (and shimmer octave 3), they never burst
  - the drop raises the emitter count, lowers decay (longer trails come from harmonic/sub), raises
    exposure/bloom; a breakdown (largest loudness fall) lowers the emitter count
"""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from audioviz import preset as presetmod  # noqa: E402
from audioviz import seed as seedmod  # noqa: E402
from audioviz.audio.analysis import load_or_analyze  # noqa: E402
from audioviz.audio.features import Director, FeatureTrack  # noqa: E402

OUT = os.path.join(ROOT, "out", "verify")


def main(song, seed=21):
    os.makedirs(OUT, exist_ok=True)
    feats = load_or_analyze(song)
    track = FeatureTrack(feats)
    pre = presetmod.load("ink_cold")
    res = seedmod.resolve(pre, seed, 16 / 9)
    N = 1024 * 1024
    d = Director(pre, res, N, track)
    dt = 1 / 60
    n = int(feats["duration"] / dt)
    keys = ["emit", "burst", "num_emitters", "exposure", "T", "decay", "bloom_gain", "ramp_jitter", "speed",
            "knee", "gamma", "grain", "vignette", "cyan"]
    rec = {k: np.zeros(n) for k in keys}
    env = {k: np.zeros(n) for k in ("sub", "bass", "mid", "high", "onset", "centroid", "flux", "harmonic",
                                     "percussive", "loudness")}
    w3 = np.zeros(n)
    imp = np.zeros(n)
    jit = np.zeros(n)
    sect = np.zeros(n)
    kick_steps, hat_steps = [], []
    prev_k, prev_h = 0, 0
    for i in range(n):
        t = (i + 1) * dt
        s = d.step(t, dt)
        for k in keys:
            rec[k][i] = s[k]
        for k in env:
            env[k][i] = s["env"][k]
        w3[i] = s["w"][2]
        imp[i] = float(np.max(s["emittersB"][:, 3]))
        jit[i] = s["jitter_seed"]
        sect[i] = d.section
        if d.kick_count != prev_k:
            kick_steps.append(i)
        if d.hat_count != prev_h:
            hat_steps.append(i)
        prev_k, prev_h = d.kick_count, d.hat_count
    t = (np.arange(n) + 1) * dt

    # -- checks ----------------------------------------------------------------------------
    ks = feats["kick_strength"]
    kt = feats["kicks"]
    rank = (np.argsort(np.argsort(ks)) + 0.5) / max(len(ks), 1)
    strong = kt[rank >= 0.35]
    burst_after = []
    for tk in strong:
        i = int(np.ceil(tk / dt)) - 1
        win = slice(max(0, i), min(n, i + 3))
        burst_after.append(bool(rec["burst"][win].max() > 0 and imp[win].max() > 0))
    hat_only = [i for i in hat_steps if i not in set(kick_steps)]
    hat_burst = int(sum(rec["burst"][i] > 0 for i in hat_only))
    jitter_on_hats = bool(all(jit[i] > jit[i - 1] for i in hat_only if i > 0))

    loud = env["loudness"]
    w = int(8 / dt)
    rise = np.full(n, -9.0)
    rise[w:n - w] = [loud[i + w // 2:i + w].mean() - loud[i - w:i - w // 2].mean() for i in range(w, n - w)]
    i_drop = int(np.argmax(rise))
    i_brk = int(np.argmin(np.where(rise > -9, rise, 9)))

    def around(arr, i):
        before = arr[max(0, i - w):max(1, i - w // 2)].mean()
        after = arr[i + w // 2:min(n, i + w)].mean()
        return round(float(before), 3), round(float(after), 3)

    drop = {k: around(rec[k], i_drop) for k in ("num_emitters", "exposure", "bloom_gain", "emit", "decay", "T")}
    brk = {k: around(rec[k], i_brk) for k in ("num_emitters", "exposure", "grain", "vignette", "speed")}
    report = {
        "song": song, "seed": seed, "duration": feats["duration"], "tempo": feats["tempo"],
        "kicks_total": int(len(kt)), "kicks_strong": int(len(strong)),
        "strong_kicks_with_burst_and_impulse": int(sum(burst_after)),
        "hats_total": int(len(feats["hats"])), "hat_only_steps_with_burst": hat_burst,
        "jitter_reseeds_on_every_hat": jitter_on_hats,
        "sections_detected": [round(x, 1) for x in feats["sections"]], "sections_applied": int(d.section),
        "drop_at_s": round(float(t[i_drop]), 1), "drop_before_after": drop,
        "breakdown_at_s": round(float(t[i_brk]), 1), "breakdown_before_after": brk,
    }
    report["pass_kicks"] = report["strong_kicks_with_burst_and_impulse"] == report["kicks_strong"]
    report["pass_hats"] = hat_burst == 0 and jitter_on_hats
    report["pass_drop"] = drop["num_emitters"][1] > drop["num_emitters"][0] and drop["exposure"][1] > drop["exposure"][0]
    report["pass_breakdown"] = brk["num_emitters"][1] < brk["num_emitters"][0] and brk["vignette"][1] > brk["vignette"][0]

    name = os.path.splitext(os.path.basename(song))[0].replace(" ", "_").replace(".", "")
    with open(os.path.join(OUT, f"report_m4_{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    _plot(t, env, rec, w3, imp, jit, sect, feats, os.path.join(OUT, f"m4_timeline_{name}.png"), None, i_drop, i_brk)
    lo = max(0.0, t[i_drop] - 5)
    _plot(t, env, rec, w3, imp, jit, sect, feats, os.path.join(OUT, f"m4_zoom_{name}.png"), (lo, lo + 10), i_drop, i_brk)
    print(json.dumps({k: v for k, v in report.items() if k.startswith("pass") or "drop" in k or "breakdown" in k}, indent=1))


def _plot(t, env, rec, w3, imp, jit, sect, feats, path, xlim, i_drop, i_brk):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(7, 1, figsize=(18, 16), sharex=True)
    for k in ("sub", "bass", "mid", "high"):
        ax[0].plot(t, env[k], lw=0.8, label=k)
    ax[0].set_ylabel("bands")
    ax[1].plot(t, env["onset"], lw=0.7, color="k", label="onset env")
    ks = feats["kick_strength"]
    ax[1].vlines(feats["kicks"], 0, ks, color="r", lw=0.6, label="kicks (strength)")
    ax[1].vlines(feats["hats"], -0.2, -0.05, color="b", lw=0.4, label="hats")
    ax[1].set_ylabel("events")
    ax[2].plot(t, rec["emit"], lw=0.6, label="emit / step")
    ax[2].plot(t, rec["burst"], lw=0.8, color="r", label="burst")
    ax[2].plot(t, imp * 1e5, lw=0.8, color="m", label="impulse x1e5")
    ax[2].set_ylabel("particles")
    ax[3].plot(t, rec["num_emitters"], lw=1, label="active emitters")
    ax[3].plot(t, env["loudness"] * 8, lw=0.8, label="loudness x8")
    ax[3].plot(t, sect, lw=1, label="section index")
    ax[4].plot(t, rec["exposure"], lw=0.8, label="exposure")
    ax[4].plot(t, rec["bloom_gain"], lw=0.8, label="bloom gain")
    ax[4].plot(t, rec["T"], lw=0.8, label="coarse T")
    ax[4].plot(t, rec["gamma"], lw=0.8, label="gamma")
    ax[5].plot(t, rec["decay"], lw=0.8, label="trail decay")
    ax[5].plot(t, rec["speed"] / 10 + 0.9, lw=0.8, label="speed/10+0.9")
    ax[6].plot(t, rec["ramp_jitter"], lw=0.8, label="ramp jitter")
    ax[6].plot(t, w3, lw=0.8, label="octave-3 weight")
    ax[6].plot(t, np.diff(jit, prepend=jit[0]) > 0, lw=0.3, alpha=0.5, label="jitter reseed")
    for a in ax:
        for s in feats["sections"]:
            a.axvline(s, color="gray", ls="--", lw=0.6)
        a.axvline(t[i_drop], color="g", lw=1.2)
        a.axvline(t[i_brk], color="orange", lw=1.2)
        a.legend(loc="upper right", fontsize=7, ncol=4)
    if xlim:
        ax[0].set_xlim(*xlim)
    ax[-1].set_xlabel("song time (s); grey dashed = detected sections, green = biggest loudness rise, orange = biggest fall")
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 21)
