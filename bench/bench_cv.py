"""
AudioViz feasibility benchmark.

Measures, on this machine:
  A. environment (Python, OpenCV build, GPU seen by OpenGL)
  B. webcam capture: open time per backend (twice, to see if a stall repeats), achievable fps per mode
  C. NumPy FFT cost (2048-sample window)
  D. dense optical flow: OpenCV DIS (3 presets) and Farneback at several sizes
  E. MediaPipe Image Segmenter (selfie square / landscape / multiclass), VIDEO mode, CPU, several input sizes
  F. MediaPipe Pose Landmarker (lite / full), with and without segmentation mask, two input sizes
  G. moderngl: GL renderer string, texture upload cost (3 upload strategies), fullscreen-pass cost, readback

Usage:  python bench_cv.py [--no-camera] [--iters N] [--json out.json] [--skip-msmf]
"""
import argparse
import json
import os
import platform
import sys
import time
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(os.path.dirname(HERE), "models")
RESULTS = {"env": {}, "camera": {}, "fft": {}, "optical_flow": {}, "segmentation": {}, "pose": {}, "gl": {}, "errors": {}}


def stats(samples_ms):
    a = np.asarray(samples_ms, dtype=np.float64)
    return {
        "n": int(a.size),
        "mean_ms": round(float(a.mean()), 3),
        "median_ms": round(float(np.median(a)), 3),
        "p95_ms": round(float(np.percentile(a, 95)), 3),
        "min_ms": round(float(a.min()), 3),
        "max_ms": round(float(a.max()), 3),
    }


def bench(fn, iters, warmup=3):
    for _ in range(warmup):
        fn()
    out = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return stats(out)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
    sys.stdout.flush()


def record_error(key, exc):
    RESULTS["errors"][key] = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    print(f"  !! {key}: {RESULTS['errors'][key]}")
    sys.stdout.flush()


# ----------------------------------------------------------------------------- A
def env_info():
    section("A. Environment")
    import cv2
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "opencv_opencl_available": bool(cv2.ocl.haveOpenCL()),
        "opencv_threads": cv2.getNumThreads(),
    }
    build = cv2.getBuildInformation()
    vio = []
    grab = False
    for line in build.splitlines():
        if line.strip().startswith("Video I/O"):
            grab = True
            continue
        if grab:
            if line.strip() == "" or not line.startswith("    "):
                break
            vio.append(line.strip())
    info["opencv_video_io"] = vio
    try:
        import mediapipe as mp
        info["mediapipe"] = mp.__version__
    except Exception as e:  # noqa
        info["mediapipe"] = f"import failed: {e}"
    try:
        import moderngl
        info["moderngl"] = moderngl.__version__
    except Exception as e:  # noqa
        info["moderngl"] = f"import failed: {e}"
    for k, v in info.items():
        print(f"  {k}: {v}")
    RESULTS["env"] = info


# ----------------------------------------------------------------------------- B
def fourcc_str(v):
    v = int(v)
    return "".join(chr((v >> (8 * k)) & 0xFF) for k in range(4))


def open_cam(api, w, h, fourcc, fps=30):
    import cv2
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(0, api)
    open_ms = (time.perf_counter() - t0) * 1000.0
    if not cap.isOpened():
        return cap, open_ms
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap, open_ms


