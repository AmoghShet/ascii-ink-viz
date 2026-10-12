"""
GPU ASCII pass proof-of-concept (clean-room; no ASCILINE code).

Builds a glyph atlas by rendering every printable ASCII character with OpenCV's Hershey font,
sorts the glyphs by measured ink coverage (that IS the luminance ramp), uploads the atlas and a
camera frame as textures, and renders the ASCII look in ONE fragment shader pass.
Everything ASCILINE exposes as a mode is a uniform here: columns, ramp length, colour bit depth,
gamma, invert, mirror, pixel mode.

Usage: python ascii_shader_demo.py [--frame path.jpg] [--out-dir bench/] [--iters 100]
"""
import argparse
import os
import time

import cv2
import moderngl
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

VS = """
#version 330
in vec2 in_pos; out vec2 uv;
void main(){ uv = in_pos*0.5+0.5; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

FS = """
#version 330
uniform sampler2D cam;      // camera frame uploaded as raw BGR bytes
uniform sampler2D atlas;    // R8, n_glyphs glyphs side by side, ordered dark -> bright
uniform vec2  grid;         // columns, rows
uniform float n_glyphs;
uniform float levels;       // how many luminance levels to use (<= n_glyphs); 1 = pixel mode
uniform float qshift;       // colour bits dropped per channel: 0 = 16M colours, 6 = 64 colours
uniform float gamma_;
uniform float invert;
uniform float mirror;
uniform float ink_floor;    // 0 = pure glyphs on black; >0 lets a dim cell colour show through
in vec2 uv; out vec4 f;

