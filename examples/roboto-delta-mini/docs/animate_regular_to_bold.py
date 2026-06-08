"""Generate the Regular → Bold animation for examples/roboto-delta-mini/docs/.

Sweeps every case-scoped parametric axis (UC, LC, FI) from a balanced
"Regular" location to a heavier "Bold" location, ping-pongs back. The
caption groups axes by case so the eye reads what's happening at each
script family at once. wght is shown separately — Roboto Delta's wght
axis has no master coverage of its own, so the actual gvar movement
comes from the nine case-scoped axes; wght is the user-facing knob the
avar2 mapping will route into them.

Style matches Crispy's documentation animations:
``documentation/phase_2026_param_axes.py`` and
``examples/crispy-mini/docs/animate_regular_condensed.py``.

Run from the avar2-studio repo root:

    python3 examples/roboto-delta-mini/docs/animate_regular_to_bold.py

Requires ``drawbot-skia`` + ``pillow`` installed in your venv. The
fixture's VF must already be built; run
``avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace``
once before this script.
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


# Starting target: a balanced "Regular" location — mid-stroke thickness,
# moderate counter width, all three case families tuned to read at
# similar visual weight.
START = {
    "XOUC": 150.0, "YOUC": 110.0, "XTUC": 540.0,
    "XOLC": 145.0, "YOLC": 105.0, "XTLC": 540.0,
    "XOFI": 160.0, "YOFI": 120.0, "XTFI": 540.0,
}
# End target: heavier "Bold". All strokes thicken; counter widths
# expand slightly to keep the heavier shapes readable.
END = {
    "XOUC": 270.0, "YOUC": 200.0, "XTUC": 620.0,
    "XOLC": 260.0, "YOLC": 190.0, "XTLC": 610.0,
    "XOFI": 265.0, "YOFI": 195.0, "XTFI": 600.0,
}
WGHT_START = 400.0
WGHT_END = 700.0

# Sample text. Three glyphs per script family is enough to read the
# transformation: cap "A", lowercase "a", a Cyrillic-free "Bb" pair to
# show another stroke family, and digits "12" so the FI axes have
# something to deform.
SAMPLE_TEXT = "Aa Bb 12"

# Animation parameters.
FRAMES = 30
FRAME_DURATION_MS = 75
CANVAS_W = 1000
CANVAS_H = 480
GLYPH_SIZE = 180

LABEL_FONT = "Helvetica"
HEADING_SIZE = 18
LABEL_SIZE = 13
ROW_GAP = 18
COLUMN_GAP = 26
SECTION_GAP = 8
ACTIVE_COLOR = 0.10
START_COLOR = 0.62


def _variation_for_frame(t: float) -> dict:
    """Linear interpolation from START to END at progress t in [0, 1]."""
    coords = {axis: START[axis] + t * (END[axis] - START[axis]) for axis in START}
    coords["wght"] = WGHT_START + t * (WGHT_END - WGHT_START)
    return coords


def _label_color(t: float) -> float:
    """Caption text fades from grey at t=0 to near-black at t=1."""
    return START_COLOR - (START_COLOR - ACTIVE_COLOR) * t


def _render_caption(t: float, baseline_top: float) -> None:
    """Draw the caption block under the glyph specimen.

    Layout:
        Regular → Bold     wght 400 → 700
                              (caption color fades darker as t grows)
        Uppercase    XOUC 150   YOUC 110   XTUC 540
        Lowercase    XOLC 145   YOLC 105   XTLC 540
        Figures      XOFI 160   YOFI 120   XTFI 540
    """
    variations = _variation_for_frame(t)
    color = _label_color(t)

    # Heading line: shows the stylistic frame at the current t.
    # Use ASCII separators only — drawbot_skia's default font fallback
    # doesn't carry Unicode arrows on every system and would render
    # them as tofu boxes.
    style_name = "Regular" if t < 0.5 else "Bold"
    next_name = "Bold" if t < 0.5 else "Regular"
    direction = "to" if t < 0.5 else "from"
    heading = f"{style_name} {direction} {next_name}     wght {variations['wght']:.0f}"
    font(LABEL_FONT)
    fontSize(HEADING_SIZE)
    head_w, _ = textSize(heading)
    fill(color)
    text(heading, ((CANVAS_W - head_w) / 2, baseline_top))

    # Three case-family rows, each with three axis cells.
    fontSize(LABEL_SIZE)
    rows = [
        ("Uppercase", ("XOUC", "YOUC", "XTUC")),
        ("Lowercase", ("XOLC", "YOLC", "XTLC")),
        ("Figures",   ("XOFI", "YOFI", "XTFI")),
    ]
    # Pre-compute column widths so the three axis cells in each row
    # share the same column geometry — keeps the grid visually aligned.
    max_label_w = max(textSize(label + ":")[0] for label, _ in rows)
    cell_text_w = max(
        textSize(f"{tag} {variations[tag]:.0f}")[0]
        for _, axes in rows for tag in axes
    )
    cell_total_w = cell_text_w
    grid_w = max_label_w + 22 + cell_total_w * 3 + COLUMN_GAP * 2
    grid_left = (CANVAS_W - grid_w) / 2

    row_top = baseline_top - HEADING_SIZE - SECTION_GAP
    for label, axes in rows:
        row_top -= ROW_GAP + LABEL_SIZE
        fill(color)
        text(f"{label}:", (grid_left, row_top))
        x = grid_left + max_label_w + 22
        for tag in axes:
            cell = f"{tag} {variations[tag]:.0f}"
            text(cell, (x, row_top))
            x += cell_total_w + COLUMN_GAP


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

    # Caption block sits below the glyph baseline. Drawbot coords are
    # bottom-left origin (Y up), so subtracting moves down on screen.
    caption_top = glyph_baseline - 32
    _render_caption(t, caption_top)

    saveImage(str(frame_path))


def _union_bbox(images, pad: int = 16) -> tuple:
    """Tightest crop covering every frame's non-white content."""
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
            "`avar2-studio examples/roboto-delta-mini/sources/RobotoDeltaMini.designspace` "
            "once so the build is cached, then re-run this script."
        )
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for i in range(FRAMES):
            t = i / (FRAMES - 1)
            _render_frame(t, tmpdir / f"frame_{i:03d}.png", font_path)

        forward = sorted(tmpdir.glob("frame_*.png"))
        # Ping-pong: exclude endpoints from the return leg so the loop
        # doesn't pause.
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
        default=Path(
            "/tmp/avar2-eval-roboto/.avar2-studio/build/RobotoDeltaMini-VF.ttf"
        ),
        help="Path to RobotoDeltaMini-VF.ttf (default: avar2-studio scratch workdir).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "regular_to_bold.gif",
        help="GIF output path.",
    )
    args = parser.parse_args()

    print(f"Rendering {args.output}")
    print(f"  Regular: {START}, wght={WGHT_START}")
    print(f"  Bold:    {END}, wght={WGHT_END}")
    make_gif(args.output, args.font)
    print(f"  done ({args.output.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
