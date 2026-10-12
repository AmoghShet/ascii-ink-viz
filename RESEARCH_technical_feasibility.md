# AudioViz: Technical Feasibility Research

**Date:** 2026-10-07
**Goal:** A real-time, "trippy" audiovisualizer in Python that takes (a) a song file and (b) live webcam input, and runs on both Windows and macOS.
**Scope of this document:** What is technically possible and which libraries/architecture to use. Creative design (the actual look, palettes, effect choreography for a dark Atoms for Peace / Thom Yorke style track) is deliberately out of scope and comes next.

Research method: web research against primary sources (PyPI pages, official docs, OpenCV/librosa source) where possible. Each claim carries a source tag `[S#]` that maps to the Sources list at the end. Claims that rest on forum posts or general knowledge are flagged `[LOW CONFIDENCE]` or `[GENERAL KNOWLEDGE]`. Where a conclusion needs an assumption not in the evidence, it is flagged `[ASSUMPTION]`.

---

## 0. Machine snapshot (this dev box)

| Item | Value |
|---|---|
| OS | Windows 11 Home 10.0.26300 |
| Python on PATH | 3.10.6 |
| Installed relevant packages | numpy 1.24.2 only |
| GPUs | NVIDIA GeForce GTX 1650 Ti + AMD Radeon integrated (hybrid laptop) |

The Python version matters: see Section 5 for a version matrix. Short version: **install Python 3.12 (or 3.11) in a venv for this project**; several current libraries no longer support 3.10.

---

## 1. Bottom line

**Yes, this is fully feasible in Python on both OSes, in real time, with GPU shaders.** The recommended stack:

| Layer | Pick | Why |
|---|---|---|
| Song decode | `soundfile` | pip wheels bundle libsndfile on Win/Mac; MP3 supported since 0.11.0 [S1] |
| Song playback + master clock | `sounddevice` | pip wheels bundle PortAudio on Win/Mac [S2]; callback gives DAC timestamps for sync [S3] |
| Offline song analysis (beats, onsets, spectral features) | `librosa` (0.11 on Py3.8+, 1.0 on Py3.12+) [S4][S5] | Precompute once, then look up by playback time |
| Optional SOTA beat grid | `beat-this` (PyTorch) [S6] | Better beats than librosa for electronic music; offline only |
| Camera | `imageio-ffmpeg` (bundled ffmpeg, MJPEG via DirectShow / AVFoundation) piped to NumPy; OpenCV's own backends as fallback | Measured on the dev box: OpenCV's DirectShow backend ignores MJPG and wedges the C270 at 720p, ffmpeg negotiates MJPEG correctly (Section 9.1) [S7][S8][S16] |
| Optional camera CV | OpenCV DIS optical flow [S9]; MediaPipe segmentation/pose [S10][S11] | Motion-driven and body-driven effects |
| GPU rendering | `moderngl` + `moderngl-window` (GLFW backend) | OpenGL 3.3+ wrapper, wheels for Win/Mac incl. Apple Silicon [S12][S13][S14] |
| Shader language | GLSL `#version 330` or `#version 410 core` | macOS caps OpenGL at 4.1 core profile [S15] |
| Video export | `imageio-ffmpeg` (bundled ffmpeg binary) [S16], optionally `turbopipe` [S17] | Pipe raw frames to ffmpeg, mux song |
| Live output to VJ apps (optional) | `SpoutGL` (Win) / `syphon-python` (Mac) [S18][S19] | Send the GPU texture into Resolume/OBS |

Alternatives considered and why they are not the primary pick: `wgpu-py` (modern, Metal/Vulkan/DX12, but Python >=3.11 and API still unsettled) [S20]; Taichi (GPU kernels, but PyPI wheels only for Python 3.9-3.11) [S21]; ShaderFlow (an existing Python audio-reactive shader framework, AGPL-3.0 unless sponsored, no evidence of webcam input) [S22]; py5 (Processing-style, needs a Java 17 JDK) [S23]; aubio / madmom (last releases 2019 / 2018, no wheels) [S24][S25].

---

## 2. Proposed architecture

```
 song.mp3/.wav ---> soundfile.read() ---> float32 PCM in RAM
        |                                       |
        |  (offline, once, ~seconds)            |  (realtime)
        v                                       v
  librosa / beat_this                    sounddevice.OutputStream
  - onset strength envelope              - callback copies PCM chunks to DAC
  - beat times, tempo                    - exposes stream.time + outputBufferDacTime
  - per-band RMS (sub/low/mid/high)      - this is THE master clock
  - spectral flux, centroid                     |
  - (optional) section boundaries               v
        |                               "song position t" each frame
        v                                       |
  feature table indexed by time  <--------------+
        |
        v
  +-------------------- main render loop (60 fps target) --------------------+
  |  camera thread (OpenCV VideoCapture, keeps only LATEST frame)            |
  |      -> optional: DIS optical flow (CPU)  -> flow texture                |
  |      -> optional: MediaPipe selfie/pose segmentation -> mask texture     |
  |  upload camera frame -> moderngl Texture.write()                         |
  |  uniforms: t, beat phase, band energies, onset, flow stats, mask stats   |
  |  pass 1..N: fragment shaders over a fullscreen quad                      |
  |      ping-pong FBOs for feedback / trails / smear / datamosh             |
  |  final pass -> window (GLFW via moderngl-window)                         |
  |      optional tee: -> ffmpeg stdin (record)  -> Spout/Syphon (VJ apps)   |
  +---------------------------------------------------------------------------+
```

Key design decisions and the evidence behind them:

1. **Precompute song analysis, drive visuals by playback clock.** Because the song is a file (not a live mic), everything expensive (beat tracking, onset detection, section analysis) can run once before playback. librosa's beat tracker accepts a precomputed onset envelope and exposes `hop_length` so the feature timeline can be mapped back to seconds [S4]. This avoids real-time beat tracking entirely, which is where Python would struggle. The only real-time audio math is cheap: an FFT of the current ~2048-sample window with NumPy. `[ASSUMPTION: a 2048-point NumPy FFT per frame at 60 fps is negligible; this is standard practice but was not benchmarked here.]`

2. **The audio stream is the clock, not the render loop.** `sounddevice` streams expose `Stream.time` ("the current stream time in seconds ... according to the same clock used to generate the timestamps passed with the time argument to the stream callback") and the callback receives `time.outputBufferDacTime` (DAC output time of the first sample in the buffer) [S3]. Track "samples delivered so far" in the callback and compute song position from it; latency is reported by `Stream.latency` [S3]. For tighter guarantees, `rtmixer` implements the callback in C so the GIL and garbage collection cannot stall it [S26].

