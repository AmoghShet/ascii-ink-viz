# ASCII Ink Visualizer

An ASCII/fluid-based, audio-reactive visualizer. Up to 1M particles move through a curl-noise flow
field on the GPU and leave trails that drift with the same flow. Each frame is drawn as ASCII
characters on black, and the characters follow the flow direction; a sideways stream draws as `-`,
a rising one as `|`. The song is analysed once (bands, kicks, hats, beats, sections) and that
analysis drives the motion. The seed changes the emitter layout, palette and glyph set; the colours
stay black, grey and a little cyan. The webcam is optional: your movement pushes the particles
around and you show up in the frame as characters.

Runs on Windows (OpenGL 4.1 core through moderngl) and on Apple Silicon Macs with the MacPatch
(4.1 core is the macOS ceiling). The Python package is `audioviz`, so commands are
`python -m audioviz ...`.

## Setup

Needs Python 3.12. From the repo folder:

Windows:

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m audioviz check
```

macOS (Apple Silicon):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
macpatch/apply.sh
python -m audioviz check
```

`check` opens a small window and prints the OpenGL version; it should say 4.1 and `M0: PASS`. The
commands below assume the venv is active.

On macOS the code needs six small fixes, kept in `macpatch/` as a patch so the Windows code stays
unchanged; `macpatch/MACPATCH.md` explains each one. Run `--camera` from Terminal.app (or another
terminal that can ask for camera permission). If the patch ever ends up applied on Windows, for
example committed on a Mac and pulled, `macpatch\windows.cmd` takes it back out
(`macpatch\windows.cmd --status` checks first).

## Use

Play a song in a window. The first run analyses the song (about 30 s); later runs load the
result from `.cache/`:

```bash
python -m audioviz play song.mp3
```

Useful options: `--seed N` (default random), `--preset ink_cold_portrait` (tall frames; sets 9:16
on its own), `--aspect 9:16`, `--fullscreen`, `--camera` (webcam
stir) or `--camera synthetic` (a fake moving blob, for testing in the dark), `--click-test`
(flashes on every beat, for setting the audio offset by ear), `--offset-ms 40`, `--tier mid`,
`--debug-view density|flow`, `--look-fps 12|24|60` (picture update rate, default 12), `--mute`
(clock runs, no sound), `--set sim.splat=0.004` (override any preset value). With no song it runs
on a wall clock with neutral audio values.

Export a video (offline and deterministic: the same song, seed and settings give the same
frames). The file frame rate defaults to the look frame rate (12); `--fps 60` keeps the 12 fps look
by repeating frames:

```bash
python -m audioviz export song.mp3 --seed 7 --size 1920x1080 --fps 60 --out out/exports/song_seed7.mp4
python -m audioviz export song.mp3 --seed 7 --size 1080x1920 --fps 30 --out out/exports/song_seed7_portrait.mp4
```

Every export writes `<name>_resolved_params.json` next to the mp4. Re-render exactly the same
frames from it with `export --params that.json --out again.mp4`.

Other commands: `still` (one frame headless, `--t 90 --view ascii`), `analyze` (print song
features), `bench` (per-pass GPU timings per tier), `check` (OpenGL context check),
`camera-test [--synthetic]` (camera plumbing check, writes `out/camera/`).

## Keys (live window)

| Key | Action |
|---|---|
| `R` | new random seed |
| `[` `]` | seed -1 / +1 |
| `Space` | pause |
| `S` | screenshot + sidecar JSON with every resolved value (`out/screens/`; `_hud.png` copy when the HUD is on) |
| `Ctrl+S` | save the current resolved values as a new preset (`audioviz/presets/user_NN.json`) |
| `1`-`9` | preset slots (presets sorted by name; `ink_cold` is 1) |
| `A` | cycle view: ASCII / raw density / flow arrows |
| `B` | bloom on/off |
| `G` | grid overlay (fine cells, coarse cells, cells drawn coarse) |
| `H` | HUD on/off (fps, GPU ms per pass, song time, seed, envelope bars, last change, shader errors) |
| `Left` `Right` | scrub 5 s |
| `,` `.` | audio offset -10 / +10 ms |
| `E` | export this song and seed in the background (`out/exports/`) |
| `C` | camera stir on/off (starts the camera if needed) |
| `V` | camera image in the frame on/off |
| `P` | frame-rate look: 12 / 24 / 60 fps (the simulation stays smooth underneath; the picture is held between updates) |
| `F` | fullscreen |
| `T` | step down one particle tier |
| `Esc` | quit |

Look knobs worth knowing (all `--set`): `grain.haze.brightness` / `coverage` (near-black texture in
the empty areas), `tone.camera_bg.gain` / `ghost_gray` / `ghost_s` / `fine_bias` (how you appear in
the smoke and how your movement trails), `grain.frame_rate` (12/24/60 look).

Shaders hot-reload: edit anything in `audioviz/shaders/` while it runs. A compile error appears in
the HUD in red, and the previous program keeps running.

## How it works (one pass per line)

Per simulation step (fixed 60 Hz, chasing the audio clock):
- **P0 flow**: 3-octave curl noise, drift, divergence-free emitter jets (kicks boost them into
  mushroom-cap puffs), optional camera velocity. 1/4 resolution.
- **P1 particles**: advect (RK2), age, respawn through an emission ring. State is an RGBA32F texture.
- **P3 trails**: semi-Lagrangian advection of the density buffer, plus decay.
- **P2 splat**: one GL point per particle from `gl_VertexID` + `texelFetch`, additive into the density.

Per rendered frame:
- **P4 bloom**: on density.
- **P5 grade**: per-cell tone, flow angle and bloom for a fine and a coarse grid.
- **P6 ASCII**: directional glyph atlas rows (`- / | \`) and nested grids (highlights use the coarse grid).
- **P7 present**: glyph glow and gamma encode.

The song is analysed offline with librosa: bands, onsets, kicks, hats, beats, downbeats, sections.
The `Director` (`audio/features.py`) maps it to the passes with attack/release envelopes; the
mapping table is in that file's docstring. A slow auto-exposure keeps the tone curve in range
whatever the seed. Section boundaries change the emitter layout, palette and grid pair on the next
downbeat.

Randomness: `seed.py` draws, in a fixed order, the noise offset and rotation, drift, emitter count
and layout, flow scale, octave weights, palette, glyph set and grid pair, then every preset value
written `[value, jitter_lo, jitter_hi]`. The invariants (black background, cold palette family,
tone shape) are not randomised.

## Files

- `audioviz/` package: `app.py` (live), `engine.py` (pass order), `gl/`, `sim/`, `render/`,
  `audio/`, `inputs/camera.py`, `export/recorder.py`, `shaders/`, `presets/ink_cold.json`
- `tests/verify.py m1|m2|m3|dummy`, `tests/audio_timeline.py SONG`, `tests/verify_export.py SONG`,
  `tests/verify_camera.py`, `tests/live_features.py`: milestone checks. Reports go to `out/verify/`.
- `NOTES_build_log.md`: timestamped build log, status board, deviations from the plan.
- `docs/MAC_VALIDATION.md`: the M6 checklist for a Mac.
- `macpatch/`: the macOS fixes (`mac.patch`, `apply.sh` for macOS, `windows.cmd` to revert them on
  Windows) and `MACPATCH.md` (what each fix does, Mac results, known issues).
