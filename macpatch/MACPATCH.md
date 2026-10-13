# MacPatch: running ASCII Ink Visualizer on macOS

The committed code was built and verified on Windows (GTX 1650 Ti). On 2026-10-13 the Mac checklist
(`docs/MAC_VALIDATION.md`, milestone M6) was run on a MacBook Neo (Apple A18 Pro, 8 GB, macOS 27.0.1)
and needed six small fixes. They are kept here as a patch rather than merged, so the Windows code
stays exactly as it was verified. Apply the patch on a Mac and everything in the checklist passes.

- `mac.patch`: the exact changes (8 files, about 40 lines)
- `apply.sh` (macOS): applies the patch (`--status` checks it, `--revert` undoes it)
- `windows.cmd` (Windows): takes the patch back out, see "Back to the Windows code" below

## Quick start on a Mac

```bash
cd ascii-ink-viz                                   # your clone of the repo
python3.12 -m venv .venv                           # Python 3.12, as on Windows
.venv/bin/python -m pip install -r requirements.txt
macpatch/apply.sh
.venv/bin/python -m audioviz check                 # expect GL_VERSION 4.1 Metal, M0: PASS
.venv/bin/python tests/verify.py dummy             # expect "pass": true
.venv/bin/python -m audioviz play ~/Downloads/song.mp3
```

All pinned requirements install from macOS arm64 wheels; no other setup is needed. Use
`.venv/bin/python` wherever the main README says `.venv/Scripts/python.exe`.

**Camera (`--camera`, `camera-test`): run it from Terminal.app.** macOS attributes the camera request
to the app that launched Python. Terminal shows the "Terminal would like to access the camera"
prompt. An app that cannot show that prompt (seen with the Claude desktop app, and likely with other
sandboxed launchers) gets no prompt and no frames: ffmpeg opens the camera and then waits forever.

## Back to the Windows code

If the MacPatch was applied (on a Mac, or committed and pulled onto a Windows machine), this
restores the code as verified on Windows. From cmd or PowerShell, in the repo:

```bat
macpatch\windows.cmd --status
macpatch\windows.cmd
```

`--status` prints `applied` or `not applied`. Without options it reverts the patch, and does
nothing if the patch is not applied. If the MacPatch had been committed, the revert shows up as
uncommitted changes; commit them. If the patched lines were edited after applying, it stops
and asks for the changes below to be undone by hand. On a Mac, `macpatch/apply.sh --revert`
does the same.

Tested with Git for Windows line endings (`core.autocrlf=true`): reverting a working-tree patch
and reverting a committed patch both give files identical to the pre-patch commit. `.gitattributes`
keeps `mac.patch` byte-exact, `apply.sh` LF and `windows.cmd` CRLF.

## What the patch changes

| # | Symptom on the Mac | Fix | Files | Effect on Windows |
|---|---|---|---|---|
| 1 | Every shader fails to compile | Rename `noise3` to `gnoise3` | `shaders/common.glsl`, `grade.frag`, `trails.frag` | None (same math, same pixels) |
| 2 | The window opens at about 40% of the screen | macOS sizes the window in points | `app.py` (`window_size_for`) | None (macOS-only path) |
| 3 | Auto tier drop to `min` while running at 60 fps | Drop only when frames are actually late | `app.py`, `config.py`, `__main__.py`, `tests/live_features.py` | Drops become rarer; never fired there in normal runs |
| 4 | Camera: "Selected video size (640x480) is not supported" | Capture 1280x720, centre-crop to 4:3 | `inputs/camera.py` | None (still 640x480 MJPEG) |
| 5 | The camera may open the iPhone (Continuity Camera) | Choose the camera by name, built-in first | `inputs/camera.py` | Still picks the C270 |
| 6 | `tests/live_features.py` hardcodes a `D:` song | `AUDIOVIZ_TEST_SONG` environment variable | `tests/live_features.py` | Same default path |

### 1. GLSL built-in name clash (`noise3`)

```
ERROR: 0:71: Return type in redeclared function 'noise3' differs from previous declaration
```

