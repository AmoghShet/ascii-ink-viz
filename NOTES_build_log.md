# AudioViz build log (overnight build of plan `robust-dancing-wren`)

Plan: `C:\Users\Asus\.claude\plans\robust-dancing-wren.md` ("Ink smoke in ASCII" generative visualizer).
Goal set 2026-10-13 ~01:28: implement the whole plan; keep this timestamped log as the checkpoint.
User instruction: building overnight; if the camera returns black it is probably lights off, so build around it, no waiting.

How to orient after a reset: read "Status board" first, then the newest log entries at the bottom.

## Status board

| Milestone | State | Notes |
|---|---|---|
| (all re-verified on final code at 02:39) | | |
| M0 Environment | DONE 01:35 | 4.1 core on GTX 1650 Ti, point sizes 1..2047 |
| M1 Particles | DONE 02:05 | tests/verify.py m1 PASS, GPU 2.7-4.6 ms @1M |
| M2 Smoke look | DONE 02:07, look pass 02:22 | verify m2 PASS; divergence-free jets fixed blobs |
| M3 ASCII | DONE 02:06 | orientation 100%, P6 0.36 ms |
| M4 Audio | DONE 02:16 (sync by ear = user) | 0 underflows full song, GPU 5.1 ms, mapping checks PASS |
| M5 Determinism + export | DONE 02:17 | md5 identical x3, 94.5 fps 1080p60, A/V 20.00/20.01 s |
| M6 Mac validation | DONE 2026-10-13 20:20 on MacBook Neo (A18 Pro), with MacPatch | 6 fixes kept as a patch (not merged): macpatch/MACPATCH.md; M1-class Mac not run |
| M7 Camera stir | DONE 02:16 (look = user) | plumbing PASS on C270 + synthetic; stir verified headless |

## Facts worth keeping

