# M6: Mac validation checklist

M6 could not be run during the overnight build (no Mac on hand). Everything Mac-specific is already
in the code; this is the list to run on an M1-class Mac and on the MacBook Neo (A18 Pro).

## Setup (once per Mac)

```bash
cd AudioViz
python3.12 -m venv .venv            # or: uv venv --python 3.12
.venv/bin/python -m pip install -r requirements.txt
```

Allow camera access for the terminal app when macOS asks (only needed for M7, `--camera`).

## What the code already does differently on macOS

| Concern | Where | What happens |
|---|---|---|
| 4.1 core profile ceiling | `gl/context.py` | Window asks for 4.1 core (forward-compatible). All shaders are `#version 410 core`, no compute, no SSBOs, no `layout(binding=)`. |
| `gl_PointSize` ignored unless enabled | `gl/context.py:setup_context` | `PROGRAM_POINT_SIZE` is enabled on every context. Point size is clamped to `GL_POINT_SIZE_RANGE`. |
| Empty VAOs draw nothing on some Apple drivers | `config.Tunables.dummy_attrib` (default True on Darwin), `shaders/splat.vert` | The splat binds a 1-float attribute per particle. `tests/verify.py dummy` proves it draws pixel-identically to the empty-VAO path. Fullscreen passes always use a real quad buffer. |
| Blending into 32F is slow | `sim/trails.py` | Density is R16F; only particle state is 32F (written, never blended). |
| Retina | everywhere | `window.buffer_size` is used for every buffer, never `window.size`. (Checked on Windows at 125% scaling: 640x360 window -> 800x450 buffer.) |
| Vendor hash differences | `shaders/common.glsl` | Integer PCG hashes and `texelFetch` for state reads; no `fract(sin())`. |
| Tier | `config.detect_tier` | M-series -> `mid` (724x724 = 524k particles); a renderer name with A16/A17/A18 -> `low` (512x512 = 262k, half-res density). Auto-drop one tier if rolling p95 GPU time > 14 ms for 60 frames. |
| Camera | `inputs/camera.py` | AVFoundation through the bundled ffmpeg, device index from `-list_devices`. |
| Fonts | `render/ascii.py` | Menlo (`/System/Library/Fonts/Menlo.ttc`) instead of Consolas; Hershey fallback if neither exists. |

## Run these, in order, and paste the outputs into `NOTES_build_log.md`

1. **Context** (M0): `python -m audioviz check`
   Expect `GL_VERSION 4.1 ... Metal`, a point size range, `M0: PASS`, and a buffer size twice the window size on Retina.
2. **Points render under core profile** (M1): `python tests/verify.py dummy`
   Expect `identical: true, nonblank: true`. If `nonblank` is false, the point path is broken on this driver.
3. **Per-pass timers**: `python -m audioviz bench --tiers mid,low,min`
   Writes `out/bench_1920x1080.json`. Note the `gpu_total_ms` per tier.
4. **Milestone checks**: `python tests/verify.py m1`, `m2`, `m3`
   M1's GPU threshold is written for the 1650 Ti (6 ms at 1M particles). On a Mac, compare the `mid`/`low` tier numbers against the 14 ms tier-drop threshold instead.
5. **Live** (M4): `python -m audioviz play "song.mp3" --tier mid`
   Press `H` for the HUD. Check fps 60, GPU ms, underflows 0 after start. Press `A` to cycle views and `S` for a screenshot (`out/screens/`).
6. **Export determinism** (M5): `python tests/verify_export.py "song.mp3"`
7. **Lock the tiers**: if the auto-drop fires on a machine, or `bench` shows > 10 ms for the tier `detect_tier` picks, change `detect_tier` in `config.py` so that machine starts one tier lower. Record the renderer string (`GL_RENDERER`) and the chosen tier in the build log.
8. **Camera** (M7): `python -m audioviz camera-test --seconds 10`, then `python -m audioviz play song.mp3 --camera`.

## Known risks to look at first

- If nothing draws in the ASCII view but the density view (`A` key) is also black, the splat path is the suspect (step 2).
- If the window is a quarter of the screen with a tiny image, something used `window.size` instead of `buffer_size` (Retina).
- macOS may report a different `outputBufferDacTime` behaviour; the clock falls back to `stream.latency`. Use `,` and `.` to set the offset by ear with `--click-test`.

## Evidence gathered on Windows (2026-10-13)

- The live window's context is a **4.1 core, forward-compatible** profile (checked with
  `glfw.get_window_attrib`: OPENGL_CORE_PROFILE, FORWARD_COMPAT true, 4.1). That is the profile
  macOS gives, so every live run tonight (full song, hot reload, resize, tier drops, camera) already
  exercised the shaders and API calls with deprecated functionality removed.
- The Darwin-only splat path (dummy vertex attribute) was forced on Windows and draws
  pixel-identical output (`tests/verify.py dummy`).
- Not covered: Metal-backed driver quirks, Retina buffer sizes, A18 Pro performance, AVFoundation
  camera capture. Those need the checklist above on real hardware.