3. **Camera on its own thread, keep only the newest frame.** OpenCV's `read()`/`grab()` return frames from a FIFO buffer, not the newest frame, which produces visible lag; `CAP_PROP_BUFFERSIZE` is not honored by every backend [S27] `[LOW CONFIDENCE: forum-sourced; the property exists in the OpenCV enum [S8] but per-backend support is not documented there]`. The robust pattern is a dedicated thread that continuously reads and overwrites a single "latest frame" slot. `[GENERAL KNOWLEDGE: widely used pattern, no single primary source.]`

4. **All visual effects in fragment shaders, with ping-pong framebuffers for feedback.** Ping-pong means two FBOs where one is read while the other is written, swapped each frame; this is the standard way to do iterative GPU effects such as trails, smoke, fluid and feedback [S28]. Inigo Quilez's feedback-effect article describes the same idea with a single texture copied back each frame, attenuating old copies so they fade [S29]. This is what makes "trippy" cheap: the camera frame is just another texture sampled by the shader chain.

5. **Target GLSL 4.1 or lower, no compute shaders.** macOS supports OpenGL only up to 4.1 and only in core profile, and Apple has deprecated OpenGL [S15]. moderngl requires OpenGL 3.3 and needs 4.3 for compute shaders [S12], so compute shaders are out on Mac. Everything proposed here is fragment-shader based, which is fine. The GTX 1650 Ti on Windows will run anything up to 4.6, but write shaders for 4.1 so one codebase works on both.

---

## 3. Component findings

### 3.1 Song decoding

- `soundfile` 0.14.0 (released 2026-06-06) ships wheels for Windows (x64/x86/arm64), macOS (x64 and arm64) and Linux; "MP3 support" arrived in 0.11.0 (2022-06-02); the docs say "On Windows (64/32) and OS X (Intel/ARM) and Linux 64, this will also install a current version of the library libsndfile" [S1].
- librosa reads audio through soundfile ("librosa uses soundfile for reading audio") [S30]. librosa 1.0.0 removed the `audioread` fallback backend entirely [S31], so ffmpeg is no longer a decoding dependency for common formats. Formats libsndfile cannot read (e.g. AAC/M4A) would need ffmpeg via `imageio-ffmpeg` [S16]. `[ASSUMPTION: that libsndfile does not decode AAC; the fetched pages list MP3 support but did not enumerate every codec.]`

### 3.2 Playback and timing

- `sounddevice` 0.5.6 (2026-08-17), Python >=3.7; wheels for Windows (x64/x86/arm64) and a macOS universal2 wheel [S2b]. The install docs: "If you install the sounddevice module with pip on macOS or Windows, the PortAudio library will be installed automagically" [S2]. No Homebrew step needed on Mac.
- PyAudio also works (Windows wheels for 3.12/3.13 exist; macOS needs `brew install portaudio`) [S32], but sounddevice is the simpler install and exposes the timing fields described above [S3].

### 3.3 Song analysis (offline)

- **librosa**: `beat_track` works in three stages (onset strength, tempo from onset autocorrelation, peak-picking consistent with tempo) and accepts a precomputed onset envelope; `onset_detect` likewise accepts a precomputed envelope [S4]. Version constraint: librosa 1.0.0 (2026-08-11) requires Python >=3.12 [S5]; librosa 0.11.0 requires Python >=3.8 [S33] and still depends on `audioread` [S33].
- **beat_this** (CPJKU, ISMIR 2024): `pip install beat-this`, PyTorch 2.0+, "state-of-the-art accuracy without DBN postprocessing", no madmom dependency; needs ffmpeg/torchaudio backend for non-WAV input [S6]. Heavy dependency (torch) but offline-only, so acceptable as an optional "better beat grid" step. `[LOW CONFIDENCE on relative accuracy for this specific genre; the SOTA claim is from the project's own README.]`
- **aubio**: last PyPI release 0.4.9 on 2019-02-08, source distribution only, no wheels [S24]. Expect build pain on modern Python. Not recommended.
- **madmom**: last release 0.16.1 on 2018-11-14, classifiers up to Python 3.7 [S25]. Not recommended.
- Useful per-frame features for a dark electronic track (all from librosa, standard API): onset strength, band-limited RMS (sub-bass / bass / mids / highs via STFT bins), spectral centroid and flux, harmonic/percussive separation (HPSS) to split kick/hat transients from pads. `[GENERAL KNOWLEDGE: these functions exist in librosa; specific pages for each were not fetched in this pass.]`

### 3.4 Camera capture

- `opencv-python` 5.0.0.93 (2026-07-02): wheels for Python 3.7-3.14 on Windows (x86/x64), macOS (x86-64 and arm64) and Linux [S7].
- Backend selection is explicit: `VideoCapture(index, apiPreference)` where apiPreference "can be used to enforce a specific reader implementation ... e.g. cv::CAP_DSHOW or cv::CAP_MSMF" [S8]. Enum values present: `CAP_DSHOW` (DirectShow), `CAP_MSMF` (Media Foundation), `CAP_AVFOUNDATION` [S8].
- **Windows gotcha:** reports that the default MSMF backend can stall for ~30 s if it fails to negotiate a format, with `cv2.CAP_DSHOW` as the workaround [S34] `[LOW CONFIDENCE: single secondary source]`. Plan: try MSMF, fall back to DSHOW, and expose the choice in config.
- **macOS gotcha:** the first run prints "OpenCV: not authorized to capture video (status 0), requesting..." and macOS prompts for camera permission for the host app (Terminal / iTerm / IDE); rerun after allowing [S35] `[LOW CONFIDENCE: forum sources, but consistent across several]`. A newer AVFoundation warning about `NSCameraUseContinuityCameraDeviceType` in Info.plist appears when enumerating devices and is harmless for scripts run from Terminal [S36] `[LOW CONFIDENCE]`.
- **Latency:** see Section 2 item 3. Also set `CAP_PROP_FRAME_WIDTH/HEIGHT` and `CAP_PROP_FPS` [S8] to 1280x720 or 640x480 at 30 fps; the GPU will upscale and nobody will notice at the effect intensity planned.
- Per-frame GPU upload: `moderngl.Texture.write(data, viewport=..., alignment=...)` updates a texture from bytes or a Buffer [S37]. A 1280x720 RGB frame is ~2.7 MB; uploading that 30 times a second is well within PCIe bandwidth. `[GENERAL KNOWLEDGE]`

### 3.5 Camera-derived signals (optional, adds a lot of "trippy")

