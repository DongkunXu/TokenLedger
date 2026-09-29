"""Make the dashboard background: colour-grade a photo towards the dashboard palette
(orange + blue) and write it to frontend/src/assets/bg.jpg.

    source scripts/env.sh   # from bash, keeps uv's cache inside the project
    uv run --no-project --with pillow --with numpy scripts/grade-background.py <photo.jpg>

The current background comes from the Unsplash photo
mohammad-alizade-XgeZu2jBaVI-unsplash.jpg (6000x4000). The output is scaled to 2400 px
on the long side; the page darkens or lightens it per theme in CSS.

Hue-selective, in HSV:
* pink / magenta turn towards warm coral and lose most of their saturation
* violet moves towards blue
* salmon moves a little towards orange
* cyan / blue are pulled towards the Codex blue
The hue map is piecewise linear and continuous, so there is no banding at the edges
of each range.
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "assets" / "bg.jpg"
LONG_SIDE = 2400

# source hue -> target hue (degrees). Targets above 360 wrap around.
HUE_MAP = [
    (0, 16), (30, 26), (60, 42), (120, 150), (170, 190), (200, 197), (230, 204),
    (262, 212), (290, 236), (315, 352), (345, 368), (360, 376),
]
# saturation multiplier by source hue
SAT_MAP = [(0, 1.22), (30, 1.18), (160, 1.0), (200, 1.0), (250, 0.8), (285, 0.45), (315, 0.4), (345, 0.75), (360, 1.22)]


def interp(table, x):
    xs, ys = zip(*table)
    return np.interp(x, xs, ys)


def grade(src: Path, dst: Path) -> None:
    img = Image.open(src).convert("RGB")
    img.thumbnail((LONG_SIDE, LONG_SIDE), Image.LANCZOS)
    hsv = np.asarray(img.convert("HSV")).astype(np.float32)
    h = hsv[..., 0] * 360 / 255
    s = hsv[..., 1] / 255
    v = hsv[..., 2] / 255
    h2 = interp(HUE_MAP, h) % 360
    s2 = np.clip(s * interp(SAT_MAP, h) * 0.92, 0, 1)
    v2 = np.clip(v, 0, 1) ** 1.08  # a touch darker in the mids
    out = np.stack([h2 * 255 / 360, s2 * 255, v2 * 255], axis=-1).round().astype(np.uint8)
    Image.fromarray(out, "HSV").convert("RGB").save(dst, quality=80, optimize=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    grade(Path(sys.argv[1]).expanduser(), OUT)
    print(f"wrote {OUT}")