def camera_tests(iters, skip_msmf=False):
    section("B. Webcam capture (fresh VideoCapture per mode)")
    import cv2
    is_win = sys.platform.startswith("win")
    is_mac = sys.platform == "darwin"
    if is_win:
        backends = [("DSHOW", cv2.CAP_DSHOW)] + ([] if skip_msmf else [("MSMF", cv2.CAP_MSMF)])
    elif is_mac:
        backends = [("AVFOUNDATION", cv2.CAP_AVFOUNDATION)]
    else:
        backends = [("V4L2", cv2.CAP_V4L2)]

    modes = [(640, 480, None), (1280, 720, None), (1280, 720, "MJPG")]
    best_frames = None
    for name, api in backends:
        res = {"modes": {}}
        print(f"\n  backend {name}")
        for mi, (w, h, fourcc) in enumerate(modes):
            key = f"{w}x{h}" + (f"_{fourcc}" if fourcc else "")
            r = {"requested": [w, h, fourcc]}
            try:
                cap, open_ms = open_cam(api, w, h, fourcc)
                r["open_ms"] = round(open_ms, 1)
                r["opened"] = bool(cap.isOpened())
                if not r["opened"]:
                    print(f"    {key:>14}: FAILED to open (open took {open_ms:.0f} ms)")
                    cap.release()
                    res["modes"][key] = r
                    continue
                if mi == 0:
                    r["buffersize_set_ok"] = bool(cap.set(cv2.CAP_PROP_BUFFERSIZE, 1))
                    r["buffersize_get"] = cap.get(cv2.CAP_PROP_BUFFERSIZE)
                # warm up / let auto-exposure settle
                t0 = time.perf_counter()
                for _ in range(5):
                    cap.read()
                r["first_5_reads_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                n = max(iters, 60)
                times = []
                frames = []
                t_start = time.perf_counter()
                for i in range(n):
                    t0 = time.perf_counter()
                    ok, frame = cap.read()
                    times.append((time.perf_counter() - t0) * 1000.0)
                    if not ok:
                        r["read_failed_at"] = i
                        break
                    if i >= n - 3:
                        frames.append(frame)
                wall = time.perf_counter() - t_start
                r["actual"] = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
                r["fourcc"] = fourcc_str(cap.get(cv2.CAP_PROP_FOURCC))
                r["reported_fps"] = cap.get(cv2.CAP_PROP_FPS)
                r["measured_fps"] = round(len(times) / wall, 1) if wall > 0 else None
                r["read_block"] = stats(times)
                if frames:
                    r["frame_shape"] = list(frames[-1].shape)
                print(f"    {key:>14}: open={r['open_ms']:.0f} ms  actual {r['actual'][0]}x{r['actual'][1]} fourcc={r['fourcc']!r} reported_fps={r['reported_fps']} measured_fps={r['measured_fps']} read() mean={r['read_block']['mean_ms']:.1f} ms p95={r['read_block']['p95_ms']:.1f} ms")
                if mi == 0:
                    print(f"    (CAP_PROP_BUFFERSIZE set->1 accepted: {r['buffersize_set_ok']}, reads back {r['buffersize_get']})")
                cap.release()
                if frames and len(frames) >= 2 and (best_frames is None or tuple(r["actual"]) == (1280, 720)):
                    best_frames = frames[-2:]
            except Exception as e:
                record_error(f"camera_{name}_{key}", e)
                try:
                    cap.release()
                except Exception:
                    pass
            res["modes"][key] = r
            sys.stdout.flush()
        RESULTS["camera"][name] = res
    return best_frames


def synthetic_pair(w=1280, h=720):
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, size=(h + 16, w + 16, 3), dtype=np.uint8)
    import cv2
    base = cv2.GaussianBlur(base, (0, 0), 3)
    a = base[0:h, 0:w].copy()
    b = base[4:h + 4, 6:w + 6].copy()
    return [a, b]


# ----------------------------------------------------------------------------- C
def fft_test(iters):
    section("C. NumPy FFT (2048-sample window, float32)")
    x = np.random.default_rng(1).standard_normal(2048).astype(np.float32)
    win = np.hanning(2048).astype(np.float32)
    r = bench(lambda: np.abs(np.fft.rfft(x * win)), max(iters * 10, 300))
    RESULTS["fft"]["rfft_2048"] = r
    print(f"  rfft(2048): mean={r['mean_ms']*1000:.1f} us  p95={r['p95_ms']*1000:.1f} us")


# ----------------------------------------------------------------------------- D
def optical_flow_tests(frames, iters):
    section("D. Dense optical flow (CPU)")
    import cv2
    a_full, b_full = frames
    g_a = cv2.cvtColor(a_full, cv2.COLOR_BGR2GRAY)
    g_b = cv2.cvtColor(b_full, cv2.COLOR_BGR2GRAY)
    presets = [
        ("DIS_ULTRAFAST", cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST),
        ("DIS_FAST", cv2.DISOPTICAL_FLOW_PRESET_FAST),
        ("DIS_MEDIUM", cv2.DISOPTICAL_FLOW_PRESET_MEDIUM),
    ]
    sizes = [(320, 180), (640, 360), (1280, 720)]
    for (w, h) in sizes:
        pa = cv2.resize(g_a, (w, h), interpolation=cv2.INTER_AREA)
        pb = cv2.resize(g_b, (w, h), interpolation=cv2.INTER_AREA)
        for name, preset in presets:
            dis = cv2.DISOpticalFlow_create(preset)
            try:
                r = bench(lambda: dis.calc(pa, pb, None), iters)
                RESULTS["optical_flow"][f"{name}_{w}x{h}"] = r
                print(f"  {name:>14} {w}x{h}: mean={r['mean_ms']:.2f} ms  p95={r['p95_ms']:.2f} ms")
            except Exception as e:
                record_error(f"flow_{name}_{w}x{h}", e)
        if (w, h) != (1280, 720):
            try:
                r = bench(lambda: cv2.calcOpticalFlowFarneback(pa, pb, None, 0.5, 3, 15, 3, 5, 1.2, 0), iters)
                RESULTS["optical_flow"][f"FARNEBACK_{w}x{h}"] = r
                print(f"  {'FARNEBACK':>14} {w}x{h}: mean={r['mean_ms']:.2f} ms  p95={r['p95_ms']:.2f} ms")
            except Exception as e:
                record_error(f"flow_FARNEBACK_{w}x{h}", e)
    r = bench(lambda: cv2.resize(cv2.cvtColor(a_full, cv2.COLOR_BGR2GRAY), (320, 180), interpolation=cv2.INTER_AREA), iters)
    RESULTS["optical_flow"]["preproc_gray_resize_320x180"] = r
    print(f"  gray+resize 720p->320x180: mean={r['mean_ms']:.2f} ms")