- **Dense optical flow (CPU):** OpenCV's DIS algorithm "includes three presets (ULTRAFAST, FAST, MEDIUM) ... even the slowest preset is still relatively fast" [S9]. Farneback is the classic alternative but "requires more computation" [S38]. Use DIS ULTRAFAST on a downscaled grayscale frame (e.g. 320x180) and upload the flow field as an RG float texture to displace/smear the feedback buffer: this is the datamosh look. `[ASSUMPTION: that DIS ULTRAFAST at 320x180 stays under ~5 ms per frame on this CPU; no benchmark was found, must be measured.]`
- **MediaPipe (person/hair/pose masks):** Image Segmenter and Pose Landmarker both offer `LIVE_STREAM` mode "for a livestream of input data, such as from a camera" [S10][S11]. Pose Landmarker gives 33 landmarks and has an `output_segmentation_masks` option [S11]. Segmenter models and Google's published latencies: selfie (256x256) ~33 ms CPU, hair (512x512) ~58 ms CPU, multiclass selfie ~218 ms CPU / ~71 ms GPU, DeepLab-v3 ~124 ms CPU [S39]. So selfie segmentation is usable at ~30 fps on a separate thread; multiclass is not real-time on CPU. `[ASSUMPTION: Google's latency table is from their reference hardware; this laptop will differ.]`
  - Platform caveat: mediapipe 1.1.0 (2026-10-06) supports Python 3.9-3.14 and ships Windows x64/arm64, Linux, and **macOS arm64 only** wheels; no macOS Intel wheel is listed [S40]. The v1.0.0 release notes say "Remove MacOS prebuilts" [S41] `[LOW CONFIDENCE on interpretation: the 1.1.0 PyPI listing still shows an arm64 macOS wheel, so "prebuilts" likely refers to something else, but Intel Mac support should be treated as absent]`. If the Mac target is Intel, skip MediaPipe or pin an older 0.10.x release.

### 3.6 GPU rendering: options compared

| Option | Status (verified) | Fit |
|---|---|---|
| **moderngl** 5.12.0 (2024-10-17), Python >=3.7, wheels Win/Linux/macOS x64+arm64; "ModernGL require OpenGL 3.3. Compute Shaders require OpenGL 4.3" [S12] | Mature, stable | **Primary pick.** Shadertoy-style multipass is trivial; macOS backend is CGL [S42] |
| **moderngl-window** 3.1.1 (2025-01-19), Python >=3.9; backends pyglet, pygame, PySide2, GLFW, SDL2, PyQt5, tkinter [S13] | Mature | Window + input + resource loading; pick GLFW |
| **glfw** 2.10.2 (2026-07-21); "The GLFW shared library and Visual C++ runtime are included in the Python wheels" (Windows) and "The GLFW shared library for 64-bit is included in the Python wheels for macOS"; macOS 11.0+ arm64 wheel [S14] | Mature | No system install needed |
| **wgpu** 0.32.0 (2026-07-19), Python >=3.11, Vulkan/Metal/DX12/OpenGL; "Until WebGPU settles as a standard, its specification may change, and with that our API will probably too" [S20] | Usable, moving target | Best long-term answer to Apple's OpenGL deprecation; shaders are WGSL not GLSL; fewer tutorials/presets to borrow. Revisit if moderngl breaks on a future macOS |
| **Taichi** 1.7.4 (2025-07-31), Python 3.9-3.11, backends CUDA/Vulkan/Metal/OpenGL 4.3+/CPU [S21] | Mature but Python-version-locked | Great for particle/fluid sims; incompatible with a Python 3.12 env |
| **ShaderFlow** (BrokenSource): "Audio reactive processing for music visualizers, FFTs already in natural scale", "Video as texture", video export via TurboPipe; AGPL-3.0, "Commercial use allowed and encouraged, simply pick a Sponsorship tier" [S22] | Active | Closest existing thing to this project, but no evidence of webcam input in the README, and AGPL is a constraint. Worth studying its shader presets |
| **py5** (Processing for CPython): needs Java 17 (or 11) installed; supports shaders and video capture via Processing libs [S23] | Active | Adds a JDK dependency; not needed |

**macOS OpenGL ceiling:** OpenGL 4.1 core profile maximum, deprecated since 10.14 but still shipping [S15]. Practical rule: `#version 330 core` or `#version 410 core` in every shader, create the context as core profile, no compute shaders, no `GL_ARB_*` 4.2+ extensions. `[ASSUMPTION: Apple has not removed OpenGL in the macOS versions you will run; as of the sources found it remains deprecated-but-present.]`

### 3.7 Effect techniques that are known-feasible in fragment shaders

All of these are standard real-time techniques; the list is `[GENERAL KNOWLEDGE]` except where cited. They are listed here only to confirm feasibility; which ones to use is the creative-design step.

- Frame feedback with decay, zoom, rotation, offset (video-feedback tunnel) [S28][S29]
- Optical-flow-displaced feedback (datamosh / pixel bleeding) using the DIS flow texture [S9]
- Kaleidoscope / radial mirroring via polar-coordinate UV remap
- Chromatic aberration, RGB channel split keyed to onset strength
- Posterize / threshold / solarize, duotone palette mapping keyed to spectral centroid
- Slit-scan and time-displacement using a ring buffer of the last N camera frames (texture array or 3D texture)
- Reaction-diffusion or fluid-ish smear in the feedback pass, driven by bass energy
- Person/background masking to apply different effects to the silhouette vs. room [S10][S39]
- Pose landmarks as effect anchors (hands as light sources, head as kaleidoscope centre) [S11]
- Edge detection (Sobel in-shader) with glow accumulated in the feedback buffer
- Pixel sorting (approximate, in-shader) and blocky macroblock corruption

### 3.8 Recording and export

- `imageio-ffmpeg` 0.6.x bundles an ffmpeg executable in platform wheels for "Windows 7+, Linux kernel 2.6.32+, OSX 10.9+" and exposes `write_frames()` generators [S16]. Pipe the rendered frames (read back from the final FBO) into ffmpeg and pass the song file as a second input to mux audio. Audio muxing is standard ffmpeg usage; the Python side only needs the right command line. `[GENERAL KNOWLEDGE for the mux step; the imageio-ffmpeg page documents frame writing, not audio mux.]`
- `turbopipe` speeds moderngl Buffer to ffmpeg stdin transfers with zero-copy chunked writes and threaded I/O [S17]; optional optimization for 1080p/4K offline renders.
- For offline "render the whole song at 4K60" mode, the same pipeline runs with a fixed timestep (song position advanced by 1/60 s per frame) instead of the audio clock; only the camera input must then be a prerecorded video rather than live. `[ASSUMPTION: you may want an offline render; this follows from the architecture, not from a stated requirement.]`

### 3.9 Live output to VJ / streaming software (optional)

- Windows: `SpoutGL` (pip, Python 3.8+, no external deps) provides `sendImage` and friends [S18].
- macOS: `syphon-python` (pip, macOS 11+, PyObjC-based) [S19].
- Resolume and OBS accept Spout/Syphon sources natively [S43].