GLSL 4.1 has a built-in `vec3 noise3(vec3)` (one of the old `noise1`..`noise4` functions).
`common.glsl` declared its own `float noise3(vec3)`. NVIDIA lets it shadow the built-in; Apple's
compiler rejects it, and since every program includes `common.glsl`, nothing compiles. The fix
renames it to `gnoise3` ("gradient noise") in `common.glsl` (the definition and `curl()`),
`grade.frag` (haze) and `trails.frag` (camera breakup). No other shader function name clashes with
a built-in.

### 2. Window size on Retina (`app.py`, `window_size_for`)

The default window is 82% of the monitor. The code divides the monitor's video mode by the content
scale. That is right on Windows, where the video mode is in pixels and moderngl-window's
`SCALE_TO_MONITOR` scales the requested size back up. On macOS the video mode is already in points
(1408x881 on the Neo), so the scale was applied twice: a 577x324-point window with a 1154x648 buffer.
With the fix the window is 1154x649 points and the buffer is 2308x1298. The fix skips the division
when `sys.platform == "darwin"`.

### 3. Tier controller false drops (`app.py`, `_tier_control`)

The controller dropped a tier when the rolling p95 of the summed GPU timers exceeded 14 ms. Apple
Silicon clocks the GPU down whenever it has slack, so measured GPU time grows until it fills the
frame. The same work reads very differently with and without vsync (seed 901942, 2308x1298 buffer):

| Live run | GPU mean / p95 | Frame p50 / p95 | Dropped steps |
|---|---|---|---|
| `low` tier, vsync | 11.3 / 14.3 ms | 16.5 / 27.1 ms | 0 |
| `mid` tier, vsync | 13.5 / 14.1 ms | 16.7 / 33.2 ms | 0 |
| `low` tier, `--no-vsync` | 4.6 / 5.1 ms | 1.2 / 3.9 ms | 0 |

Unpatched, every live run dropped from `low` to `min` within about 30 s while holding 60 fps.

The fix also requires the mean frame time over the same 60 frames to exceed
`Tunables.tier_drop_frame_ms` (25 ms). With vsync at 60 Hz, a real overload makes every frame late
(33 ms or more). The 12 fps look, whose heavy render frame alone can slip one vsync, averages
exactly 20 ms ((4 x 16.7 + 33.3) / 5). That is picture jitter, not overload, and the 25 ms
threshold also leaves room for the 24 fps look (about 23.3 ms). The drop message now includes the
mean frame time and the song time. A new `--tier-drop-frame-ms` flag overrides the threshold
(0 = GPU only). `tests/live_features.py tier` passes `--tier-drop-frame-ms 0` so its forced drop
still fires.

This is the one change that also affects Windows behaviour. There the GPU read about 5 ms live, so
the old rule never fired outside the forced test.

### 4. Camera capture mode (`inputs/camera.py`)

On Windows the module only ever asks for 640x480 MJPEG, because anything else wedges the Logitech
C270. Mac cameras have no 640x480 mode. The MacBook Neo camera offers 1280x720 and up (1920x1080,
1552x1552, 1760x1328, 1328x1760). On macOS `CAP_SIZE` is now 1280x720, and `capture_cmd` adds
`crop=ih*4/3:ih` before the existing `hflip,scale=480:360`. Everything after the pipe (4:3 480x360
image, 240x180 flow) is unchanged. The crop only applies when the capture is wider than 4:3, so the
Windows ffmpeg command is identical. `describe()` reports the real format.

### 5. Camera device by name (`inputs/camera.py`)

On macOS `list_video_devices()` returned bare indices, so the "prefer the webcam" logic could never
match. Continuity Camera iPhones (here `[1] Big Tim Camera`) come and go and can shift the indices.
It now returns names, which ffmpeg's avfoundation input accepts as well. The preference order is a
known webcam or built-in camera (`c270`, `webcam`, `facetime`, `macbook`, `built-in`), then any
`camera`, then the first device. `--camera-device "Big Tim Camera"` selects the iPhone explicitly.

### 6. Test song path (`tests/live_features.py`)

`SONG = os.environ.get("AUDIOVIZ_TEST_SONG", "D:/Downloads/Sit Down. Stand Up.mp3")`. For example:

