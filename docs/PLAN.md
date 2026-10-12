# Plan: "Ink smoke in ASCII" generative audio visualizer

## Context

The user wants a visualizer for a dark, electronic track (Atoms for Peace / Thom Yorke territory) that looks like the three reference stills they shared: black background, turbulent ink-in-water filaments with fur-like edges, a few bright cold-white clusters, monochrome with a touch of cyan. The whole frame must be made of ASCII glyphs yet still read as that smoke. Each run should be different ("random") but always inside that look. The song file drives it; the webcam only stirs the smoke (the camera image is never shown). It must run on Windows (dev box: GTX 1650 Ti) and Apple Silicon Macs (M1 and later, MacBook Neo with A18 Pro), and support both 16:9 and 9:16 from day one, plus offline export.

Everything below builds on the measured results in `D:\AudioViz\RESEARCH_technical_feasibility.md` section 9 and the working ASCII pass in `D:\AudioViz\bench\ascii_shader_demo.py`. The hard constraint is macOS OpenGL 4.1 core: no compute shaders, no SSBOs, only vertex/fragment shaders, float textures, additive blending and `gl_VertexID`.

Decisions already made by the user: **both aspect ratios from the start**, **camera = stir only (later phase)**, **pure glyphs (black between characters, no raw smoke underlay)**.

## How the look is produced (one sentence per idea)