### 3.11 Reference project: ASCILINE (user-suggested)

ASCILINE (github.com/YusufB5/ASCILINE) [S47] was cloned and read at source level (`ascii_video_player2.py`, `stream_server.py`, `codec.py`, `app.js`, about 7,800 lines total including tests). What it actually does:

- **Pipeline.** `cv2.VideoCapture` (file or webcam index) reads a frame, `cv2.resize` shrinks it straight to the character grid (e.g. 240 x 67 cells, `INTER_LINEAR`), `cvtColor` makes a gray copy. Character index is proportional: `(gray * (n - 1)) // 255` into a luminance-ordered character list. Cell colour is simply the downscaled pixel's BGR, optionally quantised by a bit shift `(rgb >> qb) << qb`; the "64 / 512 / 32K / 262K / 16M colours" modes are just that shift at 6 / 5 / 3 / 2 / 0 bits. Each cell becomes 4 bytes `[char, R, G, B]`.
- **Transport and drawing.** The 4-byte cell buffer goes through an adaptive codec (raw / zlib / delta-of-changed-cells / RLE, keyframe every 48 frames, optional lossy colour tolerance) over a WebSocket. The browser draws every cell with Canvas `fillText` in "bold 8px Courier New", batching `fillStyle` changes for runs of equal colour. The terminal player instead emits ANSI 24-bit escape codes with run-length elision, in a pure Python double loop over cells, which is why it caps itself at 160 columns on Windows.
- **Filters.** Brightness / contrast / gamma / invert are folded into one 256-entry LUT applied with `cv2.LUT`; sharpness is a `filter2D` kernel on the gray plane; three switchable character palettes: a 93-character default ramp, a 10-character "flat" ramp, a 6-character "block" ramp. Webcam frames are mirrored.
- **Clock.** The browser's `<audio>` element is the master clock (`audioEl.currentTime + audioOffset`); rendering is gated until audio actually starts, and the server paces frames as `frame_index * frame_t` against wall time, dropping frames under client backpressure. Webcam mode has no audio and bypasses the gate.
- **Webcam code.** `cv2.VideoCapture(index)` with the default backend plus `CAP_PROP_BUFFERSIZE = 1`, no fourcc request, mirrored, fallback fps from a flag. On this machine that exact code path would hit everything measured in Section 9.1: the 25 s Media Foundation open, the rejected buffer-size property, and the YUY2 frame-rate ceiling.
- **Licence.** Core engine and server are AGPL-3.0-or-later; the browser client SDK is MIT [S47].

What it means for this project:

- **The technique transfers completely; the code does not need to and must not.** Everything in the pipeline above is a per-cell function of (luminance, colour) plus a glyph lookup, which is exactly what a fragment shader does per pixel. One pass: sample the camera texture at the cell centre, quantise luminance to N levels, pick a glyph from a luminance-ordered atlas texture, quantise colour by a bit-shift uniform, multiply by the glyph's ink. The atlas and the ramp are generated by rendering every printable ASCII character and sorting by ink coverage, so nothing is copied from ASCILINE. See `bench/ascii_shader_demo.py` and Section 9.9 for the measured result.
- **Their whole feature set becomes uniforms.** Column count, ramp length (93 / 10 / 6 reproduce their three palettes), colour bit depth (their six modes), gamma/contrast/invert, sharpness, mirror, and pixel mode (ramp length 1 with full ink) are each one float. All of them can be driven per frame by the song features, per region by the person mask, or per cell by optical flow, which ASCILINE cannot do because its mapping runs once per frame on the CPU and its output is a text grid.
- **Compositing is free.** Because the ASCII pass is just another framebuffer in the chain, it can feed the feedback buffer (trails of characters), be displaced by flow, or be blended with the raw camera by a mask, none of which exists in a text-based renderer.
- **Its clock design independently confirms Section 2 item 2:** audio is the time reference, video is slaved to it, and frames are dropped rather than audio delayed.
- **Its CPU cost model is the opposite of ours.** ASCILINE's expensive parts are the Python per-cell loops, the codec and the per-character canvas text draws; the shader version has none of these, so grid density is limited only by what looks good.

### 3.10 Packaging

PyInstaller "is not a cross-compiler": build on Windows for Windows and on macOS for macOS; it can create macOS `.app` bundles and works with code signing [S44]. A macOS `.app` will need a camera-usage string in Info.plist. `[GENERAL KNOWLEDGE: NSCameraUsageDescription requirement; not fetched from Apple docs in this pass.]` For a personal tool, "clone + venv + requirements.txt" on each machine is simpler and recommended first.

---

## 4. Platform gotchas to design around

| Platform | Issue | Mitigation |
|---|---|---|
| Windows (this laptop) | Hybrid graphics: the display adapter is the integrated GPU, so an OpenGL context may land on it instead of the NVIDIA GPU [S45] | Set the Python executable to "High performance" in Windows Graphics Settings / NVIDIA Control Panel. `[GENERAL KNOWLEDGE for the mitigation]` |
| Windows | MSMF camera backend may stall on open [S34] `[LOW CONFIDENCE]` | Config flag to use `cv2.CAP_DSHOW` |
| macOS | Camera permission prompt tied to the terminal/IDE process [S35] | Document "allow camera, then rerun" |
| macOS | OpenGL 4.1 max, deprecated [S15] | GLSL 4.1, no compute; keep wgpu-py as the migration path |
| macOS Intel | No current MediaPipe wheel [S40] | MediaPipe optional; feature-flag it |
| Both | Python version drift (Section 5) | Pin Python 3.12 and a requirements lock |

---

## 5. Python version matrix (as of 2026-10-07, from PyPI)

| Package | Version | Python support | Source |
|---|---|---|---|
| moderngl | 5.12.0 | >=3.7 | [S12] |
| moderngl-window | 3.1.1 | >=3.9 | [S13] |
| glfw | 2.10.2 | (wheels incl. macOS arm64) | [S14] |
| opencv-python | 5.0.0.93 | 3.7-3.14 | [S7] |
| sounddevice | 0.5.6 | >=3.7 | [S2b] |
| soundfile | 0.14.0 | py2.py3 universal wheels | [S1] |
| librosa | 1.0.0 | >=3.12 | [S5] |
| librosa | 0.11.0 | >=3.8 | [S33] |
| mediapipe | 1.1.0 | 3.9-3.14 (macOS arm64 only) | [S40] |
| beat-this | current | needs PyTorch >=2.0 | [S6] |
| wgpu | 0.32.0 | >=3.11 | [S20] |
| taichi | 1.7.4 | 3.9-3.11 | [S21] |
| imageio-ffmpeg | 0.6.x | wheels Win/macOS incl. arm64 | [S16] |