- Python: `D:/AudioViz/.venv/Scripts/python.exe` (3.12.15). The venv has no pip; install with `python -m uv pip install --python .venv/Scripts/python.exe <pkgs>`.
- Installed for M0 at 01:31: moderngl-window 3.1.1, glfw 2.10.2, soundfile 0.14.0, librosa 1.0.0, scipy 1.18.1 (numba 0.68, pyglet 2.1.19 came along).
- Test songs (user's own files, referenced in place, never copied): `D:/Downloads/Myxomatosis.mp3`, `D:/Downloads/Sit Down. Stand Up.mp3`, `D:/Downloads/Backdrifts.mp3`, `D:/AMOGH/Give Up The Ghost.mp3`.
- The three reference stills were shared in chat earlier, they are not on disk. Judge the look against the plan's description.
- Webcam: Logitech C270. Capture only through ffmpeg MJPEG (`bench/cam_ffmpeg.py`). Never 1280x720 without MJPEG (wedges the camera until replug).

## Log

### 2026-10-13 01:28 start
- Read plan, research doc (section 9), `bench/ascii_shader_demo.py`, `bench/cam_ffmpeg.py`. `audioviz/` package does not exist yet.
- 01:31 installed M0 packages (above).

### 2026-10-13 01:40 M0 done, pipeline skeleton up
- M0 PASS: `python -m audioviz.gl.context` -> NVIDIA GTX 1650 Ti, GL 4.1.0, PROGRAM_POINT_SIZE ok, point size range 1..2047, buffer 800x450 for a 640x360 window (125% DPI scaling: always use buffer_size).
- Wrote: gl/{context,uniforms,passes,pingpong,timing}.py, shaders (common, flow, particles, splat, trails, bloom_*, grade, ascii, present, hud, meter), sim/{flowfield,particles,trails}.py, render/{bloom,grade,ascii,present,meter}.py, audio/{analysis,player,features}.py, seed.py, preset.py, config.py, engine.py, presets/ink_cold.json.
- UBO: one table in gl/uniforms.py generates both the numpy dtype and the GLSL block (`#include "frame_ubo"`), all vec4 fields, binding set from Python (4.1 has no layout(binding)).
- Analysis: librosa.stft was 9 s and HPSS 45 s on a 232 s song; replaced with a scipy rfft STFT (0.3 s) and HPSS on a 4x frequency-pooled spectrogram (7 s). Whole analysis 24 s, cached in `.cache/features/<sha1>_v5.npz`.
- Kick detection on the low-band percussive flux: 781 events on Myxomatosis (it has a busy fuzz bass); only kicks with strength >= 0.3 make bursts.

### 2026-10-13 01:52 first look tuning (headless, `out/dev/tune.py` + JSON override files)
- First render: everything saturated (density max 42), fat glowing tubes. Fixes: splat 0.03 -> 0.004, stronger mid/high curl octaves (weights 1.0/1.2/0.6, scales 1.1/3.0/8.0), faster time rates, thinner emitters (radius 0.009), shorter trails (decay range 0.93..0.97).
- DEVIATION (plan did not have it): slow auto-exposure. Absolute density depends on seed/emitter count/tier, so a 64x36 meter of the density buffer is read back through 2 PBOs (no stall) and the Director adapts an exposure multiplier (log domain, tau 2.5 s) so the covered-area mean lands at tone 0.32. Audio exposure mappings multiply on top. `tone.auto_exposure.enabled` turns it off.
- DEVIATION: glyph font default is the system monospace TTF (Consolas on Windows, Menlo on macOS) via Pillow; Hershey is the fallback and still selectable (`glyphs.font`). At 6x12 Consolas reads as characters, Hershey reads as blobs (compare `out/atlas/`).
- DEVIATION: sub bass "curl base scale" is done as a crossfade between octave 1 at scale s1 and a copy at 1.6*s1 (UBO simH), because rescaling the noise domain slides the whole pattern.
- Full-res ASCII crop (`out/dev/tune_d5_crop.png`) reads as smoke: faint oriented wisps (`/ | - ~ \`), coarse bright clusters.

### 2026-10-13 02:07 M1-M3 verified headless; live window works
- Live: `python -m audioviz play SONG --mute` (mute = audio clock runs, silence out; used for every overnight test so nothing plays out loud at night). `--auto-keys "6:S,8:A,..."` presses keys unattended; all 14 key actions tested (screenshot, view cycle, grid, bloom, seek, offset).
- Startup hitch fixed: glyph atlases for every grid pair are built in prewarm (a build is 50-100 ms; one at a section change dropped 4 steps). Audio latency hint 'high' (clock compensates via DAC timestamps). Result: 0 dropped steps over 25 s; the only underflow is callback 0 (stream priming), reported separately as `underflows_after_start`.
- HUD atlas keeps the font baseline (ink-centred glyphs made '.' and '_' unreadable in text).
- `tests/verify.py m1|m2|m3|dummy` -> `out/verify/report_*.json` + images:
  - M1 PASS: seeds 1/2 differ (layout, palette glacier/steel, glyph set classic/sharp); no collapse over 60 s (occupied cells 0.39-0.65, top-1% mass 0.18-0.51); GPU 3.2-4.6 ms at 1M @1080p. `python -m audioviz bench`: high 2.66 ms, mid 2.76, low 2.03 GPU (out/bench_1920x1080.json).
  - M2 PASS: density max bounded over 60 s (peaks 1.2-7.9, no growth); GPU 3.8 ms (<7); portrait 1080x1920 not stretched (longitudinal flow correlation x/y ratio mean 0.97 over 8 seeds vs 1.00 landscape; a stretch would show 1.78).
  - M3 PASS: synthetic uniform flow at 0/45/90/135 deg -> 100% of unambiguous glyph votes land in the expected atlas row (- / | \); P6 0.36 ms on real smoke (0.98 ms worst case with every cell lit).
  - Apple dummy-attribute splat path draws pixel-identical output (bug found: `a_dummy*0.0` got constant-folded so the attribute vanished -> KeyError; now `v_w *= 1.0 + a_dummy`).
- Not checkable here: "screenshot beside the three references" (references not on disk); judged against the plan's description instead.

### 2026-10-13 02:16 M4 verified (except sync-by-ear), M7 plumbing verified
- Full-song live run, muted, "Sit Down. Stand Up." (260 s) at 2098x1180, 1.05M particles (`out/live_stats.json`): underflows after start 0 (one at callback 0 = stream priming), GPU mean 5.09 / p95 5.55 ms (target < 8), frame p50 16.66 ms (vsync 60), 885 kicks, 1346 hats, 9 sections applied. The only dropped steps came from 3 synchronous PNG screenshot writes -> screenshots now encode in a background thread.
- `tests/audio_timeline.py SONG` runs the Director over the whole song on the CPU -> `out/verify/m4_timeline_*.png`, `m4_zoom_*.png`, `report_m4_*.json`. PASS: every strong kick -> burst + radial impulse; hats never burst, every hat reseeds glyph jitter; loudness rise -> more emitters + exposure; biggest fall (outro) -> fewer emitters, slower, heavier vignette. Sub entering at ~109 s lengthens trails (decay 0.955 -> 0.97), bass entering at ~21 s speeds advection.
- Kick strengths are now ranked within the song (raw onset_low peaks were all >= 0.3); only rank >= 0.35 puffs, burst size 0.25..1 x burst_fraction.
- `--click-test` adds the beat pulse as a white flash in P7. Sync by ear needs a human: run `play SONG --click-test` and use , . to set the offset.
- M7 camera (`audioviz/inputs/camera.py`): ffmpeg MJPEG 640x480 on DirectShow; ffmpeg itself mirrors + area-scales to 240x180 gray (CPU path 7 ms -> 1.8 ms). DIS ULTRAFAST flow, masked by an adaptive frame-difference test (DIS extrapolated the blob's motion over flat background: 98% of the frame "active"; now 10-15% for one moving blob, 0% for a still room), temporal smoothing then a noise gate, scaled to camera-heights/s, uploaded RG32F, summed in P0 with `sim.ext_flow_gain` (0.6). Thread cost 3.6-3.9 ms (< 5).
- Webcam facts tonight: first run brightness 1.9 (lights off) -> correctly reported "dark (lights off?)" and zero stir; later runs brightness ~120 (a light came on, or exposure adapted), still scene -> 0% active. 30.2 fps, first frame 3.0-4.6 s. Shutdown was a 3.3 s hard terminate because ffmpeg blocked on a full stdout pipe and never read the 'q'; now stdout is drained during shutdown -> graceful exit in 0.77 s. Camera kept working across 6 opens (no wedge).
- `tests/verify_camera.py` (synthetic blob): stir changes the flow locally (max 0.33, mean 0.05 vs base flow 0.28) and the density; P0 0.47 -> 0.74 ms with the external texture; upload 0.01 ms. PASS.
- `python -m audioviz camera-test [--synthetic]` -> `out/camera/`.

### 2026-10-13 02:17 M5 verified
- `tests/verify_export.py SONG` -> `out/verify/export/report_m5.json`. PASS on all: frame 300 md5 c3cd1c0f... identical across two `export --seed 7` runs AND a re-run from the first run's `*_resolved_params.json`; 1080p60 export 94.5 fps (1.57x real time; pipe writes overlap rendering in a writer thread, 2 PBOs); mp4 video 20.00 s vs audio 20.01 s, 1200 frames; 1080x1920 @ 30 fps export ok (240 frames).
- DEVIATION: the sim always steps at 60 Hz; a 30 fps export runs 2 steps per frame (plan said one step per frame) so 30 and 60 fps exports look alike.
- No ffprobe ships with imageio-ffmpeg; durations are measured by decoding each stream with `ffmpeg -map 0:v|0:a -f null -`.

### 2026-10-13 02:22 look pass with music (out/dev/look*.py, contact sheets out/dev/look*_sheet.png)
- With loud music the frame turned into big solid `@@@@` blobs. Density view showed one saturated disc per emitter. Two causes, both compressive (non-divergence-free) terms in P0:
  1. the Gaussian emitter jet: velocity falls off along the jet, so particles decelerate and pile up at its front forever;
  2. the radial kick impulse (a pure source): every kick pushes particles out to the same ring; on Myxomatosis (2-3 strong kicks/s) they never leave (blob with a hole at the emitter).
- Fix (DEVIATION from "radial impulse at nearest emitter"): the jet now comes from a stream function psi = J*s*tanh(b/s)*exp(-a^2/2L^2) (divergence-free by construction), and a kick adds to that jet's strength for ~150 ms (impulse_gain 0.35) instead of a radial source. Side effect, and the best look change of the night: the return flow rolls plumes into mushroom caps / vortex pairs, much more like ink in water.
- Bloom now lifts cell brightness multiplicatively (r*(1+0.6*bloom)); additive bloom painted glyphs into empty space around clusters.
- Emitter layouts are shifted upstream of the drift (sim.upstream_shift 0.15) so plumes cross the frame.
- Defaults now: life 8.5 s, emitters 2..5, emitter radius 0.012, diffusion 0.006.

### 2026-10-13 02:32 live-feature checks (tests/live_features.py -> out/verify/report_live_features.json)
- PASS reload: a deliberate GLSL error in ascii.frag is reported ("failed, keeping the old program"), the app keeps running, a later valid edit recompiles, and the error clears.
- PASS presets: Ctrl+S wrote user_01.json (test artefact deleted), keys 2/1 switch preset slots, R/[/] reseed.
- Fixed: reseed did a blocking 1 s prewarm whose synchronous meter read held the GIL ~190 ms -> audio underflow. Now the refill is spread over frames (4 extra steps/frame), and all 24 glyph atlases (3 sets x cell sizes) are built at startup (0.5 s; glyph images cached). Reseed + preset switching: 0 dropped steps, 0 underflows.
- Fixed crash: rebuilding size-dependent resources (tier drop, resize) re-attached the old, released 1x1 zero ext-velocity texture. PASS tier (forced --tier-drop-ms 2: high->mid->low->min) and PASS resize (F: 2560x1440 fullscreen and back).
- Known cosmetic: after leaving fullscreen, moderngl-window restores the logical window size (buffer 1679x944 instead of 2098x1180 at 125% DPI). Renders fine.
- PASS portrait live (663x1180 buffer, GPU p95 5.4 ms), PASS camera live (device and synthetic; frame p95 18.4 ms).
- 02:3x user woke briefly, asked for status; E-key export check was interrupted, rerun in the background.

### 2026-10-13 02:39 final sweep: everything re-verified on the final code
- E-key export PASS: full song (4:20, 1080p60, AAC) rendered in the background in 200 s; a second E press while it ran was refused ("export already running"). Sample kept: `out/exports/20261013_022936_seed8.mp4` (+ resolved params); the duplicate test export was deleted.
- Re-ran after the look changes (jets, bloom, defaults): verify m1 / m2 / m3 / dummy all PASS; verify_export PASS (frame 300 md5 68ca9aac... identical across 2 runs and the params re-run; 1080p60 at 91.4 fps; A/V durations equal; portrait 30 fps ok); verify_camera PASS (flow thread 3.5 ms); audio_timeline PASS on both songs (Sit Down. Stand Up. seed 21, Myxomatosis seed 7).
- Fullscreen restore fixed: after leaving fullscreen the window is rescaled until the framebuffer matches the original (2098x1180 again). live_features resize PASS.
- 4K export (3840x2160 @ 30, 6 s) works: glyph cells scale to 12x24, 15.7 fps offline (x264 at 4K is the limit).
- Added `presets/ink_cold_portrait.json` (plan item; meta.aspect 9:16 makes `play` open tall by default; plumes start lower and rise through the frame). `--aspect` now defaults to the preset's aspect.
- README.md written (setup, commands, keys, how it works). docs/MAC_VALIDATION.md has the M6 checklist plus the evidence gathered here: the live window is a 4.1 core forward-compatible context (same profile as macOS), so every live run exercised the Mac API subset.

## Morning hand-off (things only the user can do)
1. Sync by ear: `.venv/Scripts/python.exe -m audioviz play "D:/Downloads/Myxomatosis.mp3" --click-test`, then `,` / `.` until the flash sits on the beat; use that as `--offset-ms`. Output latency measured by the clock: ~170-180 ms on the default device.
2. Judge the look against the three reference stills (not on disk). Fast knobs: `--set tone.auto_exposure.target=0.4` (brighter mids), `--set sim.splat=0.006`, `--set grid.T=[0.7,0,0]` (fewer coarse glyphs), R / [ ] for other seeds, Ctrl+S to keep one.
3. Camera with the lights on: `play SONG --camera` and wave a hand; `--set sim.ext_flow_gain=1.2` for a stronger stir. HUD (H) shows camera status/motion.
4. M6 on a Mac: docs/MAC_VALIDATION.md.

### 2026-10-13 02:40 M6 blocked on hardware
- Looked for a Mac to run M6 on: ListAgents shows one peer session ("Trippy audiovisualizer with camera input", a local Claude Desktop session on this Windows PC); cloud agents run Linux. No macOS machine is reachable from here, so M6 (repeat M1-M5 on an M1-class Mac / MacBook Neo, lock tiers) cannot be run tonight.
- Everything M6 can lean on is in place: Darwin code paths (dummy attribute, PROGRAM_POINT_SIZE, buffer_size everywhere, R16F blending, Menlo, AVFoundation capture, tier detection for M-series / A18), the core-profile evidence, and the step-by-step checklist in docs/MAC_VALIDATION.md, whose steps are the same scripts used for M1-M5 here.

### 2026-10-13 02:49 user awake, lights on: camera tested live with "Everything In Its Right Place.mp3" (the 3:13 file the user named; NOT the 4:11 "(1)" file)
- Finding: inside the live app the camera THREAD was starved to 8.9 fps with 100 ms per flow frame (headless it ran 30 fps / 3.8 ms). The render loop holds the GIL while it waits on the GPU. Fix: capture + DIS flow now run in a separate PROCESS (multiprocessing spawn); flow, the gray frame and stats go through shared memory with a seqlock; graceful stop via an Event (worker sends 'q' to ffmpeg). Live result: 30.2 fps, 4.2 ms flow.
- User requests (new decisions, override the plan's "camera = stir only" and "pure smooth" defaults):
  1. Camera feed shown as a "sea of dim gray" behind the smoke. Done in glyphs (still pure glyphs on black): grade.frag samples the camera (auto-levels p5/p95 from the worker, cover-crop, mirrored) into the cell texture's 4th channel; ascii.frag draws isotropic glyphs in neutral dim gray in fine cells where the smoke is fainter. Preset `tone.camera_bg` {enabled, level 0.33, gamma 1.2, brightness 1.0}; key V toggles; UBO camA/camB.
  2. More digital, less smooth: frame hold. The sim still steps at 60 Hz; the picture updates at `grain.frame_rate` (default 24) and P7 re-presents the held frame in between. Key P cycles 12/24/60, `--look-fps`. Export: `--fps` defaults to the look rate; the sim uses a 60 Hz fixed-step accumulator (2-3 steps per 24 fps frame, deterministic); a look rate below the file rate repeats frames.

### 2026-10-13 02:54 cohesion pass (user: "biggest concern is cohesion; technically it works phenomenally, now it's artistic")
- User feedback on the first background version: feed too clear, two layers look too different, wants more contrast and more smoke to marry them.
- Changes: shared `cam_level()` in common.glsl (auto-levels, contrast crush `camera_bg.contrast` 0.55 drops shadows to black, mip blur `camera_bg.blur` 1.5, gamma 1.6). Background glyphs now use the flow-oriented atlas rows and the cold palette (`camera_bg.tint` 0.75) instead of neutral gray isotropic glyphs. The feed EMITS smoke: trails.frag adds `camera_bg.smoke` (0.006/step) x cam_level x a sparse drifting noise mask, so lit shapes shed wisps that the flow carries (a uniform injection turned a lit wall into a solid M@M slab). More smoke overall: emitters 3..6, radius 0.016, life 10 s, diffusion 0.008, auto-exposure target 0.36.
- Result frame: out/dev/camera_bg_device.png (lit wall reads as / \ ~ - glyph texture in the smoke's language).
- Open: a 640 ms hitch ~6 s into a live run with --camera (35 dropped steps once); not yet traced.

### 2026-10-13 03:00 camera AS smoke (user: "whitest spots of the smoke don't match the whitest spots of the image -> one layer over the other"; "too much smoke emitting from me"; "make the camera feed more smoke-like"; "a tad more clear")
- Replaced the separate glyph layer + additive emission with one field: trails.frag pulls the density UP toward the camera image each step (relax, never down, so particle smoke is untouched). The image is sampled upstream along the flow (warp) and broken up by 2-octave drifting noise (breakup); whatever the flow carries off decays like any trail. Same tone curve, bloom, coarse highlights, auto-exposure for both -> the image's brightest areas ARE the brightest smoke.
- Preset `tone.camera_bg`: gain 0.18 (0.45 saturated the lit wall into a solid slab; sheet out/dev/camgain_sheet.png compares 0.1/0.16/0.24), relax 0.12, breakup 0.5, warp 0.06, contrast 0.35, blur 0.7, gamma 1.3; the old glyph layer is kept but off (level 0). UBO camD.

### 2026-10-13 03:02 camera ghosts (user: "too smoky; I meant it leaves ghost-like fading trails for a few seconds on change")
- Image is clear again (breakup 0, warp 0, relax 0.35). New ghost buffer (sim/trails.py Ghost, shaders/ghost.frag, R16F at 1/4 res): G = max(advect(G, flow * ghost_drift) * 0.5^(dt/ghost_s), cam_level(now)). Peak-hold with a slow fade: moving bright parts leave trails that fade over a few seconds and drift with the smoke; a still scene stays clear. The trail density is pulled up toward gain * G (same field, same tone curve as the smoke). Preset: ghost_s 1.5, ghost_drift 0.35. UBO camE.

### 2026-10-13 03:06 gray ghosts + clearer feed (user: "ghosting too pronounced; trails more gray; feed more clear") [session was reset/compacted here; oriented from this log]
- Ghost buffer is RG16F now: R = live camera level, G = fading peak-hold. Trails pull toward max(live, ghost_gray * hold): live image bright, trails a dimmer gray. ghost_s 1.5 -> 0.9, ghost_gray 0.45.
- ffmpeg delivers 480x360 gray (BG_SIZE) for the image; the worker area-downsizes 2x to 240x180 for the flow.
- Clarity: blur 0.7 -> 0.3, contrast 0.35 -> 0.2, gamma 1.1, relax 0.6; gain 0.14 (0.22 blew the monitor-lit silhouette out to M@M). Cells where the camera is most of the density raise the coarse threshold by fine_bias (0.25) so the feed stays in fine glyphs; grade writes the camera's density share into the cell texture's 4th channel (layer mode only when camera_bg.level > 0).
- Auto-levels may stretch at most ~4x (white point >= black + 0.25): in a near-dark room the p5/p95 range was 1..24 and the stretch turned sensor noise into white smoke.
- 03:05 room went dark (brightness 2-7) during these tests; clarity could not be judged on the real feed, only that dark scenes no longer blow out.

### 2026-10-13 03:11 lightless verification of the camera-as-smoke changes + monitor-light tuning
- IMPORTANT user fact: the user tests with the room lights OFF and only the monitor lighting their face (camera brightness ~4-10 of 255). That is the normal test condition, not "camera broken".
- New `tests/verify_camera_smoke.py` feeds a synthetic test image straight into the GPU pipeline (no light needed) -> out/verify/report_camera_smoke.json, camsmoke_*.png. All PASS: clarity correlation 0.913 (old settings) -> 0.965 (new); coarse-glyph share inside the camera image 0.02 -> 0.003 (fine_bias); gray trails: after the shape moves away, trail target / old live = 0.37 at 0.25 s, 0.21 at 0.9 s, 0.11 at 1.8 s (<= ghost_gray 0.45, halves every 0.9 s); pure dark noise frame -> camera level mean 0.004; monitor-lit face (values ~20/255) -> face level 1.0, surround median 0.0.
- The 03:05 "max 4x stretch" rule would have erased a monitor-lit face; replaced by MIN_LEVEL_RANGE 0.06 (up to ~16x) + temporal smoothing of the drawn image in the camera process (BG_SMOOTH 0.5) so stretched noise stays quiet.
- Live 03:10 (monitor light, brightness 9.8): camera 30 fps, flow 3.8 ms, GPU 5.6 ms; the feed reads in fine glyphs inside the smoke. A startup spike triggered an auto tier drop to mid; the tier controller now ignores the first 5 s.
- User's PowerShell got "No Python at '\"C:\...cpython-3.12.15...'" running .venv/Scripts/python.exe; the same command works from this session's PowerShell and bash, so it is something in that terminal's environment. Launches are done from this session for now.

### 2026-10-13 03:14 near-black haze + camera priming fix
- User: "add some near black but not black chars to all the empty black spots, as other smoke, for more texture". Grade writes a negative ramp value (-h) into cells the smoke and camera leave empty (r < 0.035), from a slow two-octave noise field drifting with the drift vector, thinned per cell by a hash (coverage). ASCII draws those as columns 1..3 of the flow-oriented rows in cold near-black (linear brightness 0.022). Preset `grain.haze` {enabled, coverage 0.6, brightness 0.022, scale 2.2, speed 0.05}. UBO hazeA. Frame: out/dev/haze_check.png.
- Startup hitch with --camera (555 ms once the first frames arrived): camera textures are now created blank and bound before prewarm (CameraStir.prime + App._prime_camera) -> 131 ms. While doing this I introduced a bug (self.dt used before it was set; the exception was swallowed, the CameraStir was dropped, its shared memory freed, and the worker crashed with FileNotFoundError on the shm name); fixed, and camera start errors now print a traceback.

### 2026-10-13 03:16 user: "near perfect; make everything 12 FPS; add more texture"
- grain.frame_rate 24 -> 12 (live picture and export default; P still cycles 12/24/60). More texture: haze coverage 0.6 -> 0.85 and brightness 0.022 -> 0.03, tone.ramp_jitter 0.4 -> 0.55, grain 0.035 -> 0.06. Still: out/dev/texture_check.png. Launched live for the user (song + camera, monitor light).

### 2026-10-13 03:19 clearer camera feed (user: "near perfect; can we make my camera feed clearer?")
- Cause: the texture pass (ramp jitter 0.55, flow-oriented rows) also scrambled the face. Now cells where the camera is >50% of the density use the isotropic tone ramp and 15% of the jitter (ascii.frag, camShare from the cell texture's 4th channel); smoke and haze keep their texture. Camera process applies an unsharp mask (BG_SHARPEN 0.8) to the drawn image; camera_bg.blur 0.3 -> 0, contrast 0.2 -> 0.25.
- tests/verify_camera_smoke.py now also measures what the viewer sees: glyph-level clarity (mean drawn brightness per fine cell vs the camera image, 3x3 neighbourhood): 0.898 (previous render) -> 0.941. All camera checks PASS.
- Last session had 2 long stalls (1.46 s at 19 s, 0.73 s at 27 s) with no export/screenshot/preset files written; most likely window dragging (Windows freezes the GLFW loop during a move). --verbose now logs every key action to attribute future stalls.

### 2026-10-13 03:23 saved to GitHub; M6 deferred by the user
- User: "leave M6 for now. now save everything to my github" (declined running M6 on a GitHub Actions macos-14 VM runner).
- Private repo https://github.com/AmoghShet/audioviz (main, commit f5471cc): code, shaders, presets, tests, docs (incl. docs/PLAN.md copy of the plan), README, this log, research doc, bench scripts + JSON results. Excluded by .gitignore: .venv*, .cache/, out/ (renders and screenshots with the user's camera feed), bench/*.jpg|png (webcam stills and ASCII renders of them), models/ (MediaPipe, unused), all audio files.
- Status: M0-M5, M7 DONE and verified on Windows; M6 DEFERRED (no Mac reachable; checklist docs/MAC_VALIDATION.md).

### 2026-10-13 20:20 M6 on the MacBook Neo (A18 Pro, 8 GB, macOS 27.0.1); fixes kept as a patch
- Venv from Homebrew python3.12 (3.12.10); every pinned requirement installed from arm64 wheels. Test song: "Everything In Its Right Place.mp3" (193.7 s, 120.2 BPM; analysis 28.7 s).
- M0 PASS: `4.1 Metal - 91.7`, renderer `Apple A18 Pro`, point sizes 1..64, buffer 2x window; headless context identical. `detect_tier` -> `low`.
- Six fixes were needed. They are NOT merged: the user wants the Windows code unchanged, so they live in `macpatch/mac.patch` (apply with `macpatch/apply.sh`; details in `macpatch/MACPATCH.md`):
  1. `noise3` clashes with the GLSL 4.1 built-in `vec3 noise3(vec3)`. NVIDIA allowed it; Apple rejects it, so no shader compiled. Renamed to `gnoise3`.
  2. `window_size_for` divided the macOS video mode (already in points) by the content scale: 577x324 pt window. Skipped on darwin -> 1154x649 pt, 2308x1298 buffer.
  3. Tier controller false drops: Apple GPUs clock down when there is slack, so GPU ms fills the frame (`low`: 11.3 ms at vsync vs 4.6 ms with --no-vsync). It dropped low -> min every run at 60 fps. A drop now also needs mean frame time > 25 ms (`Tunables.tier_drop_frame_ms`, `--tier-drop-frame-ms`). This also changes Windows behaviour slightly.
  4. Camera: the Mac camera has no 640x480 mode (1280x720 and up). Capture 720p and centre-crop to 4:3 in ffmpeg; downstream unchanged.
  5. Camera devices by name on macOS (Continuity iPhone "Big Tim Camera" was index 1); prefer the built-in.
  6. `tests/live_features.py` song from `AUDIOVIZ_TEST_SONG`.
- Patched results: dummy PASS; m1/m2 visual checks PASS (GPU 7.5-10.8 ms at 1M particles vs the 1650 Ti's 6/7 ms thresholds, expected); m3 PASS (orientation 100%, P6 0.48 ms); full song live on `low`: 0 underflows, 0 dropped steps, latency 14 ms; verify_export PASS (md5 c26cf095... x3, 1080p60 at 74.6 fps); camera synthetic + verify_camera + verify_camera_smoke PASS; live --camera works from Terminal.app (user: "looks fine").
- Camera permission: anything launched by the Claude desktop app never got a prompt; ffmpeg opened the camera and waited forever for frames (even outside the sandbox). Terminal.app prompts normally.
- Open (not in the patch): the camera delivers 24 fps but the flow scale assumes 30 (stir about 25% strong); vsync does not fully pace the window (HUD 90-96 fps on 60 Hz); a hung camera leaves an orphaned ffmpeg; GPU timers are noisy (bench one tier per process); 12 fps look render frame slips a vsync (frame p95 27-33 ms).
- Also: the project is now called "ASCII Ink Visualizer" (README, window title, HUD, CLI description); the package and command stay `audioviz`.
- `macpatch/windows.cmd` reverts the MacPatch on Windows (tested with core.autocrlf=true: working-tree and committed patch both restore the pre-patch files exactly). `.gitattributes` pins mac.patch (-text), apply.sh (LF), windows.cmd (CRLF).