# ----------------------------------------------------------------------------- E
def _mp_image(bgr, size=None):
    import cv2
    import mediapipe as mp
    img = bgr if size is None else cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


def segmentation_tests(frames, iters):
    section("E. MediaPipe Image Segmenter (CPU, VIDEO mode) at several INPUT sizes")
    import cv2
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    frame = frames[-1]
    sizes = [("720p", None), ("640x360", (640, 360)), ("320x180", (320, 180)), ("256x256", (256, 256))]
    models = [
        ("selfie_square", "selfie_segmenter.tflite"),
        ("selfie_landscape", "selfie_segmenter_landscape.tflite"),
        ("selfie_multiclass", "selfie_multiclass_256x256.tflite"),
    ]
    for name, fname in models:
        path = os.path.join(MODELS, fname)
        if not os.path.exists(path):
            record_error(f"seg_{name}", FileNotFoundError(path))
            continue
        for mode_name, want_conf in [("category", False), ("confidence", True)]:
            for size_name, size in sizes:
                if name == "selfie_multiclass" and size_name not in ("720p", "256x256"):
                    continue
                if want_conf and size_name not in ("320x180", "256x256"):
                    continue
                key = f"{name}_{mode_name}_{size_name}"
                try:
                    opts = vision.ImageSegmenterOptions(
                        base_options=mp_python.BaseOptions(model_asset_path=path),
                        running_mode=vision.RunningMode.VIDEO,
                        output_category_mask=not want_conf,
                        output_confidence_masks=want_conf,
                    )
                    seg = vision.ImageSegmenter.create_from_options(opts)
                    mp_img = _mp_image(frame, size)
                    ts = [0]

                    def run():
                        ts[0] += 33
                        res = seg.segment_for_video(mp_img, ts[0])
                        if want_conf:
                            return res.confidence_masks[0].numpy_view()
                        return res.category_mask.numpy_view()

                    r = bench(run, iters)
                    mask = run()
                    r["mask_shape"] = list(mask.shape)
                    r["mask_dtype"] = str(mask.dtype)
                    RESULTS["segmentation"][key] = r
                    print(f"  {key:>36}: mean={r['mean_ms']:.2f} ms  p95={r['p95_ms']:.2f} ms  mask={r['mask_shape']} {r['mask_dtype']}")
                    seg.close()
                except Exception as e:
                    record_error(f"seg_{key}", e)
                sys.stdout.flush()

    r = bench(lambda: _mp_image(frame, (320, 180)), iters)
    RESULTS["segmentation"]["preproc_resize_bgr2rgb_wrap_320x180"] = r
    print(f"  resize 720p->320x180 + BGR->RGB + mp.Image wrap: mean={r['mean_ms']:.2f} ms")