```bash
AUDIOVIZ_TEST_SONG=~/Downloads/song.mp3 .venv/bin/python tests/live_features.py tier
```

## Results on the MacBook Neo (A18 Pro), patched

Test song: "Everything In Its Right Place" (193.7 s, 120.2 BPM; analysis 28.7 s, then cached).

| Check | Result |
|---|---|
| M0 `check` | PASS. `4.1 Metal - 91.7`, renderer `Apple A18 Pro`, point sizes 1..64, buffer = 2 x window; headless context identical |
| `verify.py dummy` | PASS. The dummy-attribute and empty-VAO splats draw identical, non-blank images |
| `bench` 1920x1080 | `high` 8.6 ms, `mid` 6.6, `low` 5.9, `min` 2.8 GPU (noisy, see below) |
| `verify.py m1` | Seeds differ, no collapse over 60 s. GPU 7.5-10.0 ms is over the 1650 Ti's 6 ms threshold at 1M particles (expected) |
| `verify.py m2` | Density bounded, portrait not stretched. GPU 10.8 ms vs the 1650 Ti's 7 ms (expected) |
| `verify.py m3` | PASS. Orientation 100% in all four directions, ASCII pass 0.48 ms |
| Live, full song, auto tier (`low`) | Stays on `low`. 0 underflows, 0 dropped steps, all 658 kicks / 1040 hats / 9 sections; output latency 14 ms (Windows: 170-180 ms) |
| `verify_export.py` | PASS. Frame-300 md5 identical across 2 runs and the params re-run; 1080p60 at 74.6 fps (1.24x real time, `high` tier, x264-bound); A/V 20.00 / 20.01 s; portrait OK |
| `camera-test --synthetic`, `verify_camera.py`, `verify_camera_smoke.py` | PASS. 30 fps, flow 2.8 ms, clean stop 0.06 s; all six camera-smoke checks |
| Live `--camera` (from Terminal.app) | Works; the camera image reads in fine glyphs inside the smoke |

**Tiers:** `detect_tier` picks `low` for an A18 renderer, and that is right for this machine. `mid` holds
60 fps too, but its render frame slips a vsync more often (frame p95 33 ms vs 27 ms). M-series
Macs (`mid`) were not tested.

## Known issues the patch does not fix

- **Camera runs at 24 fps, not 30.** The flow-to-velocity conversion uses the constant
  `CAP_FPS` (30), so on this camera the stir is about 25% stronger than it was tuned on Windows.
  The fix would be to use the measured frame rate from the worker.
- **Vsync does not fully pace the window.** The HUD showed 90-96 fps on a 60 Hz panel (frame p50 15 ms):
  the Metal-backed GL drawable queue lets frames through early after a late one. The picture is
  fine, but the window presents more often than needed.
- **A hung camera leaves ffmpeg behind.** If ffmpeg never delivers a frame (for example the
  permission problem above), `CameraStir.stop()` terminates the worker process but its ffmpeg
  child keeps the camera open. Kill it with `pkill -f ffmpeg-macos`.
- **GPU timers on macOS are noisy.** Benchmarking several tiers in one process inflated whichever
  tier ran second (`mid` read 17.7 ms in one run and 6.8 ms when run alone). Bench one tier per run
  when the numbers matter.
- **12 fps look jitter.** On `low` and `mid`, frame p95 is 27-33 ms because the render frame
  sometimes slips one vsync. Audio and simulation are unaffected; the picture update lands 16 ms late now and then.

## Updating the patch

If the code changes and the patch no longer applies cleanly, `apply.sh` falls back to a 3-way merge
and stops on conflicts. Each change above is small enough to redo by hand. To regenerate the patch,
start from a checkout with nothing else uncommitted in those files, apply it, make the edits, then
save the diff:

```bash
git diff -- audioviz/__main__.py audioviz/app.py audioviz/config.py audioviz/inputs/camera.py \
  audioviz/shaders/common.glsl audioviz/shaders/grade.frag audioviz/shaders/trails.frag \
  tests/live_features.py > macpatch/mac.patch
```

Six of those files are committed with CRLF line endings (`git ls-files --eol`). Keep them CRLF
when editing, or the patch stops matching.
