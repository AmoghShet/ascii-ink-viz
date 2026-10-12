"""M7: the camera stir moves the smoke and changes nothing but P0's external-velocity term.

    python tests/verify_camera.py [--device]      (default: synthetic moving blob, works in the dark)

Runs the same seed twice for 6 s: once with the stir (gain from the preset), once without, and
compares the flow fields and density. Also records the flow thread cost and the upload cost.
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
from audioviz.gl.context import create_headless  # noqa: E402
from audioviz.gl.pingpong import Target  # noqa: E402
from audioviz.inputs.camera import CameraStir  # noqa: E402

OUT = os.path.join(ROOT, "out", "verify")
DT = 1 / 60


def main(use_device=False):
    os.makedirs(OUT, exist_ok=True)
    ctx = create_headless()
    pre = presetmod.load("ink_cold")
    W, H = 1920, 1080
    tgt = Target(ctx, (W, H), 4, "f1", "nearest")
    cam = CameraStir(ctx, synthetic=not use_device, gain=pre["sim"]["ext_flow_gain"])
    cam.start()
    t0 = time.perf_counter()
    while cam.frames < 10 and time.perf_counter() - t0 < 10:
        time.sleep(0.05)
    rep = {"source": cam.describe(), "status_at_start": cam.status()}
    flows, dens, uploads = {}, {}, []
    for stir in (False, True):
        eng = Engine(ctx, (W, H), pre, 9, "high", Tunables(timers=True))
        eng.prewarm(2.0)
        for i in range(360):
            t = (i + 1) * DT
            if stir:
                u0 = time.perf_counter()
                tex, scale, gain = cam.update(eng.aspect)
                uploads.append((time.perf_counter() - u0) * 1000)
                eng.set_external_flow(tex, scale, gain)
            eng.sim_step(t, DT)
            if i % 2 == 0:
                eng.render(tgt.fbo)
                eng.timers.end_frame()
            if stir and i == 359:
                for v, name in ((2, "flow"), (1, "density"), (0, "ascii")):
                    eng.view = v
                    eng.render(tgt.fbo)
                    img = np.flipud(np.frombuffer(tgt.fbo.read(components=3), np.uint8).reshape(H, W, 3))
                    cv2.imwrite(os.path.join(OUT, f"m7_stir_{name}.png"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        flows[stir] = np.frombuffer(eng.flow.tex.read(), np.float16).astype(np.float32).reshape(eng.flow.size[1], eng.flow.size[0], 2)
        dens[stir] = np.frombuffer(eng.trails.tex.read(), np.float16).astype(np.float32)
        rep[f"p0_ms_{'stir' if stir else 'nostir'}"] = round(eng.timers.mean("P0 flow"), 3)
        eng.release()
    cam_status = cam.status()
    cam.stop()
    dflow = np.sqrt(((flows[True] - flows[False]) ** 2).sum(axis=2))
    rep.update({
        "status_at_end": cam_status,
        "flow_thread_ms": round(cam.flow_ms, 2),
        "upload_ms_mean": round(float(np.mean(uploads)), 3),
        "flow_diff_mean": round(float(dflow.mean()), 4),
        "flow_diff_max": round(float(dflow.max()), 4),
        "flow_base_mean": round(float(np.sqrt((flows[False] ** 2).sum(axis=2)).mean()), 4),
        "density_l1_diff": round(float(np.abs(dens[True] - dens[False]).mean()), 5),
    })
    rep["pass_stir_moves_smoke"] = rep["flow_diff_max"] > 0.05 and rep["density_l1_diff"] > 1e-4
    rep["pass_flow_thread_under_5ms"] = rep["flow_thread_ms"] < 5.0
    path = os.path.join(OUT, "report_m7.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main("--device" in sys.argv)
