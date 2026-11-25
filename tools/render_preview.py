#!/usr/bin/env python3
"""Render the scene to a GIF and/or PNG without a terminal (needs Pillow).

    python tools/render_preview.py --gif docs/preview.gif --png docs/preview.png
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..'))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import winter  # noqa: E402

FONTS = [
    "C:/Windows/Fonts/consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "DejaVuSansMono.ttf",
]


def load_font(size):
    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("No monospace font found; edit FONTS in this script.")


def draw_frame(scene, font, cw, chh, fps):
    w, h = scene.w, scene.h
    ch, co = scene.render()
    img = Image.new('RGB', (w * cw, (h + 1) * chh), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(h):
        base = y * w
        for x in range(w):
            c = ch[base + x]
            if c != ' ':
                k = co[base + x]
                d.text((x * cw, y * chh), c, font=font,
                       fill=(k >> 16, (k >> 8) & 255, k & 255))
    d.text((0, h * chh), scene.status(fps)[:w], font=font,
           fill=(128, 136, 156))
    return img


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cols', type=int, default=132)
    p.add_argument('--rows', type=int, default=42)
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--font-size', type=int, default=13)
    p.add_argument('--fps', type=float, default=12.5)
    p.add_argument('--seconds', type=float, default=5.0)
    p.add_argument('--warmup', type=float, default=10.0)
    p.add_argument('--no-moon', action='store_true')
    p.add_argument('--no-glow', action='store_true')
    p.add_argument('--snow', type=float, default=1.0)
    p.add_argument('--fire', type=float, default=1.0)
    p.add_argument('--wind', type=float, default=0.3)
    p.add_argument('--help-overlay', action='store_true')
    p.add_argument('--gif')
    p.add_argument('--png')
    a = p.parse_args()

    st = winter.Settings()
    st.moon = not a.no_moon
    st.glow = not a.no_glow
    st.snow, st.fire, st.wind = a.snow, a.fire, a.wind
    scene = winter.Scene(a.cols, a.rows - 1, a.seed, st)
    scene.show_help = a.help_overlay
    for _ in range(int(a.warmup * 25)):
        scene.step(1 / 25.0)

    font = load_font(a.font_size)
    box = font.getbbox('M')
    cw = int(round(font.getlength('M')))
    chh = int(round((box[3] - box[1]) * 1.75))

    if a.png:
        draw_frame(scene, font, cw, chh, 25).save(a.png)
        print("wrote", a.png)
    if a.gif:
        frames = []
        for _ in range(int(a.seconds * a.fps)):
            scene.step(1.0 / a.fps)
            img = draw_frame(scene, font, cw, chh, 25)
            frames.append(img.quantize(colors=128, dither=Image.Dither.NONE))
        frames[0].save(a.gif, save_all=True, append_images=frames[1:],
                       duration=int(1000 / a.fps), loop=0, optimize=True)
        print("wrote", a.gif, os.path.getsize(a.gif) // 1024, "KB")


if __name__ == '__main__':
    main()
