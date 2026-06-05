"""Deterministic unit tests for the image-compositing verbs.

Run: python3 -m pytest tests/test_imgops.py   (or: python3 tests/test_imgops.py)
Skips cleanly if Pillow isn't installed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

try:
    from PIL import Image
    import imgops
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class TestSparkImg(unittest.TestCase):
    def _canvas(self, color=(30, 34, 45), size=(200, 200)):
        return Image.new("RGB", size, color)

    def test_bg_color(self):
        im = self._canvas((10, 20, 30))
        self.assertEqual(imgops.bg_color(im), (10, 20, 30))

    def test_extract_asset(self):
        im = self._canvas()
        im.paste((255, 0, 0), (50, 50, 90, 90))
        crop = imgops.extract_asset(im, (50, 50, 90, 90))
        self.assertEqual(crop.size, (40, 40))
        self.assertEqual(crop.getpixel((0, 0)), (255, 0, 0))

    def test_move_region_relocates_and_fills(self):
        bg = (30, 34, 45)
        im = self._canvas(bg)
        im.paste((200, 100, 0), (40, 40, 80, 80))          # a square at y=40
        out = imgops.move_region(im, (40, 40, 80, 80), dy=60)
        # old location is background again
        self.assertEqual(out.getpixel((60, 60)), bg)
        # square now 60px lower
        self.assertEqual(out.getpixel((60, 120)), (200, 100, 0))

    def test_overlay_centered_places_asset_at_center(self):
        im = self._canvas(size=(200, 200))
        asset = Image.new("RGB", (40, 20), (0, 255, 0))
        out = imgops.overlay_centered(im, [asset], y=10)
        # single 40px asset centered in 200px → x 80..120
        self.assertEqual(out.getpixel((100, 15)), (0, 255, 0))
        self.assertEqual(out.getpixel((50, 15)), (30, 34, 45))   # left of it = bg

    def test_overlay_centered_two_assets_symmetric(self):
        im = self._canvas(size=(200, 200))
        a = Image.new("RGB", (30, 20), (255, 0, 0))
        b = Image.new("RGB", (30, 20), (0, 0, 255))
        out = imgops.overlay_centered(im, [a, b], y=10, gap=20)
        # pair width = 30+20+30 = 80, centered → starts at x=60
        self.assertEqual(out.getpixel((60, 15)), (255, 0, 0))    # start of a
        self.assertEqual(out.getpixel((130, 15)), (0, 0, 255))   # within b (60+30+20=110..140)

    def test_detect_region_finds_bright_square(self):
        im = self._canvas((30, 34, 45), size=(300, 300))
        im.paste((255, 255, 255), (100, 120, 180, 200))
        bbox = imgops.detect_region(im, min_run=5)
        self.assertIsNotNone(bbox)
        x0, y0, x1, y1 = bbox
        # within a couple px of the painted square
        self.assertTrue(abs(x0 - 100) <= 3 and abs(y0 - 120) <= 3)
        self.assertTrue(abs(x1 - 180) <= 3 and abs(y1 - 200) <= 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
