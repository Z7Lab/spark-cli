"""Deterministic image-compositing verbs (the general layer under image playbooks).

Pure PIL, no numpy. Each function takes explicit pixel parameters and is fully
reproducible — semantic addressing ("the phone", "the badges") is the caller's job
(inspect the image, supply coords), which keeps these verbs pure and unit-testable.

Pillow is an optional dependency: this module is imported lazily by `spark image`,
which prints an install hint if PIL is missing rather than failing at startup.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw


def bg_color(im, strip: int = 50):
    """Background colour: the average of a `strip`-px band along the bottom edge
    (the empty region in a typical hero/landing composite)."""
    w, h = im.size
    return im.crop((0, h - strip, w, h)).resize((1, 1)).getpixel((0, 0))


def _mask(im, bg, threshold: int):
    """Binary mask (L, 0/255) of pixels whose max-channel difference from `bg`
    exceeds `threshold`."""
    diff = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, bg))
    r, g, b = diff.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return mx.point(lambda p: 255 if p > threshold else 0)


def detect_region(im, search_box=None, threshold: int = 26, exclude_top: int = 0,
                  min_run: int = 15):
    """Bounding box of the dominant foreground object inside `search_box`.

    Profiles the difference-from-background mask by row and column and returns the
    span of the densest contiguous band — what a caller uses to locate "the phone"
    before moving it. Returns (x0, y0, x1, y1) in full-image coordinates, or None.
    """
    im = im.convert("RGB")
    W, H = im.size
    bg = bg_color(im)
    mask = _mask(im, bg, threshold)
    x0s, y0s, x1s, y1s = (search_box or (0, 0, W, H))
    y0s = max(y0s, exclude_top)

    # row profile within the search band
    def row_count(y):
        return sum(mask.crop((x0s, y, x1s, y + 1)).getdata()) // 255

    rows = [y for y in range(y0s, y1s) if row_count(y) > min_run]
    if not rows:
        return None
    # densest contiguous vertical segment
    segs, start = [], None
    for y in range(y0s, y1s):
        if row_count(y) > min_run:
            start = y if start is None else start
        elif start is not None:
            segs.append((start, y - 1)); start = None
    if start is not None:
        segs.append((start, y1s - 1))
    top, bottom = max(segs, key=lambda s: s[1] - s[0])

    def col_count(x):
        return sum(mask.crop((x, top, x + 1, bottom)).getdata()) // 255
    cols = [x for x in range(x0s, x1s) if col_count(x) > min_run]
    if not cols:
        return None
    return (cols[0], top, cols[-1] + 1, bottom + 1)


def extract_asset(im, bbox):
    """Crop `bbox` (x0, y0, x1, y1) out of the image."""
    return im.convert("RGB").crop(tuple(bbox))


def move_region(im, bbox, dx: int = 0, dy: int = 0, bg=None, clear_pad: int = 0):
    """Relocate the `bbox` region by (dx, dy), background-filling the vacated source.

    `bg` defaults to the auto-detected background colour. `clear_pad` widens the
    erased source rectangle (useful when a thin element border sits just outside the
    detected bbox). Returns a new image.
    """
    im = im.convert("RGB")
    bg = bg or bg_color(im)
    region = im.crop(tuple(bbox))
    out = im.copy()
    x0, y0, x1, y1 = bbox
    ImageDraw.Draw(out).rectangle(
        (x0 - clear_pad, y0 - clear_pad, x1 + clear_pad, y1 + clear_pad), fill=bg)
    out.paste(region, (x0 + dx, y0 + dy))
    return out


def overlay_centered(im, assets, y: int, scale: float = 1.0, gap: int = 24,
                     anchor_x=None):
    """Paste `assets` (PIL Images) as a horizontally-centered group at row `y`.

    `scale` resizes each asset; `gap` is the spacing between them; `anchor_x`
    overrides the centre (defaults to image centre). Returns a new image.
    """
    im = im.convert("RGB")
    W = im.size[0]
    scaled = []
    for a in assets:
        a = a.convert("RGB")
        if scale != 1.0:
            a = a.resize((max(1, round(a.size[0] * scale)), max(1, round(a.size[1] * scale))))
        scaled.append(a)
    total = sum(a.size[0] for a in scaled) + gap * (len(scaled) - 1)
    cx = anchor_x if anchor_x is not None else W // 2
    x = cx - total // 2
    out = im.copy()
    for a in scaled:
        out.paste(a, (x, y))
        x += a.size[0] + gap
    return out