**Recommendation:** Python **3.12** in a project venv. Everything in the primary stack supports it, including librosa 1.0. The only casualty is Taichi, which is not in the primary stack. Your current 3.10.6 would force librosa 0.11 and block wgpu-py; it is not worth building on.

---

## 6. Expected performance envelope

`[ASSUMPTION-heavy section: these are engineering estimates, not measurements. Verify in the first prototype.]`

- Shader passes: a chain of 4-8 fullscreen fragment passes at 1080p is trivial for a GTX 1650 Ti and for any Apple Silicon GPU; this will not be the bottleneck.
- Camera: 30 fps capture on its own thread; the render loop runs at 60 fps and simply reuses the last frame when no new one has arrived.
- CPU side per frame: NumPy FFT on a 2048-sample window (<1 ms), optional DIS flow at 320x180 (estimate single-digit ms), optional MediaPipe selfie segmentation (~33 ms per Google's table [S39], so run it on its own thread at ~20-30 fps and interpolate).
- Python GIL: keep audio in the sounddevice callback (C-level buffer copy), camera and ML in threads that spend their time inside OpenCV/MediaPipe C++ (which release the GIL), and the render loop as the main thread. `[GENERAL KNOWLEDGE]`

---

## 7. Open questions and risks

1. **DIS optical flow and MediaPipe timings on this laptop** — RESOLVED, see Section 9 (all comfortably real-time). Mac timings still unmeasured.
2. **Which Mac?** — RESOLVED: M1-and-later plus MacBook Neo (A18 Pro), all arm64, so MediaPipe's arm64 wheel applies [S40][S46].
3. **Audio/visual sync tolerance.** The sounddevice timestamp approach should give sub-20 ms alignment [S3], but output-device latency on Bluetooth headphones can add 100-200 ms; a manual offset knob is needed. `[GENERAL KNOWLEDGE on Bluetooth latency]`
4. **OpenGL on future macOS.** Deprecated since 10.14 [S15]. If Apple removes it, the rendering layer moves to wgpu-py [S20] and shaders are ported from GLSL to WGSL; the rest of the architecture is unchanged.
5. **ShaderFlow licensing.** If any of its code or presets are reused, AGPL-3.0 applies unless sponsored [S22]. Studying its approach is fine; copying code has license implications.

---

## 8. Suggested next steps (technical spikes, in order)

1. Create a Python 3.12 venv; install `moderngl moderngl-window glfw opencv-python sounddevice soundfile librosa numpy`.
2. Spike A (1 file): open camera on a thread, upload to a moderngl texture, draw it through one fragment shader at 60 fps. Measure capture fps and upload cost on Windows; repeat on the Mac.
3. Spike B: play the song with `sounddevice.OutputStream`, print the song position from the callback clock, run librosa beat tracking offline, flash the screen on beats. Check sync by ear; add an offset knob.
4. Spike C: add ping-pong FBO feedback with decay/zoom and one onset-driven parameter. This is the first "trippy" frame.
5. Spike D (optional): DIS optical flow as a displacement texture; MediaPipe selfie mask as a second texture. Measure ms.
6. Then move to the creative design document: effect palette, section-by-section choreography of the track, colour system, and parameter mappings from audio features to shader uniforms.

---

## 9. Measured results (2026-10-07, Windows dev box)

Hands-on follow-up to Section 7 items 1 and 2. Scripts: `bench/bench_cv.py` (CV + GPU timings, writes `bench/bench_results.json`) and `bench/cam_probe.py` (webcam backend/mode probe with hard timeouts, writes `bench/cam_probe_results.json`).

**Environment used:** Python 3.12.15 (uv-managed, venv at `.venv`), opencv-contrib-python 5.0.0.93, mediapipe 1.1.0, moderngl 5.12.0, numpy 2.5.3. Hardware: AMD Ryzen 5 4600HS (6C/12T), NVIDIA GTX 1650 Ti, Logitech C270 webcam. Room was dark (tests ran around 01:30 local time), which matters for the webcam numbers.

**Mac target resolved:** the user's targets are M1-and-later Macs plus the MacBook Neo, which Apple lists with an A18 Pro chip (6-core CPU, 5-core GPU) [S46]. All of these are arm64, so the arm64-only MediaPipe wheel [S40] is not a problem. No Intel Mac support is needed.

### 9.1 Webcam (Logitech C270, OpenCV 5.0.0)

| Backend / mode | Result |
|---|---|
| MSMF open | 24 to 27 s, on every one of 4 opens. The stall from [S34] is real and repeatable on this camera. |
| MSMF 640x480 (first run) | Streamed at 30.7 fps, read() mean 32 ms. |
| MSMF, change resolution on an open capture | OpenCV 5.0.0 raised `cv::Mat` "_step >= minstep" assertion in read(). Always reopen the capture to change mode. |
| MSMF after that crash | Every read() timed out at 10 s. |
| DSHOW open | 0.7 to 1.4 s. |
| DSHOW 640x480 YUY2 | 15 fps, read() mean 66 ms. Consistent with the C270 halving its frame rate in low light. `[ASSUMPTION: low light is the cause; the C270 is documented by users to do this but it was not isolated here]` |
| DSHOW 1280x720 (any fourcc) | read() timed out at 1 s, black frames. The MJPG fourcc request was ignored in both set orders (always negotiated YUY2). 720p YUY2 at 30 fps exceeds USB 2.0 bandwidth, so MJPG is required for 720p on this camera. `[GENERAL KNOWLEDGE]` |
| CAP_PROP_BUFFERSIZE | set() returned False on both backends (DSHOW reads back -1). Confirms [S27]: use the latest-frame thread, do not rely on this property. |
| After the crash and two hard-killed processes | Camera wedged: every DSHOW mode returned black frames with 1 s timeouts, while PnP still reported the device as OK. Needs an unplug/replug (device restart needs an elevated shell). |

Design consequences: use `cv2.CAP_DSHOW` on Windows; open a fresh capture per mode change; wrap read() in a thread with a watchdog so a wedged camera cannot stall the render loop; always release() on exit, never hard-kill mid-stream; treat 640x480 as the safe default on this camera and keep 720p as an option to verify after a replug (open item: get DSHOW to honor MJPG on the C270, or test MSMF with MJPG).

**Round 2 (after a replug), what the camera actually supports.** The ffmpeg binary bundled with `imageio-ffmpeg` can enumerate DirectShow modes (`ffmpeg -list_options true -f dshow -i video="Logi C270 HD WebCam"`). The C270 reports, among others:

| Format | 640x480 | 1280x720 |
|---|---|---|
| Raw YUY2 (`yuyv422`) | up to 30 fps | **up to 7.5 fps** |
| MJPEG | up to 30 fps | up to 30 fps |

This explains the whole 720p failure: OpenCV's DirectShow backend ignored the `MJPG` fourcc request in both set orders and asked for 1280x720 YUY2 at 30 fps, a mode the camera does not have. The result is black frames with 1 s read timeouts, **and the camera then stays dead for every later process (OpenCV 5, OpenCV 4, and ffmpeg alike) until it is unplugged and replugged.** PnP keeps reporting the device as OK. Reproduced twice.

After the replug, DirectShow 640x480 delivered real frames (brightness 125, user visible) at 14 fps with auto-exposure reading -4. DirectShow exposure values are log2 seconds, so -4 is a 62 ms exposure, which by itself caps the camera near 16 fps `[GENERAL KNOWLEDGE: DirectShow exposure scale; the fps cap follows arithmetically]`. The room is lit but not brightly. The manual-exposure test (`bench/cam_exposure.py`) could not run because the camera had been wedged again by a 720p attempt in between; it is queued for the next replug.

**Rounds 3 and 4 (after a second replug): measured.**

| Test | Result |
|---|---|
| ffmpeg (bundled) DirectShow, MJPEG 640x480, auto exposure | **29.6 fps**, brightness 123, first frame after 3.0 s, frame gap p95 76 ms |
| ffmpeg DirectShow, MJPEG 1280x720, auto exposure | **20.2 fps**, brightness 125, first frame after 4.3 s, frame gap mean 50 ms p95 57 ms |
| ffmpeg MJPEG 1280x720 with manual exposure -5 set beforehand | 19.9 fps, so the 720p cap is not exposure |
| CPU decode of a 720p JPEG (cv2.imdecode, 34 to 45 KB) | 5.5 ms, so the 720p cap is not decoding either; it is the camera or USB delivery |
| OpenCV 5 DirectShow 640x480 YUY2, auto exposure | 14.8 fps, exposure reads -4 (62 ms) |
| same, manual exposure -5 / -6 / -7 | **29.6 / 29.7 / 28.7 fps**, brightness 88 / 60 / 37 (darker image, gain already at 192) |
| manual exposure persistence | the -5 set by OpenCV was still in effect in the next process (ffmpeg and a later OpenCV readback), until auto was restored |
| OpenCV 4.14 (venv `.venv-cv4`) DirectShow | identical to OpenCV 5: MJPG fourcc ignored, 640x480 YUY2 at 13.7 fps, 720p request delivered black frames and wedged the camera again. Not a 5.0 regression. |

Decisions for the camera layer:

1. **Capture through ffmpeg, not OpenCV's camera backends.** `bench/cam_ffmpeg.py` is the prototype: the `imageio-ffmpeg` binary requests MJPEG explicitly on DirectShow, decodes, and pipes raw BGR frames to Python. On this camera that gives a clean 30 fps at 640x480 or 20 fps at 1280x720, and the same script maps to AVFoundation on macOS, so the camera layer is one code path on both OSes. OpenCV stays for optical flow only. Startup is 2 to 4 s to first frame, so open the camera before the song starts.
2. **Default to 640x480 at 30 fps and upscale on the GPU**, with 720p at 20 fps as an option. For an effects-heavy visualizer the extra resolution matters less than a steady 30 fps feed into the feedback buffer. `[ASSUMPTION: a creative judgement, to be confirmed in the design phase.]`
3. **Set exposure manually when using OpenCV capture**, since auto exposure in normal room light halves the frame rate on the C270; through ffmpeg MJPEG at 480p this did not happen.
4. **Never request 1280x720 without MJPEG on this camera**, with any library; it kills the device until a replug.
5. The latency of the ffmpeg pipe was not measured directly (frame gaps were steady at 33 ms, with occasional 75 ms spikes at 480p); a camera-to-screen latency test with a visible timer is a cheap follow-up.

### 9.2 Audio math

| Test | Mean | p95 |
|---|---|---|
| NumPy rfft of a 2048-sample float32 window | 0.060 ms | 0.07 ms |

Negligible, as assumed in Section 2.

### 9.3 Dense optical flow (CPU, single frame pair, 30 iterations)

| Method | 320x180 | 640x360 | 1280x720 |
|---|---|---|---|
| DIS ULTRAFAST | 0.96 ms | 3.5 ms | 11.3 ms |
| DIS FAST | 3.8 ms | 8.5 ms | 22 ms |
| DIS MEDIUM | 8.3 ms | 21 ms | 69 ms |
| Farneback | 22 ms | 95 ms | not run |
| gray + resize 720p to 320x180 (preprocessing) | 1.6 ms | | |

DIS ULTRAFAST at 320x180 costs about 2.6 ms including preprocessing, well under the 5 ms assumed in Section 3.5. Even 640x360 ULTRAFAST fits a 60 fps budget. Farneback is 20x slower and not worth using.

### 9.4 MediaPipe Image Segmenter (CPU, XNNPACK, VIDEO mode)

The cost is dominated by the input and output size, not the model: MediaPipe resizes the mask back to the input resolution. Feeding a downscaled frame is the whole trick.

| Model / output | 1280x720 input | 640x360 | 320x180 | 256x256 |
|---|---|---|---|---|
| Selfie square, category mask | 138 ms | 44 ms | 20 ms | 21 ms |
| Selfie square, confidence mask | | | 12 ms | 12 ms |
| Selfie landscape, category mask | 134 ms | 38 ms | 15 ms | 16 ms |
| Selfie landscape, confidence mask | | | 7.2 ms | 7.5 ms |
| Selfie multiclass (hair/skin/clothes), category | 371 ms | | | 225 ms |
| Selfie multiclass, confidence | | | | 220 ms |
| resize to 320x180 + BGR to RGB + mp.Image wrap | 3.0 ms | | | |

Conclusion: selfie landscape with a float confidence mask at 320x180 input is about 10 ms per frame including preprocessing, so a person mask can be refreshed at the full 30 fps camera rate on a worker thread. The multiclass model is not real-time on this CPU; skip it or run it at a few fps for slowly changing hair/skin regions.

### 9.5 MediaPipe Pose Landmarker (CPU, VIDEO mode, one person in frame)

Measured on MediaPipe's own sample photo (a person), since the webcam was wedged. One pose with 33 landmarks was detected in every run.

| Model | 1280x720 | 1280x720 + mask | 640x360 | 640x360 + mask |
|---|---|---|---|---|
| Pose lite | 25.5 ms | 39 ms | 24.7 ms | 29.7 ms |
| Pose full | 40 ms | 51 ms | 37 ms | 41 ms |

Conclusion: pose lite at 640x360 runs at about 33 to 40 fps on its own thread, with or without the mask. Running both segmentation and pose concurrently fits in the 12 logical cores. On the earlier black-frame run (no person) the detector-only cost was 27 ms, so the cost does not collapse when nobody is in frame.

### 9.6 moderngl on the GTX 1650 Ti

The standalone context landed on the NVIDIA GPU, not the AMD iGPU, without any settings change (the Optimus concern in Section 4 did not materialize here; verify once with a real window).

| Test (1280x720 frame unless noted) | Mean | p95 |
|---|---|---|
| Texture.write of the OpenCV BGR array directly, no conversion | 1.46 ms | 1.6 ms |
| Texture.write RGBA from prebuilt bytes | 0.48 ms | 0.6 ms |
| cv2.cvtColor BGR to RGBA, then write | 2.8 ms | 3.1 ms |
| NumPy channel flip + ascontiguousarray + tobytes + write | 14.3 ms | 15.7 ms |
| 8 fullscreen feedback passes at 1920x1080 | 1.6 ms | 2.2 ms |
| 8 fullscreen feedback passes at 2560x1440 | 2.3 ms | 2.8 ms |
| FBO readback RGB 1920x1080 (for recording / Spout) | 3.2 ms | 3.7 ms |
| FBO readback RGB 2560x1440 | 5.4 ms | 5.8 ms |

Conclusions: upload the BGR frame as-is and swizzle `.bgr` in the shader (the NumPy flip path is 10x slower and must be avoided). Eight 1080p passes cost under 2 ms, so the shader chain can be far deeper than planned. Readback for recording is affordable at 1080p60.

### 9.7 Frame budget at 60 fps (16.7 ms), from measurements

| Thread | Work | Cost |
|---|---|---|
| Render (main) | upload 720p BGR + 8 passes 1080p + FFT + Python overhead | about 3.5 ms measured, leaves 13 ms headroom |
| Camera | read() at 30 fps | blocks on the driver, no CPU cost to the render loop |
| Flow | gray + resize + DIS ULTRAFAST 320x180 | 2.6 ms per camera frame |
| Mask | resize + selfie landscape confidence 320x180 | about 10 ms per camera frame |
| Pose (optional) | pose lite 640x360 + mask | about 30 ms, so 30 fps max |
| Record (optional) | FBO readback 1080p + pipe to ffmpeg | 3.2 ms + pipe cost (not measured) |

Everything fits on this laptop with margin. `[ASSUMPTION: the Apple Silicon targets will be in the same range. M1 and later have faster single cores than this Ryzen; the MacBook Neo's A18 Pro has 2 performance + 4 efficiency cores [S46], so the CPU-side ML (segmentation, pose) is the part to re-measure first on that machine. The GPU side is so cheap here that any Apple GPU will be fine.]`

### 9.9 GPU ASCII pass (the ASCILINE look, measured)

`bench/ascii_shader_demo.py` builds a 95-glyph atlas by rendering printable ASCII with OpenCV's Hershey font and sorting by measured ink coverage (the ramp comes out as `' .`,'^:-;~"_*i!l=rI></\+j()vt|f{}L?7sx][cTzyYun1oeakFVhJm9642CX3UZKbPpq5dAESw#HOG$%DgQR8N&0WBM@'`), then renders the captured 640x480 camera frame as ASCII at 1920x1080 in one fragment pass. Columns, ramp length, colour bit depth, gamma, invert, mirror and pixel mode are all uniforms.