- **Thin filaments and fur** come from ~0.25 to 1 million particles following a divergence-free curl-noise field; streamlines of such a field stretch into hair-thin sheets, and the fur is individual particle trails at the edge of a bundle. A fluid-dye sim would blur anything thinner than a texel, so it is not used for milestone 1.
- **Bright clusters** fall out of additive splatting where streamlines bunch, plus a tone curve with a hard knee and bloom.
- **Smoke softness** comes from a trail buffer that is advected by the same field and decays every frame (semi-Lagrangian), drawn *before* fresh particle splats so filaments stay crisp on top of soft history.
- **ASCII** is one fragment pass over a per-cell texture: luminance picks ramp position, the flow direction at the cell picks an oriented glyph row (`- / | \`) so filaments read as strokes, and two nested grids (fine for wisps, coarse for highlights) are blended per coarse cell.
- **Randomness** is a seed that derives noise offsets, emitter layout, palette variant, glyph set and bounded parameter jitter; invariants (black background, cold monochrome family, tone shape) are never randomised.

## Per-frame pass list

Resolutions are for 1080p-equivalent; all buffers derive from the framebuffer size, so 1080x1920 and 4K work unchanged. Costs: dev box, measured by the design pass unless marked estimate.

| # | Pass | Res / format | Reads | Writes | Cost |
|---|---|---|---|---|---|
| P0 | Flow field: 3-octave curl noise (+ optional external velocity) | 1/4 res, RG16F | UBO, extVel (1x1 zero texture until camera phase) | velTex | ~0.1 ms |
| P1 | Particle update: advect, age, respawn at emitters | SxS RGBA32F ping-pong (x, y, age, hash) | state, velTex, UBO | state' | ~0.6 ms at 1M |
| P3 | Trail: advect previous density along velTex, decay, small bias | full res, R16F ping-pong | dens, velTex | dens' | ~0.3 ms |
| P2 | Splat: `S*S` GL_POINTS from `gl_VertexID` + `texelFetch`, gaussian point, blend ONE,ONE, no clear | into dens' | state' | dens' | 2.8 ms at 1M, ~0.7 ms at 262k |
| P4 | Bloom: knee threshold, 3 downsample levels, separable blur, tinted cold | 1/4, 1/8, 1/16 res R16F | dens' | bloomTex | ~0.2 ms |
| P5 | Grade to cells: 4-tap supersample of density per cell, tone curve, flow angle and magnitude per cell | fine grid and coarse grid, RGBA16F (lum, angle, mag, highlight) | dens', bloomTex, velTex | cellF, cellC | <0.1 ms |
| P6 | ASCII glyph pass with nested grids, directional atlas rows, palette, grain, vignette | full res RGBA8 | cellF, cellC, atlasF, atlasC | frame | 0.3 ms (measured in bench) |
| P7 | Present, or export readback via PBO to ffmpeg | window / export FBO | frame | screen / pipe | 0.1 ms; 3.2 ms readback at 1080p |

Total about 4.5 ms at 1M particles, about 2.5 ms at 262k. 60 fps budget is 16.7 ms.

Particle tiers (a runtime controller drops one tier if rolling p95 frame time exceeds 14 ms for 60 frames):

| Target | State texture | Particles | Density res |
|---|---|---|---|
| GTX 1650 Ti | 1024x1024 | 1.05M | full |
| M1 and later | 724x724 | 524k | full |
| A18 Pro | 512x512 | 262k | half res, ASCII pass upscales |

## Key techniques (non-obvious parts)

Splat vertex shader (no VBO; one point per state texel):

```glsl
#version 410 core
uniform sampler2D u_state; uniform ivec2 u_stateSize; uniform float u_pointSize;
out float v_w;
void main(){
  ivec2 ij = ivec2(gl_VertexID % u_stateSize.x, gl_VertexID / u_stateSize.x);
  vec4 p = texelFetch(u_state, ij, 0);          // x,y in clip space, age 0..1, hash
  gl_Position = vec4(p.xy, 0.0, 1.0);
  gl_PointSize = u_pointSize;                    // needs ctx.enable(moderngl.PROGRAM_POINT_SIZE)
  v_w = smoothstep(0.0, 0.15, p.z) * (1.0 - smoothstep(0.8, 1.0, p.z));
}
```

Curl noise (P0), seed offsets the domain, z is time:

```glsl
vec2 curl(vec2 p, float z){ const float e = 0.01;
  float dx = noise3(vec3(p.x+e,p.y,z)) - noise3(vec3(p.x-e,p.y,z));
  float dy = noise3(vec3(p.x,p.y+e,z)) - noise3(vec3(p.x,p.y-e,z));
  return vec2(dy, -dx) / (2.0*e); }
// v = c1*curl(p*s1+off, t*w1) + c2*curl(p*s2, t*w2) + c3*curl(p*s3, t*w3); p is aspect-corrected
```

Trail advection (P3):

```glsl
vec2 back = uv - texture(u_vel, uv).xy * u_dt * u_trailAdvect;
float d = texture(u_dens, back).r * u_decay - u_bias;   // decay 0.90..0.985, bias ~0.002
f = vec4(max(d, 0.0));
```

Respawn and all per-particle randomness use an integer hash of `(texel, frameIndex, seed)`, never `fract(sin(...))`, which differs between NVIDIA and Apple drivers.

## ASCII mapping (P5 + P6)

- **Grids.** Cell sizes are defined at 1080p-equivalent and scaled by `buffer_height / 1080` (or width for 9:16), so column count follows the aspect. Default pair: fine 6x12 px, coarse 12x24 px (coarse = exactly 2x fine so they tile). Preset-selectable pairs: 4x8 + 12x24 ("furry", glyphs become texture), 8x16 + 16x32 ("readable"). Pure-glyph mode: black between glyphs, ink floor 0.
- **Blend.** Per coarse cell, `w = smoothstep(T - B, T + B, toneCoarse)` with `T = 0.62`, `B = 0.06`: below the band the four fine glyphs render, above it one coarse glyph. Highlights therefore become big readable characters, wisps stay fine.
- **Direction.** If flow magnitude at the cell exceeds a threshold and ramp position is below 0.7, fold the flow angle to [0, pi), bin into 4 orientations, and use that atlas row; highlights always use the isotropic row (the references show blobs, not strokes, in the bright clusters).
- **Atlas.** 5 rows (isotropic, horizontal, `/`, vertical, `\`) x 10 columns, R8, one atlas per grid. Build by rendering each glyph at 4x the cell size with OpenCV Hershey, `INTER_AREA` downsample to the exact cell, re-sort each row by measured ink, and peak-normalise every glyph so its brightest pixel is 1.0. This fixes the three defects found in the bench demo: brightness capped near 40% because 1 px strokes cover 10 to 20% of a cell, atlas aliasing from sampling a 16x32 atlas into 8x16 cells, and mirrored glyphs because `mirror` flipped the in-cell coordinate (mirror only the *sample* position).
  Seed rows (re-sorted by ink at build time): iso `  . : * o O 8 # @ M`, H `  . _ - " ~ =`, `/` row `  , / 7 Z y %`, V `  ' : ; ! | I`, `\` row `` ` \ N K & `` (columns 7 to 9 shared from iso).
- **Tone.** `tone(d) = pow(smoothstep(knee, 1, 1 - exp(-d * exposure)), gamma)` with exposure 1.4, knee 0.04, gamma 0.75. Ramp index = `floor(r * 9 + jitter)` with a per-cell hash jitter in [-0.4, 0.4] reseeded on each onset (breaks banding, gives the skitter on percussion).
- **Light.** Output linear RGBA16F before the final encode: `lum = r * ink * (1 + glow * r^2)` with glow 1.5 so highlights exceed 1.0 and bloom lifts them.
- **Colour.** 4-stop gradient on r: 0.0 black, 0.30 `(0.06,0.08,0.11)`, 0.70 `(0.42,0.48,0.56)`, 1.0 `(0.86,0.96,1.0)`; `cyan_tint` 0 to 0.15 shifts the top stops; bloom tinted `(0.85,0.97,1.0)`. Grain 0.02 to 0.06 weighted to midtones; flicker 0.01 to 0.04; vignette 0.3 to 0.5.

## Audio to visual mapping

Features are precomputed per song with librosa (hop 512 at 44.1 kHz), normalised to song percentiles (p5 to 0, p95 to 1), cached as `.npz` keyed by file hash, sampled at song time + 30 ms lookahead, then smoothed with one-pole attack/release envelopes.

| Feature | Drives | Attack / release | Range |
|---|---|---|---|
| Sub RMS (20 to 60 Hz) | curl base scale; trail decay (persistence) | 60 / 400 ms | scale x1.0 to 1.6; decay 0.96 to 0.985 |
| Bass RMS (60 to 200) | emitter rate; advection speed | 30 / 250 | speed x0.6 to 1.3 |
| Mid RMS | tone gamma (midtone lift) | 40 / 200 | 0.9 down to 0.65 |
| High RMS | octave-3 curl weight; grain; fine-grid bias (T up) | 15 / 120 | octave 0 to 0.4; T +0.1 |
| Onset strength (percussive) | radial impulse at nearest emitter; exposure +0.3; glyph jitter reseed; bloom +0.2 | 0 / 150 | impulse 1 to 3 |
| Beat phase | exposure breathing `1 + 0.08 cos(2 pi phase)`; emitter respawn quantised to downbeat | none | |
| Spectral centroid | coarse threshold T (low centroid = coarser glyphs); cyan tint | 300 / 600 | T 0.52 to 0.72 |
| Spectral flux | octave-2 curl amplitude; ramp jitter amount | 20 / 200 | jitter 0.2 to 0.8 |
| Harmonic energy | trail persistence; bloom gain | 200 / 1200 | bloom 0.35 to 0.9 |
| Percussive energy | tone knee (sharper wisps) | 10 / 100 | 0.04 to 0.12 |
| Loudness (2 s RMS) | emitter count; exposure; vignette | 500 / 2000 | 1 to 5 emitters |
| Section boundary | reseed emitter layout with 2-beat crossfade; palette variant; grid pair | one-shot | |

Intended behaviour: a kick puffs the main emitter and briefly sharpens trails; hi-hats only reseed glyph jitter and shimmer the fine octave; a drop doubles emitters, lengthens trails, pushes highlights to the coarse grid and raises bloom; a breakdown drops to 1 or 2 emitters, fine grid only, slow drift, heavier grain and vignette.

## Seed, presets and reproducibility

- `numpy.random.default_rng(seed)` draws sub-seeds in a fixed order: noise domain offset and rotation, drift direction, emitter count (1 to 5) and positions in a 0.15 to 0.85 safe area with min spacing 0.2, flow scale x0.8 to 1.4, octave weight jitter +-20%, palette variant (3 curated cold palettes, tint jitter +-0.03), glyph set, grid pair, and every preset parameter's `[value, jitter_lo, jitter_hi]`.
- Preset JSON sections: `meta, grid, glyphs, tone, palette, bloom, grain, sim, audio_map, random`. A run is fully defined by `(preset, seed, song, feature-extractor version, resolution, fps)`; every screenshot and export writes `resolved_params.json` with all resolved values and a code hash.
- Determinism: the simulation never reads wall-clock time. Live mode runs a fixed-step accumulator (sim dt = 1/60, at most 3 steps per rendered frame) that catches up to the sounddevice clock (frames delivered in the callback + reported latency + user offset). Export mode uses `t = n / fps`, one step per frame, so the same seed renders the same frames.

## Aspect handling (both from the start)

- Flow-field and emitter coordinates live in an aspect-corrected unit space (`p = (uv - 0.5) * vec2(aspect, 1)`), so a 9:16 run gets a tall field, not a stretched one.
- Emitter safe area and bloom radii are expressed in fractions of the short side.
- Grid cell size scales with the short side; column count is derived, never fixed.
- Export presets: `landscape` 1920x1080 and 3840x2160, `portrait` 1080x1920, both at 30 or 60 fps.

## Iteration tooling

Shader hot-reload (poll mtimes every 250 ms, keep the old program and overlay the compile error on failure), HUD rendered through the ASCII pass (fps, song time, seed, envelope bars, last-changed parameter), keys: `R` reseed, `[` `]` seed +-1, `Space` pause, `S` screenshot + sidecar JSON, `A` cycle ASCII / raw density / flow arrows, `B` bloom, `G` grid overlay, `1-9` preset slots, `Left/Right` scrub 5 s, `,` `.` audio offset +-10 ms, `E` export, `Ctrl+S` save preset. Per-pass GPU timers via `ctx.query(time=True)`.

## Module layout (`D:\AudioViz\audioviz\`)

- `__main__.py`: CLI `play song.mp3 --seed N --preset ink_cold --aspect 16:9|9:16` and `export song.mp3 --seed N --size 1920x1080 --fps 60 --out file.mp4`.
- `config.py`: tunables dataclass, per-platform tiers. `seed.py`: seed to resolved parameters. `preset.py`: JSON schema, jitter resolution, `resolved_params.json` writer.
- `audio/analysis.py` (librosa feature table + cache), `audio/player.py` (sounddevice stream, song clock, offset), `audio/features.py` (lookup, envelopes, UBO fields).
- `gl/context.py` (moderngl-window GLFW config at 4.1 core, headless standalone context for export), `gl/pingpong.py` (FBO pair with `swap()`, generalising `bench/bench_cv.py` section G), `gl/passes.py` (fullscreen pass helper, `#include` preprocessor), `gl/uniforms.py` (std140 UBO from a numpy dtype, vec4-aligned fields only), `gl/timing.py`.
- `sim/flowfield.py` (P0), `sim/particles.py` (P1, P2, tiers, dummy-attribute fallback for Apple drivers), `sim/trails.py` (P3).
- `render/bloom.py` (P4), `render/grade.py` (P5), `render/ascii.py` (directional atlas builder ported from `bench/ascii_shader_demo.py:build_atlas`, P6), `render/present.py` (P7, debug views).
- `shaders/`: `common.glsl` (UBO layout, integer hash, noise3, curl), `flow.frag`, `particles.frag`, `splat.vert/.frag`, `trails.frag`, `bloom_*.frag`, `grade.frag`, `ascii.frag`, `present.frag`.
- `export/recorder.py`: fixed-step loop, PBO readback, imageio-ffmpeg pipe with audio mux.
- `inputs/camera.py`: interface returning `extVel` (and later `mask`) textures; stub returns 1x1 zero textures until the camera phase. Real implementation reuses `bench/cam_ffmpeg.py` (MJPEG via ffmpeg) and DIS ULTRAFAST at 320x180 (2.6 ms measured).
- `presets/ink_cold.json` (+ `ink_cold_portrait.json`).
- `app.py`: wiring, fixed-step loop, pass ordering, keys.

UBO `Frame` (std140, binding 0 in every program): `time(t, dt, frameIndex, seedF)`, `seedU`, `res(fbW, fbH, colsF, rowsF)`, `bands(sub, low, mid, high)`, `onset(strength, beatPhase, beatPulse, flux)`, `spectral(centroid, rms, sectionEnergy, _)`, `simA(speed, decay, trailAdvect, pointSize)`, `simB(curlScale1..3, curlTimeRate)`, `look(exposure, bloom, dirThreshold, levels)`, `emitters[8]`, `palette[4]`.

## Milestones and verification

- **M0 Environment.** `uv pip install moderngl-window glfw soundfile librosa scipy` into `.venv` (moderngl, numpy, opencv-contrib, sounddevice, imageio-ffmpeg already present). Verify: `python -m audioviz.gl.context` opens a window, prints a 4.1 core `GL_VERSION`, `PROGRAM_POINT_SIZE` enables without error, point size range printed.
- **M1 Particles.** P0, P1, P2, P7, grayscale. Verify: GPU timer sum under 6 ms at 1M on the dev box; `--seed 1` and `--seed 2` give visibly different emitter layouts; particles do not collapse into blobs over 60 s.
- **M2 Smoke look.** Add P3, P4, P5. Verify: screenshot beside the three references shows thin filaments with fur edges and a few bright clusters; `--debug-view density` max stays below `splat / (1 - decay)`; frame time under 7 ms; 1080x1920 run looks tall, not stretched.
- **M3 ASCII.** P6 with the directional atlas and nested grids. Verify: `--debug-view` toggles raw vs ASCII; the ASCII frame reads as the smoke at arm's length; glyph orientation follows the flow (grid overlay + flow arrows view); P6 timer about 0.3 ms; no mirrored characters.
- **M4 Audio.** Analysis cache, playback clock, UBO mappings. Verify: `--click-test` flashes on beats in sync by ear after setting the offset; sounddevice underflow count stays 0 over a full song; frame time under 8 ms; a kick, a hi-hat run and a drop each do what the mapping table says.
- **M5 Determinism and export.** Verify: `export --seed 7` run twice gives identical md5 for frame 300; 1080p60 export runs faster than real time; `ffprobe` shows equal audio and video durations; `resolved_params.json` reproduces the run.
- **M6 Mac validation.** Repeat M1 to M5 on an M1-class Mac (and the MacBook Neo if available): points render under core profile, Retina buffer sizes correct, per-pass timers recorded, tiers locked into `config.py`.
- **M7 Camera stir (optional, after the above).** `inputs/camera.py` real implementation: ffmpeg MJPEG capture thread, DIS optical flow at 320x180, uploaded as `extVel` and summed in P0 with `ext_flow_gain`. Verify: waving a hand moves the smoke with no visible camera image, flow thread under 5 ms per frame, no change to any other pass.

## Risks and mitigations

- macOS core profile ignores `gl_PointSize` unless `PROGRAM_POINT_SIZE` is enabled; query the point size range and clamp.
- Empty VAOs render nothing on some Apple drivers: bind a dummy 1-float attribute of N entries behind a Darwin-default flag.
- Blending into 32F is slow on some Apple drivers: density stays R16F; decay keeps values bounded.
- Hash/noise differences across vendors: integer hashes only, `texelFetch` for all state reads.
- Retina: use `wnd.buffer_size` everywhere, never `wnd.size`.
- Fine 4x8 cells stop reading as characters; default is 6x12 + 12x24, with 4x8 kept as a preset.
- The design-pass particle timings (1M splat 2.8 ms, update 0.97 ms) were measured on the dev GPU by the design agent, not yet reproduced in `bench/`; M1 re-measures them with the real pass chain.

## Out of scope for this plan

Fluid pressure solve (can be added into P0 later), person-mask emitter (user chose stir-only), Spout/Syphon output, packaging.
