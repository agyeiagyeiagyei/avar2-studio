"""Generate the Regular Condensed animation for examples/crispy-mini/docs/.

Sweeps the three parametric axes — XTRA, XOPQ, YOPQ — from the
``Default`` instance (the first master, sitting at the thin-condensed
corner) to a target ``Regular Condensed`` location, ping-pongs back,
and writes a GIF.

This is the canonical illustration for the parametric → stylistic
workflow: each axis on its own is just a knob, but a coordinated move
across all three produces a recognisable style.

Style matches Crispy's own ``documentation/phase_2026_param_axes.py``:
white background, dark sample text, a caption with all three axis
values where the active values fade from a fixed-grey starting point
to near-black at the target.

Run from the avar2-studio repo root:

    python3 examples/crispy-mini/docs/animate_regular_condensed.py

(Requires ``drawbot-skia`` and ``pillow`` installed in your venv. The
companion build at ``.avar2-studio/build/CrispyMini-VF.ttf`` is loaded
from a scratch workdir — if you've never run ``avar2-studio
examples/crispy-mini/sources/CrispyMini.glyphs``, do that once first
so the VF exists.)
"""

import argparse
import tempfile
from pathlib import Path

from drawbot_skia.drawbot import (
    newDrawing,
    newPage,
    fill,
    rect,
    font,
    fontSize,
    fontVariations,
    textSize,
    text,
    saveImage,
)
from PIL import Image, ImageChops


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FONT_PATH = REPO_ROOT.parent / "Documents/avar2-studio/examples/crispy-mini/build/CrispyMini-VF.ttf"

# The Default instance — Crispy Mini's first master, the thin-condensed
# corner of the parametric design space.
START = {"XTRA": 94.0, "XOPQ": 2.0, "YOPQ": 2.0}

# A balanced mid-design-space location — what a designer might pick to
# anchor a "Regular Condensed" stylistic instance.
END = {"XTRA": 456.3, "XOPQ": 228.9, "YOPQ": 164.8}

# Classic specimen word — ascenders (d, h), x-height letters (e, s, i,
# o, n), a counter (o), and a cap. Every glyph is in Crispy Mini's
# 64-glyph keep set. The word also exercises both cases, which
# matters for this fixture: Crispy's unified XOPQ/YOPQ/XTRA axes
# deform every glyph (compare to roboto-delta-mini, where only the
# cap A would respond).
SAMPLE_TEXT = "Adhesion"

# Animation parameters — match Crispy's parametric-axis GIFs.
FRAMES = 30
FRAME_DURATION_MS = 70
CANVAS_W = 1000
CANVAS_H = 500
GLYPH_SIZE = 200

LABEL_FONT = "Helvetica"
LABEL_SIZE = 18
LABEL_GAP = 32
LABEL_SEPARATOR = "   "
ACTIVE_COLOR = 0.10
START_COLOR = 0.62


def _variation_for_frame(t: float) -> dict:
    """Linear interpolation from START to END at progress t in [0, 1]."""
    return {axis: START[axis] + t * (END[axis] - START[axis]) for axis in START}


def _render_frame(t: float, frame_path: Path, font_path: Path) -> None:
    newDrawing()
    newPage(CANVAS_W, CANVAS_H)
    fill(1, 1, 1)
    rect(0, 0, CANVAS_W, CANVAS_H)

    variations = _variation_for_frame(t)

    # Glyph block.
    fill(0)
    font(str(font_path))
    fontSize(GLYPH_SIZE)
    fontVariations(**variations)
    text_w, _ = textSize(SAMPLE_TEXT)
    text_x = (CANVAS_W - text_w) / 2
    glyph_baseline = CANVAS_H * 0.55
    text(SAMPLE_TEXT, (text_x, glyph_baseline))

    # Caption — XTRA / XOPQ / YOPQ values, colored by how far they've moved.
    font(LABEL_FONT)
    fontSize(LABEL_SIZE)
    parts = []
    for ax_name in ("XTRA", "XOPQ", "YOPQ"):
        val = variations[ax_name]
        part_text = f"{ax_name} {val:.1f}"
        part_w, _ = textSize(part_text)
        # As t grows, the color darkens from START_COLOR to ACTIVE_COLOR —
        # eye reads "these are the ones doing the work."
        color = START_COLOR - (START_COLOR - ACTIVE_COLOR) * t
        parts.append({"text": part_text, "width": part_w, "color": color})

    sep_w, _ = textSize(LABEL_SEPARATOR)
    total_w = sum(p["width"] for p in parts) + sep_w * (len(parts) - 1)
    x_cursor = (CANVAS_W - total_w) / 2
    label_y = glyph_baseline - LABEL_GAP - LABEL_SIZE

    for i, p in enumerate(parts):
        fill(p["color"])
        text(p["text"], (x_cursor, label_y))
        x_cursor += p["width"]
        if i < len(parts) - 1:
            x_cursor += sep_w

    saveImage(str(frame_path))


def _union_bbox(images, pad: int = 16) -> tuple:
    """Tightest crop covering every frame's content."""
    bboxes = []
    for img in images:
        rgb = img.convert("RGB")
        white = Image.new("RGB", rgb.size, (255, 255, 255))
        diff = ImageChops.difference(rgb, white)
        bbox = diff.getbbox()
        if bbox:
            bboxes.append(bbox)
    if not bboxes:
        return (0, 0, images[0].width, images[0].height)
    left = max(0, min(b[0] for b in bboxes) - pad)
    upper = max(0, min(b[1] for b in bboxes) - pad)
    right = min(images[0].width, max(b[2] for b in bboxes) + pad)
    lower = min(images[0].height, max(b[3] for b in bboxes) + pad)
    return (left, upper, right, lower)


def make_gif(output_path: Path, font_path: Path) -> None:
    if not font_path.exists():
        raise FileNotFoundError(
            f"VF not found at {font_path}. Run "
            "`avar2-studio examples/crispy-mini/sources/CrispyMini.glyphs` "
            "once so the build is cached, then re-run this script."
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for i in range(FRAMES):
            t = i / (FRAMES - 1)
            _render_frame(t, tmpdir / f"frame_{i:03d}.png", font_path)

        forward = sorted(tmpdir.glob("frame_*.png"))
        # Ping-pong (exclude endpoints from the return leg so the loop
        # doesn't pause).
        sequence = forward + forward[-2:0:-1]
        rgb_frames = [Image.open(p).convert("RGB") for p in sequence]
        crop = _union_bbox(rgb_frames)
        cropped = [im.crop(crop) for im in rgb_frames]
        images = [im.convert("P", palette=Image.ADAPTIVE) for im in cropped]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(
            output_path,
            save_all=True,
            append_images=images[1:],
            duration=FRAME_DURATION_MS,
            loop=0,
            optimize=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--font",
        type=Path,
        default=Path("/tmp/avar2-eval-crispy/.avar2-studio/build/CrispyMini-VF.ttf"),
        help="Path to CrispyMini-VF.ttf (default: the scratch workdir avar2-studio uses).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "regular_condensed.gif",
        help="GIF output path.",
    )
    args = parser.parse_args()

    print(f"Rendering {args.output}")
    print(f"  start: {START}")
    print(f"  end:   {END}")
    make_gif(args.output, args.font)
    print(f"  done ({args.output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