| Variant | Cost per frame (GTX 1650 Ti) |
|---|---|
| 240 columns, 93 levels, full colour | 0.30 ms mean, 0.43 ms p95 |
| 480 columns, 93 levels, full colour | 0.26 ms mean, 0.44 ms p95 |

Output images: `bench/ascii_demo_*.png` (six variants: 240 columns at 16M and 64 colours, a 10-level ramp at 512 colours, a 6-level inverted ramp at 120 columns, 480 columns, and pixel mode). The cost is independent of grid density, as expected for a per-pixel shader. For comparison, ASCILINE's own README targets 24 to 30 fps at 200 to 240 columns on the CPU [S47].

### 9.8 Open items after this round

1. ~~Replug the C270 and rerun the probe~~ DONE, see the rounds 3 and 4 table in 9.1: ffmpeg MJPEG gives 30 fps at 480p and 20 fps at 720p; OpenCV cannot negotiate MJPEG on this camera.
2. Measure `bench/bench_cv.py` and `bench/cam_ffmpeg.py` on an Apple Silicon Mac (and on the MacBook Neo if available). Camera backend there is AVFoundation.
3. Pipe-to-ffmpeg recording cost and camera-to-screen latency are unmeasured.
4. The dev box camera is wedged again after the final OpenCV 4 test; replug before the next camera session.

