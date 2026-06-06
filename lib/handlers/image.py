"""image handlers — deterministic local image-compositing verbs (require Pillow)."""

from __future__ import annotations

import sys
from pathlib import Path

from sparkcore import REPO_ROOT, dim, red, cyan, ok, fail


def _bbox(s: str):
    return tuple(int(x) for x in s.split(","))


def _load(params):
    """Import imgops + Pillow and open the input image as RGB.

    Exits with the install hint if Pillow is missing — the one place the image
    verbs touch the optional dependency.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT / "lib"))
        import imgops
        from PIL import Image
    except ImportError:
        print(fail("spark image requires Pillow:  pip install Pillow")); sys.exit(1)
    im = Image.open(Path(params["image"]).expanduser()).convert("RGB")
    return imgops, Image, im


def detect_region(params, cfg):
    """Print the bounding box of the main object in an image."""
    imgops, _Image, im = _load(params)
    sb = params["search_box"]
    bbox = imgops.detect_region(
        im, search_box=_bbox(sb) if sb else None,
        threshold=params["threshold"],
        exclude_top=params["exclude_top"])
    if not bbox:
        print(fail("No region detected — adjust --search-box/--threshold.")); sys.exit(1)
    print(",".join(str(v) for v in bbox))
    print(dim(f"  size {bbox[2]-bbox[0]}x{bbox[3]-bbox[1]}  (feed to move-region/extract-asset)"))
    return {"action": "image.detect-region", "bbox": list(bbox),
            "width": bbox[2] - bbox[0], "height": bbox[3] - bbox[1]}


def extract_asset(params, cfg):
    """Crop a sub-image (e.g. a badge) out of an image."""
    imgops, _Image, im = _load(params)
    bbox = _bbox(params["bbox"])
    out = params["out"]
    imgops.extract_asset(im, bbox).save(out)
    print(ok(f"Saved {cyan(out)}  {dim(f'({bbox[2]-bbox[0]}x{bbox[3]-bbox[1]})')}"))
    return {"action": "image.extract-asset", "out": out, "bbox": list(bbox)}


def move_region(params, cfg):
    """Relocate a region and background-fill the source."""
    imgops, _Image, im = _load(params)
    bbox = _bbox(params["bbox"])
    bgs = params["bg"]
    bg = None if bgs == "auto" else tuple(int(x) for x in bgs.split(","))
    dx, dy = params["dx"], params["dy"]
    res = imgops.move_region(im, bbox, dx=dx, dy=dy, bg=bg,
                             clear_pad=params["clear_pad"])
    out = params["out"]
    res.save(out)
    print(ok(f"Saved {cyan(out)}  {dim(f'(moved dx={dx} dy={dy})')}"))
    return {"action": "image.move-region", "out": out, "bbox": list(bbox),
            "dx": dx, "dy": dy}


def overlay_centered(params, cfg):
    """Paste assets as a centered group onto an image."""
    imgops, Image, im = _load(params)
    assets = [Image.open(Path(p).expanduser()) for p in params["assets"].split(",") if p]
    if not assets:
        print(red("overlay-centered needs --assets")); sys.exit(1)
    y = params["y"]
    res = imgops.overlay_centered(
        im, assets, y=y, scale=params["scale"],
        gap=params["gap"], anchor_x=params["anchor_x"])
    out = params["out"]
    res.save(out)
    print(ok(f"Saved {cyan(out)}  {dim(f'({len(assets)} asset(s) centered at y={y})')}"))
    return {"action": "image.overlay-centered", "out": out,
            "assets": len(assets), "y": y}


HANDLERS = {
    "image.detect_region":    detect_region,
    "image.extract_asset":    extract_asset,
    "image.move_region":      move_region,
    "image.overlay_centered": overlay_centered,
}
