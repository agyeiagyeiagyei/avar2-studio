#!/usr/bin/env python3
"""E2i-c bookkeeping check: classify every (glyph, master) the rule would push
below a 1 u advance at the SPAC tight end.

For each case print: glyph, base (composites), master location, that master's
advance, ink and sidebearings, the rule's d, and the resulting advance — then
say whether it is
  (a) a measurement / bookkeeping error (fix it), or
  (b) the rule itself (the user's floor decision applies).

Usage: python test-output/e2ic_classify.py <source.glyphs> <sidecar.json>
       [--end -40] [--build font.ttf]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fontTools.pens.boundsPen import BoundsPen  # noqa: E402

from avar2_studio import sources_export  # noqa: E402


def _metrics(font, name):
    """(advance, lsb, ink) of one glyph in a UFO font."""
    glyph = font[name]
    pen = BoundsPen(font)
    glyph.draw(pen)
    lsb = pen.bounds[0] if pen.bounds else 0
    ink = pen.bounds[2] - pen.bounds[0] if pen.bounds else 0
    return float(glyph.width), float(lsb), float(ink)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--build", type=Path, default=None)
    args = ap.parse_args(argv)

    entry = json.loads(args.sidecar.read_text())["transforms"][0]
    params = entry["params"]
    lo = float(params.get("min", -20))
    hi = float(params.get("max", 40))
    bias = float(params.get("bias", 1.0))
    scale = float(params.get("scale", 1.0))
    end = args.end if args.end is not None else lo
    if end >= 0:
        print("the floor is a tight-end rule; pass a negative --end", file=sys.stderr)
        return 2

    from avar2_studio.build import config_generator  # noqa: F401  (import cost)

    font, _fmt = sources_export._load(args.source)
    doc = font  # glyphsLib font; the export converts it, so use its UFO
    from glyphsLib import to_ufos

    ufos = to_ufos(doc)
    default_ufo = ufos[0]
    renames = {}
    build_locations = None
    if args.build:
        # (B): the shipped measurement — inks from the BUILD at each master.
        renames = sources_export._production_to_source(default_ufo)
        tags = {a.name: (a.axisTag or a.name) for a in doc.axes}
        build_locations = []
        for master in doc.masters:
            values = dict(zip((a.name for a in doc.axes), master.axes))
            build_locations.append({tags[n]: float(v)
                                    for n, v in values.items() if tags.get(n)
                                    and v is not None})
        inks_build = sources_export._ink_widths_from_build(args.build)
        basis = {renames.get(n, n): w for n, w in inks_build.items()}
    else:
        basis = sources_export._ink_widths(default_ufo)
    names = [n for n in basis if n in default_ufo and n != ".notdef"]
    mean_D = sum(basis[n] for n in names) / len(names)

    print(f"end {end:g}  bias {bias:g}  scale {scale:g}  masters {len(ufos)}")
    print(f"mean_D {mean_D:.1f} u over {len(names)} glyphs")
    print()
    cases = []
    for index, ufo in enumerate(ufos):
        if args.build:
            raw = sources_export._ink_widths_from_build(
                args.build, build_locations[index])
            inks = {renames.get(n, n): w for n, w in raw.items()
                    if renames.get(n, n) in names}
        else:
            inks = {n: w for n, w in sources_export._ink_widths(ufo).items()
                    if n in names}
        mean_L = sum(inks.values()) / len(inks)
        label = f"master {index}"
        if getattr(ufo.info, "styleName", None):
            label = ufo.info.styleName
        for name, ink in sorted(inks.items()):
            glyph = ufo[name]
            base = None
            if glyph.components:
                base = glyph.components[0].baseGlyph
                ink = inks.get(base, 0.0)
                if ink <= 0:
                    continue
            d = end * scale * (ink / mean_L) ** bias * (mean_L / mean_D)
            advance, lsb, own_ink = _metrics(ufo, name)
            rsb = advance - lsb - own_ink
            # the export's cap: advance must stay >= 1
            final = advance + (d if glyph.components else 2 * d)
            if final >= 1.0:
                continue
            cases.append({
                "glyph": name, "base": base, "master": label,
                "advance": advance, "ink": ink, "own_ink": own_ink,
                "lsb": lsb, "rsb": rsb, "d": d,
                "rule_advance": final,
            })

    if not cases:
        print("no (glyph, master) at this end is driven below 1 u — the floor "
              "would not fire here")
        return 0

    print(f"{len(cases)} case(s) below 1 u at SPAC {end:g}:")
    for c in cases:
        overhang = "OVERHANG" if (c["lsb"] < 0 or c["rsb"] < 0
                                  or c["own_ink"] > c["advance"]) else "no overhang"
        comp = f" (base {c['base']})" if c["base"] else ""
        print(f"  {c['glyph']}{comp:<14} {c['master']:<22} "
              f"adv {c['advance']:7.1f} ink {c['ink']:6.1f} "
              f"lsb {c['lsb']:7.1f} rsb {c['rsb']:7.1f} "
              f"d {c['d']:7.1f} -> adv {c['rule_advance']:7.1f}  {overhang}")

    no_overhang = [c for c in cases
                   if not (c["lsb"] < 0 or c["rsb"] < 0
                           or c["own_ink"] > c["advance"])]
    print()
    print(f"overhanging: {len(cases) - len(no_overhang)}  "
          f"no overhang: {len(no_overhang)}")
    if no_overhang:
        print("STATE: blocked — these have no overhang at that corner:",
              ", ".join(f"{c['glyph']}@{c['master']}" for c in no_overhang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
