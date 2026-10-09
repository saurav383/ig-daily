"""Stitch the dry-run cards into one image for review."""
import glob
import os

from PIL import Image

files = sorted(glob.glob("out/*.jpg"))
files = [f for f in files if not f.endswith("_review.jpg")]
if not files:
    raise SystemExit("no cards in out/")

thumb = 520
sheet = Image.new("RGB", (thumb * len(files), thumb), "#000000")
for i, p in enumerate(files):
    im = Image.open(p).resize((thumb, thumb))
    sheet.paste(im, (i * thumb, 0))
    print(f"  {os.path.getsize(p) // 1024:>4} KB  {os.path.basename(p)}")

sheet.save("out/_review.jpg", quality=90)
print(f"\n  contact sheet: out/_review.jpg ({len(files)} cards)")