# ----------------------------------------------------------------------------- F
def pose_tests(frames, iters):
    section("F. MediaPipe Pose Landmarker (CPU, VIDEO mode)")
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    frame = frames[-1]
    sizes = [("720p", None), ("640x360", (640, 360))]
    for name, fname in [("pose_lite", "pose_landmarker_lite.task"), ("pose_full", "pose_landmarker_full.task")]:
        path = os.path.join(MODELS, fname)
        if not os.path.exists(path):
            record_error(f"pose_{name}", FileNotFoundError(path))
            continue
        for size_name, size in sizes:
            for with_mask in (False, True):
                if with_mask and size_name == "720p" and name == "pose_full":
                    pass
                key = f"{name}_{size_name}{'_mask' if with_mask else ''}"
                try:
                    opts = vision.PoseLandmarkerOptions(
                        base_options=mp_python.BaseOptions(model_asset_path=path),
                        running_mode=vision.RunningMode.VIDEO,
                        output_segmentation_masks=with_mask,
                        num_poses=1,
                    )
                    lm = vision.PoseLandmarker.create_from_options(opts)
                    mp_img = _mp_image(frame, size)
                    ts = [0]

                    def run():
                        ts[0] += 33
                        return lm.detect_for_video(mp_img, ts[0])

                    r = bench(run, iters)
                    res = run()
                    r["poses_detected"] = len(res.pose_landmarks)
                    r["landmarks_per_pose"] = len(res.pose_landmarks[0]) if res.pose_landmarks else 0
                    if with_mask and res.segmentation_masks:
                        m = res.segmentation_masks[0].numpy_view()
                        r["mask_shape"] = list(m.shape)
                        r["mask_dtype"] = str(m.dtype)
                    RESULTS["pose"][key] = r
                    print(f"  {key:>26}: mean={r['mean_ms']:.2f} ms  p95={r['p95_ms']:.2f} ms  poses={r['poses_detected']} landmarks={r['landmarks_per_pose']}" + (f" mask={r.get('mask_shape')}" if with_mask else ""))
                    lm.close()
                except Exception as e:
                    record_error(f"pose_{key}", e)
                sys.stdout.flush()


