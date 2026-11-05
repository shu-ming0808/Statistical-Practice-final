"""Remove only the flat, edge-connected background of existing chart PNGs.

Plot generators save transparent figures directly. This one-time migration also
covers archived reports without rerunning their statistical analyses.
The photographic site hero is intentionally excluded.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
from scipy.ndimage import label


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"app/frontend/src/assets/hero.png"}


def tracked_pngs() -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z", "--", "*.png"], cwd=ROOT)
    return [ROOT / name.decode("utf-8") for name in raw.split(b"\0") if name]


def convert(path: Path, *, apply: bool) -> str:
    with Image.open(path) as source:
        rgba = np.asarray(source.convert("RGBA")).copy()
    height, width, _ = rgba.shape
    corners = np.array([rgba[0, 0], rgba[0, -1], rgba[-1, 0], rgba[-1, -1]], dtype=np.int16)
    if np.any(corners[:, 3] == 0):
        return "already transparent"
    background = np.median(corners[:, :3], axis=0).astype(np.int16)
    if np.max(np.abs(corners[:, :3] - background)) > 12:
        return "nonuniform corners; skipped"
    background_pixels = np.all(np.abs(rgba[:, :, :3].astype(np.int16) - background) <= 12, axis=2)
    components, _ = label(background_pixels, structure=np.ones((3, 3), dtype=np.uint8))
    border_ids = np.unique(np.r_[components[0], components[-1], components[:, 0], components[:, -1]])
    border_ids = border_ids[border_ids != 0]
    mask = np.isin(components, border_ids)
    if mask.mean() < 0.01:
        return "no flat edge background; skipped"
    rgba[mask, 3] = 0
    if apply:
        Image.fromarray(rgba, "RGBA").save(path)
    return f"{mask.mean():.1%} background pixels{' updated' if apply else ' found'}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for path in tracked_pngs():
        relative = path.relative_to(ROOT).as_posix()
        if relative in EXCLUDED:
            continue
        print(relative, convert(path, apply=args.apply))


if __name__ == "__main__":
    main()