**Camera test scripts kept in `bench/`:** `cam_ffmpeg.py` (ffmpeg capture, `--list` enumerates devices), `cam_exposure.py` (exposure vs fps at 640x480, safe), `cam_probe.py` (OpenCV backend probe, stops at the first dead mode; `--allow-720p-yuy2` re-enables the mode that wedges the C270).

---

## Sources

Tier: **P** = primary (official docs, PyPI, source code), **E** = established secondary, **U** = unverified secondary (forums, blogs).

- [S1] P - soundfile on PyPI (0.14.0, MP3 support, bundled libsndfile wheels): https://pypi.org/project/soundfile/
- [S2] P - python-sounddevice installation docs (PortAudio bundled on macOS/Windows): https://python-sounddevice.readthedocs.io/en/latest/installation.html
- [S2b] P - sounddevice on PyPI (0.5.6, wheels): https://pypi.org/project/sounddevice/
- [S3] P - python-sounddevice Streams API (callback `time` fields, `Stream.time`, `latency`): https://python-sounddevice.readthedocs.io/en/latest/api/streams.html
- [S4] P - librosa `beat_track` docs (three-stage algorithm, precomputed onset envelope, hop_length): https://librosa.org/doc/main/generated/librosa.beat.beat_track.html and https://librosa.org/doc/main/generated/librosa.onset.onset_detect.html
- [S5] P - librosa on PyPI (1.0.0, Python >=3.12): https://pypi.org/project/librosa/
- [S6] P - Beat This! (CPJKU) repository/readme (pip install beat-this, PyTorch 2.0+): https://github.com/CPJKU/beat_this
- [S7] P - opencv-python on PyPI (5.0.0.93, Python 3.7-3.14, platform wheels): https://pypi.org/project/opencv-python/
- [S8] P - OpenCV videoio.hpp source (CAP_DSHOW, CAP_MSMF, CAP_AVFOUNDATION, CAP_PROP_BUFFERSIZE, VideoCapture(index, apiPreference)): https://raw.githubusercontent.com/opencv/opencv/4.x/modules/videoio/include/opencv2/videoio.hpp
- [S9] P - OpenCV tracking.hpp source (DISOpticalFlow presets and speed statement): https://raw.githubusercontent.com/opencv/opencv/4.x/modules/video/include/opencv2/video/tracking.hpp
- [S10] P - MediaPipe Image Segmenter Python guide (IMAGE/VIDEO/LIVE_STREAM, mask types): https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter/python
- [S11] P - MediaPipe Pose Landmarker Python guide (33 landmarks, output_segmentation_masks, LIVE_STREAM): https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker/python
- [S12] P - moderngl on PyPI (5.12.0, OpenGL 3.3 / compute 4.3, wheels): https://pypi.org/project/moderngl/
- [S13] P - moderngl-window on PyPI (3.1.1, Python >=3.9, window backends): https://pypi.org/project/moderngl-window/ and https://moderngl-window.readthedocs.io/en/latest/guide/window_guide.html
- [S14] P - glfw on PyPI (2.10.2, bundled shared library on Windows/macOS): https://pypi.org/project/glfw/
- [S15] E - macOS OpenGL 4.1 ceiling and deprecation: https://gitlab.kitware.com/vtk/vtk/-/issues/19291 ; https://geeks3d.com/20180605/apple-announces-that-opengl-and-opencl-will-be-deprecated-in-macos-10-14-mojave ; https://www.mathworks.com/matlabcentral/answers/328640-opengl-version-on-macos
- [S16] P - imageio-ffmpeg on PyPI (bundled ffmpeg binary, write_frames): https://pypi.org/project/imageio-ffmpeg/
- [S17] P - turbopipe on PyPI (moderngl Buffer to ffmpeg pipe): https://pypi.org/project/turbopipe/
- [S18] P - SpoutGL on PyPI / GitHub (Windows, Python 3.8+): https://pypi.org/project/SpoutGL/ ; https://github.com/jlai/Python-SpoutGL
- [S19] P - syphon-python on PyPI (macOS 11+, PyObjC): https://pypi.org/project/syphon-python
- [S20] P - wgpu on PyPI (0.32.0, Python >=3.11, backends, API stability note): https://pypi.org/project/wgpu/
- [S21] P - taichi on PyPI (1.7.4, Python 3.9-3.11, backends): https://pypi.org/project/taichi/
- [S22] P - ShaderFlow on GitHub / PyPI (audio reactive, video as texture, AGPL-3.0 + sponsorship): https://github.com/BrokenSource/ShaderFlow ; https://pypi.org/project/shaderflow
- [S23] P - py5 on PyPI (Java 17 requirement) and Processing forum thread on video capture: https://pypi.org/project/py5/ ; https://discourse.processing.org/t/video-capture-with-py5/33191
- [S24] P - aubio on PyPI (0.4.9, 2019, sdist only): https://pypi.org/project/aubio/
- [S25] P - madmom on PyPI (0.16.1, 2018): https://pypi.org/project/madmom/
- [S26] P - rtmixer on PyPI (C callback avoiding GIL/GC): https://www.pypi.org/project/rtmixer/0.1.1/
- [S27] U - OpenCV forum threads on VideoCapture buffering lag and CAP_PROP_BUFFERSIZE support: https://forum.opencv.org/t/delay-in-videocapture-because-of-buffer/2755 ; https://answers.opencv.org/question/183162/delay-in-frame-grabbing-with-opencv3-and-ubuntu-1604
- [S28] E - Chalmers TDA361 lab on ping-pong FBOs; Khronos forum on FBO feedback: https://www.cse.chalmers.se/edu/course.2016/TDA361/tutorials/lab5.html ; https://community.khronos.org/t/feedback-effect-using-fbo-textures-and-shaders/65639
- [S29] E - Inigo Quilez, feedback effects article: https://iquilezles.org/articles/feedbackfx/
- [S30] P - librosa I/O formats docs (uses soundfile): https://librosa.org/doc/latest/ioformats.html
- [S31] P - librosa changelog v1.0.0 (audioread backend removed): https://librosa.org/doc/latest/changelog.html
- [S32] P - PyAudio on PyPI / homepage (Windows wheels for 3.12/3.13, macOS via Homebrew): https://pypi.org/project/PyAudio/ ; https://people.csail.mit.edu/hubert/pyaudio/
- [S33] P - librosa 0.11.0 setup.cfg (python_requires >=3.8, audioread dependency): https://raw.githubusercontent.com/librosa/librosa/0.11.0/setup.cfg
- [S34] U - Blog on Windows MSMF stall and CAP_DSHOW workaround: https://www.technetexperts.com/python-webcam-capture-fixes/amp/
- [S35] U - OpenCV forum / Wing IDE Q&A on macOS "not authorized to capture video" and permission prompt: https://forum.opencv.org/t/opencv-not-authorized-to-capture-video-status-0-requesting/8512 ; https://ask.wingware.com/question/3289/how-to-authorize-the-camera-on-macbook/
- [S36] U - ffmpeg trac ticket on NSCameraUseContinuityCameraDeviceType warning: https://trac.ffmpeg.org/ticket/11001
- [S37] P - moderngl Texture reference (write/read/use): https://moderngl.readthedocs.io/en/latest/reference/texture.html
- [S38] E - LearnOpenCV optical flow overview (Farneback cost statement): https://learnopencv.com/optical-flow-in-opencv/
- [S39] P - MediaPipe Image Segmenter overview (models and latency table): https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter
- [S40] P - mediapipe on PyPI (1.1.0, Python 3.9-3.14, wheel list): https://pypi.org/project/mediapipe/
- [S41] P - MediaPipe v1.0.0 release notes ("Remove MacOS prebuilts"): https://github.com/google-ai-edge/mediapipe/releases/tag/v1.0.0
- [S42] P - moderngl context docs (macOS backend CGL, require= parameter): https://moderngl.readthedocs.io/en/latest/topics/context.html
- [S43] E - Resolume docs on Syphon and Spout inputs: https://resolume.com/article/30
- [S44] P - PyInstaller docs / repo (not a cross-compiler, macOS .app bundles, code signing): https://github.com/pyinstaller/pyinstaller/ ; https://pyinstaller.org/
- [S45] P - NVIDIA Optimus developer guide (display device is the integrated GPU; apps must select the NVIDIA device): https://docs.nvidia.com/cuda/optimus-developer-guide/index.html
- [S46] P - Apple, MacBook Neo technical specifications (A18 Pro, 6-core CPU, 5-core GPU): https://www.apple.com/macbook-neo/specs/
- [S47] P - ASCILINE repository and README (modes, webcam flags, audio sync, split AGPLv3/MIT licence): https://github.com/YusufB5/ASCILINE

Gaps not covered in this pass: no hands-on benchmarks were run; Apple's own documentation on OpenGL deprecation was not fetched directly (secondary sources used); per-backend support of CAP_PROP_BUFFERSIZE is forum-sourced only; the exact codec list of the bundled libsndfile was not enumerated.