# ----------------------------------------------------------------------------- G
def gl_tests(frames, iters):
    section("G. moderngl (standalone context)")
    import cv2
    import moderngl
    ctx = moderngl.create_standalone_context()
    info = {
        "GL_VENDOR": ctx.info.get("GL_VENDOR"),
        "GL_RENDERER": ctx.info.get("GL_RENDERER"),
        "GL_VERSION": ctx.info.get("GL_VERSION"),
        "version_code": ctx.version_code,
        "max_texture_size": ctx.info.get("GL_MAX_TEXTURE_SIZE"),
    }
    for k, v in info.items():
        print(f"  {k}: {v}")
    RESULTS["gl"]["info"] = info

    frame = np.ascontiguousarray(frames[-1])
    h, w = frame.shape[:2]
    tex3 = ctx.texture((w, h), 3)
    tex4 = ctx.texture((w, h), 4)
    rgb_bytes = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).tobytes()
    rgba_bytes = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA).tobytes()

    def up_rgb_bytes():
        tex3.write(rgb_bytes); ctx.finish()

    def up_rgba_bytes():
        tex4.write(rgba_bytes); ctx.finish()

    def up_bgr_direct():
        # zero-conversion: upload the OpenCV BGR array as-is, swizzle .bgr in the shader
        tex3.write(frame); ctx.finish()

    def up_cv_cvt_rgb():
        tex3.write(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)); ctx.finish()

    def up_cv_cvt_rgba():
        tex4.write(cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)); ctx.finish()

    def up_numpy_flip():
        tex3.write(np.ascontiguousarray(frame[:, :, ::-1]).tobytes()); ctx.finish()

    for label, fn in [("write RGB from prebuilt bytes", up_rgb_bytes),
                      ("write RGBA from prebuilt bytes", up_rgba_bytes),
                      ("write BGR numpy array directly (no conversion)", up_bgr_direct),
                      ("cv2.cvtColor BGR2RGB + write", up_cv_cvt_rgb),
                      ("cv2.cvtColor BGR2RGBA + write", up_cv_cvt_rgba),
                      ("numpy [:, :, ::-1] + ascontiguous + tobytes + write", up_numpy_flip)]:
        r = bench(fn, iters)
        RESULTS["gl"][f"upload_{w}x{h}: {label}"] = r
        print(f"  {label:>52} {w}x{h}: mean={r['mean_ms']:.2f} ms p95={r['p95_ms']:.2f} ms")

    vs = """
    #version 330
    in vec2 in_pos; out vec2 uv;
    void main(){ uv = in_pos*0.5+0.5; gl_Position = vec4(in_pos,0.0,1.0); }
    """
    fs = """
    #version 330
    uniform sampler2D cam; uniform sampler2D prev; uniform float t;
    in vec2 uv; out vec4 f;
    void main(){
        vec2 c = uv - 0.5;
        float r = length(c); float a = atan(c.y, c.x);
        a = mod(a + t*0.1, 6.2831853/6.0);  // kaleidoscope fold
        vec2 k = vec2(cos(a), sin(a))*r + 0.5;
        vec3 camc = texture(cam, k).bgr;
        vec3 pr = texture(prev, (uv-0.5)*0.995 + 0.5 + 0.002*vec2(sin(t), cos(t))).rgb;
        vec3 col = mix(camc, pr, 0.9);
        col.r = texture(prev, uv + vec2(0.003,0.0)).r*0.5 + col.r*0.5;
        f = vec4(col, 1.0);
    }
    """
    prog = ctx.program(vertex_shader=vs, fragment_shader=fs)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    vao = ctx.vertex_array(prog, [(quad, "2f", "in_pos")])
    prog["cam"].value = 0
    prog["prev"].value = 1
    for (W, H) in [(1920, 1080), (2560, 1440)]:
        fbos = [ctx.framebuffer(color_attachments=[ctx.texture((W, H), 4)]) for _ in range(2)]
        state = {"i": 0, "t": 0.0}

        def passes(n=8):
            for _ in range(n):
                src = fbos[state["i"] % 2]
                dst = fbos[(state["i"] + 1) % 2]
                dst.use()
                tex3.use(0)
                src.color_attachments[0].use(1)
                state["t"] += 0.016
                prog["t"].value = state["t"]
                vao.render(moderngl.TRIANGLE_STRIP)
                state["i"] += 1
            ctx.finish()

        r = bench(passes, iters)
        RESULTS["gl"][f"8_fullscreen_passes_{W}x{H}"] = r
        print(f"  8 fullscreen feedback passes @{W}x{H} (+finish): mean={r['mean_ms']:.2f} ms p95={r['p95_ms']:.2f} ms")

        def readback():
            fbos[0].read(components=3)

        r = bench(readback, iters)
        RESULTS["gl"][f"fbo_read_rgb_{W}x{H}"] = r
        print(f"  FBO readback RGB {W}x{H}: mean={r['mean_ms']:.2f} ms p95={r['p95_ms']:.2f} ms")
        for f_ in fbos:
            f_.release()
    ctx.release()


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-camera", action="store_true")
    ap.add_argument("--frame", default=None, help="use this saved image (e.g. from cam_probe.py) instead of live capture")
    ap.add_argument("--skip-msmf", action="store_true")
    ap.add_argument("--iters", type=int, default=30)
    ap.add_argument("--json", default=os.path.join(HERE, "bench_results.json"))
    args = ap.parse_args()

    try:
        env_info()
    except Exception as e:
        record_error("env", e)

    frames = None
    if args.frame:
        import cv2
        img = cv2.imread(args.frame)
        if img is not None:
            if img.shape[:2] != (720, 1280):
                img = cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA)
            # second frame: the same image shifted a few pixels, so optical flow has real motion to solve
            shifted = np.roll(np.roll(img, 4, axis=0), 6, axis=1)
            frames = [shifted, img]
            RESULTS["camera"]["note"] = f"frames loaded from {args.frame}"
            print(f"\n  (using saved real frame {args.frame} {img.shape} for CV/GL tests)")
    elif not args.no_camera:
        try:
            frames = camera_tests(args.iters, skip_msmf=args.skip_msmf)
        except Exception as e:
            record_error("camera", e)
    if frames is None or len(frames) < 2:
        print("\n  (no camera frames; using synthetic 1280x720 frames for CV/GL tests)")
        frames = synthetic_pair()
        RESULTS["camera"]["note"] = "synthetic frames used for CV tests"
    else:
        import cv2
        cv2.imwrite(os.path.join(HERE, "last_camera_frame.jpg"), frames[-1])
        print(f"\n  (using real camera frames {frames[-1].shape} for CV/GL tests; saved bench/last_camera_frame.jpg)")

    for name, fn in [("fft", lambda: fft_test(args.iters)),
                     ("optical_flow", lambda: optical_flow_tests(frames, args.iters)),
                     ("segmentation", lambda: segmentation_tests(frames, args.iters)),
                     ("pose", lambda: pose_tests(frames, args.iters)),
                     ("gl", lambda: gl_tests(frames, args.iters))]:
        try:
            fn()
        except Exception as e:
            record_error(name, e)
            traceback.print_exc()

    with open(args.json, "w") as f:
        json.dump(RESULTS, f, indent=2)
    section(f"Done. Results written to {args.json}")
    if RESULTS["errors"]:
        print("Errors:")
        for k, v in RESULTS["errors"].items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