void main(){
    vec2 u = uv;
    if (mirror > 0.5) u.x = 1.0 - u.x;
    vec2 cell   = floor(u * grid);
    vec2 inCell = fract(u * grid);
    vec2 cuv    = (cell + 0.5) / grid;              // sample the camera once per cell, at its centre
    vec3 c      = texture(cam, vec2(cuv.x, 1.0 - cuv.y)).bgr;
    float lum   = dot(c, vec3(0.299, 0.587, 0.114));
    lum = pow(lum, 1.0 / gamma_);
    if (invert > 0.5) lum = 1.0 - lum;

    float ink;
    if (levels <= 1.5) {
        ink = 1.0;                                   // pixel mode: solid cell
    } else {
        float lv = floor(lum * (levels - 1.0) + 0.5) / (levels - 1.0);
        float gi = floor(lv * (n_glyphs - 1.0) + 0.5);
        vec2 auv = vec2((gi + inCell.x) / n_glyphs, 1.0 - inCell.y);
        ink = texture(atlas, auv).r;
    }
    float q  = exp2(qshift);
    vec3 cq  = floor(c * 255.0 / q) * q / 255.0;     // ASCILINE-style bit-shift colour quantisation
    f = vec4(cq * max(ink, ink_floor), 1.0);
}
"""


def build_atlas(cell_w=16, cell_h=32, font=cv2.FONT_HERSHEY_SIMPLEX, scale=0.75, thickness=1):
    """Render printable ASCII 32..126, measure ink, sort ascending. Returns (atlas uint8 [cell_h, n*cell_w], ramp str)."""
    chars = [chr(c) for c in range(32, 127)]
    glyphs = []
    for ch in chars:
        img = np.zeros((cell_h, cell_w), np.uint8)
        (tw, th), base = cv2.getTextSize(ch, font, scale, thickness)
        x = max(0, (cell_w - tw) // 2)
        y = (cell_h + th) // 2
        cv2.putText(img, ch, (x, y), font, scale, 255, thickness, cv2.LINE_AA)
        glyphs.append((float(img.mean()), ch, img))
    glyphs.sort(key=lambda g: g[0])
    atlas = np.concatenate([g[2] for g in glyphs], axis=1)
    ramp = "".join(g[1] for g in glyphs)
    return atlas, ramp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default=os.path.join(HERE, "last_cv4_frame.jpg"))
    ap.add_argument("--out-dir", default=HERE)
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    args = ap.parse_args()

    frame = cv2.imread(args.frame)
    if frame is None:
        raise SystemExit(f"could not read {args.frame}")
    fh, fw = frame.shape[:2]

    atlas, ramp = build_atlas()
    n_glyphs = len(ramp)
    print(f"atlas: {atlas.shape[1]}x{atlas.shape[0]} px, {n_glyphs} glyphs")
    print(f"measured ramp (dark -> bright): {ramp!r}")
    cv2.imwrite(os.path.join(args.out_dir, "ascii_atlas.png"), atlas)

    ctx = moderngl.create_standalone_context()
    print("GL_RENDERER:", ctx.info["GL_RENDERER"])
    cam = ctx.texture((fw, fh), 3, data=np.ascontiguousarray(frame).tobytes())
    cam.filter = (moderngl.LINEAR, moderngl.LINEAR)
    at = ctx.texture((atlas.shape[1], atlas.shape[0]), 1, data=atlas.tobytes())
    at.filter = (moderngl.LINEAR, moderngl.LINEAR)
    prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
    quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
    vao = ctx.vertex_array(prog, [(quad, "2f", "in_pos")])
    W, H = args.width, args.height
    fbo = ctx.framebuffer(color_attachments=[ctx.texture((W, H), 4)])
    prog["cam"].value = 0
    prog["atlas"].value = 1
    prog["n_glyphs"].value = float(n_glyphs)

    def render(cols, levels, qshift, gamma=1.0, invert=0.0, mirror=1.0, ink_floor=0.0):
        cell_w = W / cols
        rows = max(1, int(round(H / (cell_w * 2.0))))   # 1:2 cell aspect, like a terminal
        prog["grid"].value = (float(cols), float(rows))
        prog["levels"].value = float(levels)
        prog["qshift"].value = float(qshift)
        prog["gamma_"].value = float(gamma)
        prog["invert"].value = float(invert)
        prog["mirror"].value = float(mirror)
        prog["ink_floor"].value = float(ink_floor)
        fbo.use()
        cam.use(0)
        at.use(1)
        vao.render(moderngl.TRIANGLE_STRIP)

    def readback():
        img = np.frombuffer(fbo.read(components=3), np.uint8).reshape(H, W, 3)
        return np.flipud(img)[:, :, ::-1].copy()   # GL is bottom-up and we uploaded BGR (shader swizzled to RGB)

    variants = [
        ("ascii_demo_240cols_93lv_16M.png",   dict(cols=240, levels=93, qshift=0)),
        ("ascii_demo_240cols_93lv_64col.png", dict(cols=240, levels=93, qshift=6)),
        ("ascii_demo_240cols_10lv_512col.png", dict(cols=240, levels=10, qshift=5)),
        ("ascii_demo_120cols_6lv_invert.png", dict(cols=120, levels=6, qshift=0, invert=1.0, gamma=1.4)),
        ("ascii_demo_480cols_93lv.png",       dict(cols=480, levels=93, qshift=0)),
        ("ascii_demo_pixelmode_160cols.png",  dict(cols=160, levels=1, qshift=3)),
    ]
    for name, kw in variants:
        render(**kw)
        ctx.finish()
        cv2.imwrite(os.path.join(args.out_dir, name), readback())
        print("wrote", name)

    # timing: the pass alone, at 240 columns and at 480 columns
    for cols in (240, 480):
        for _ in range(5):
            render(cols=cols, levels=93, qshift=0)
        ctx.finish()
        ts = []
        for _ in range(args.iters):
            t0 = time.perf_counter()
            render(cols=cols, levels=93, qshift=0)
            ctx.finish()
            ts.append((time.perf_counter() - t0) * 1000.0)
        print(f"ASCII pass @ {W}x{H}, {cols} cols: mean={np.mean(ts):.3f} ms  p95={np.percentile(ts, 95):.3f} ms")
    ctx.release()


if __name__ == "__main__":
    main()
