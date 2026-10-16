"""Kinetikern — per-instance spacing proposals from Kinetikern2's engine.

Kinetikern2 (Mirko Velimirovic, MIT; ``native/kinetikern2`` in this repo)
spaces a font from a kinetic equilibrium over glyph triplets. Here its engine
runs as a dylib behind the vendored ctypes bridge, fed the outlines of the
built variable font instantiated at one location, Pass 1 (sidebearings) only
with the punctuation and figures conventions on. Nothing here writes a font.
"""

from __future__ import annotations

import math
import os
import time
import unicodedata
from pathlib import Path

from fontTools import unicodedata as fud
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from . import bridge as kb
from . import punctuation as kpu

LOOSENESS_RATIO = 3.86  # the plugin's middle slider: repulsion over spring
DEFAULT_THRESHOLD = 5.0  # kerning threshold per 1000 em; Pass 1 never uses it
# Unicode blocks of symbols that are spaced but never kerned (kk2_fonts.py)
TECHNICAL_BLOCKS = (
    (0x2190, 0x21FF), (0x2200, 0x22FF), (0x2300, 0x23FF), (0x2400, 0x243F), (0x2440, 0x245F), (0x2460, 0x24FF),
    (0x2500, 0x257F), (0x2580, 0x259F), (0x25A0, 0x25FF), (0x2600, 0x26FF), (0x2700, 0x27BF), (0x27C0, 0x27EF),
    (0x27F0, 0x27FF), (0x2800, 0x28FF), (0x2900, 0x297F), (0x2980, 0x29FF), (0x2A00, 0x2AFF), (0x2B00, 0x2BFF),
    (0xE000, 0xF8FF), (0x1F000, 0x1FAFF),
)

_ENGINE = None


def engine_path():
    """The built engine library, or None. ``$AVAR2_KK2_DYLIB`` overrides the
    repo build at native/kinetikern2/target/release."""
    env = os.environ.get("AVAR2_KK2_DYLIB")
    if env:
        p = Path(env)
        return p if p.exists() else None
    p = Path(__file__).resolve().parents[3] / "native" / "kinetikern2" / "target" / "release" / kb.DYLIB_NAME
    return p if p.exists() else None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        path = engine_path()
        if path is None:
            raise kb.EngineError("Kinetikern2 engine not built; see native/kinetikern2/README.md")
        _ENGINE = kb.Engine(str(path))
    return _ENGINE


def physics(looseness):
    """(spring, repulsion) for a slider position, -1 tight to +1 loose; 0 is
    Kinetikern2's calibrated middle (kk2_window.physics_from_sliders)."""
    t = max(-1.0, min(1.0, float(looseness)))
    return math.exp(-0.55 * t), LOOSENESS_RATIO * math.exp(0.55 * t)


def _finish(job):
    while True:
        state = job.poll()[0]
        if state != kb.STATE_RUNNING:
            break
        time.sleep(0.002)
    if state != kb.STATE_DONE:
        err = job.error()
        job.free()
        raise kb.EngineError("engine job ended in state %d: %s" % (state, err))
    out = job.take()
    job.free()
    return out


def _technical(cp):
    return any(a <= cp <= b for a, b in TECHNICAL_BLOCKS)


def _rhythm_group(ch):
    cat = unicodedata.category(ch)
    if cat in ("Lu", "Lt"):
        return kb.GROUP_UPPERCASE
    if cat in ("Ll", "Lm", "Lo"):
        return kb.GROUP_LOWERCASE
    if cat == "Nd":
        return kb.GROUP_FIGURES
    return kb.GROUP_OTHER


def _tt_contours(font, name):
    """Contours as [(x, y, kind)…]: 0 line, 2 off-curve, 3 quadratic on-curve."""
    glyf = font["glyf"]
    coords, ends, flags = glyf[name].getCoordinates(glyf)
    contours, start = [], 0
    for end in ends:
        idx = list(range(start, end + 1))
        contour = []
        for k, i in enumerate(idx):
            x, y = coords[i]
            if not flags[i] & 1:
                kind = 2
            else:
                kind = 0 if flags[idx[k - 1]] & 1 else 3
            contour.append((float(x), float(y), kind))
        contours.append(contour)
        start = end + 1
    return contours


class _Info(object):
    """What punctuation.engine_arg reads per glyph of a snapshot."""
    __slots__ = ("unicode", "empty", "width")

    def __init__(self, unicode, empty, width):
        self.unicode, self.empty, self.width = unicode, empty, width


class _Snap(object):
    generation = 0

    def __init__(self, names, infos):
        self.names = names
        self.index = dict((n, i) for i, n in enumerate(names))
        self.infos = infos


def _load(static):
    """Engine inputs for the encoded letters, figures, punctuation and symbols
    of a static TrueType font, the way the plugin snapshots a master."""
    cmap = static.getBestCmap() or {}
    glyf, hmtx = static["glyf"], static["hmtx"]
    rows, seen = [], set()
    for cp in sorted(cmap):
        if unicodedata.category(chr(cp))[0] not in "LNPS":
            continue
        name = cmap[cp]
        if name in seen:
            continue
        seen.add(name)
        rows.append((name, cp))
    names = [n for n, _ in rows]
    index = dict((n, i) for i, n in enumerate(names))
    advances = [hmtx[n][0] for n in names]
    figures = set(i for i, (_, cp) in enumerate(rows) if 0x30 <= cp <= 0x39)
    tabular = len(figures) == 10 and len(set(advances[i] for i in figures)) == 1
    specs, infos, current = [], {}, {}
    for i, (name, cp) in enumerate(rows):
        ch = chr(cp)
        script = fud.script(ch)
        g = glyf[name]
        contours = _tt_contours(static, name)
        base = kb.NONE
        if g.isComposite() and g.components:
            base = index.get(g.components[0].glyphName, kb.NONE)
        flags = 0
        if not _technical(cp):
            flags |= kb.GLYPH_KERN
        if script in kb.RTL_SCRIPTS:
            flags |= kb.GLYPH_RTL
        if tabular and i in figures:
            flags |= kb.GLYPH_FIXED_ADVANCE
        # a base letter sets its group's spacing zone (kk2_snapshot.zone_reference)
        if unicodedata.category(ch)[0] == "L" and unicodedata.decomposition(ch) == "":
            flags |= kb.GLYPH_ZONE
        if contours:
            # hmtx holds the sidebearings; a phantom-point variation (SPAC,
            # a Kinetikern correction) moves them without moving the outline
            g.recalcBounds(glyf)
            lsb = hmtx[name][1]
            shift = lsb - g.xMin
            if shift:
                contours = [[(x + shift, y, k) for (x, y, k) in c] for c in contours]
            rsb = advances[i] - lsb - (g.xMax - g.xMin)
            current[name] = (lsb, rsb)
        spec = kb.GlyphSpec(name, contours, advances[i], _rhythm_group(ch), flags, kb.script_code(script), base)
        if contours:
            spec.cur_lsb, spec.cur_rsb = float(lsb), float(rsb)
        specs.append(spec)
        infos[name] = _Info(cp, not contours, advances[i])
    return names, [cp for _, cp in rows], specs, _Snap(names, infos), current


def propose(vf_path, location, looseness=0.0, style="text", threads=0):
    """Space the built font at ``location`` ({axis tag: value}; other axes
    at their defaults) with Pass 1 at ``looseness``. Returns, per encoded
    letter, figure, punctuation mark and symbol, its current and proposed
    sidebearings and advance in font units (``new`` equals ``cur`` for a
    glyph the engine leaves alone; the word space is not listed: the engine
    never sets it), plus the location, UPM and a few statistics."""
    font = TTFont(str(vf_path))
    upm = font["head"].unitsPerEm
    pinned = {}
    if "fvar" in font:
        by_lower = dict((a.axisTag.lower(), a) for a in font["fvar"].axes)
        pinned = dict((a.axisTag, float(a.defaultValue)) for a in font["fvar"].axes)
        for tag, value in (location or {}).items():
            axis = by_lower.get(str(tag).lower())
            if axis is None:
                raise ValueError("unknown axis %r" % (tag,))
            pinned[axis.axisTag] = float(value)
        font = instantiateVariableFont(font, pinned, inplace=True, optimize=False)
    if "glyf" not in font:
        raise ValueError("Kinetikern needs TrueType outlines")
    names, cps, specs, snap, current = _load(font)
    engine = _engine()
    packer = kb.InputPacker()
    for spec in specs:
        packer.add(spec)
    t0 = time.time()
    context = _finish(engine.prepare(packer, upm, threads=threads))
    spring, repulsion = physics(looseness)
    params = kb.make_params(spring=spring, repulsion=repulsion, coupling=0.0, skip_pass2=True,
                            threshold=DEFAULT_THRESHOLD * upm / 1000.0, threads=threads, style=style)
    punctuation = kpu.engine_arg(snap, style) if kpu.available() else None
    result = _finish(engine.solve(context, params, punctuation=punctuation))
    ms = (time.time() - t0) * 1000.0
    hmtx = font["hmtx"]
    glyphs, dl, dr = [], [], []
    for i, name in enumerate(names):
        adv = hmtx[name][0]
        cur = current.get(name)
        m = result.metrics[i]
        spaced = cur is not None and bool(m.valid)
        if spaced:
            lsb, rsb = int(round(m.lsb)), int(round(m.rsb))
            new = {"lsb": lsb, "rsb": rsb, "advance": adv + (lsb - cur[0]) + (rsb - cur[1])}
            dl.append(lsb - cur[0])
            dr.append(rsb - cur[1])
        else:
            new = None
        glyphs.append({
            "name": name, "unicode": cps[i], "spaced": spaced,
            "cur": {"lsb": cur[0], "rsb": cur[1], "advance": adv} if cur else {"lsb": None, "rsb": None, "advance": adv},
            "new": new if new else ({"lsb": cur[0], "rsb": cur[1], "advance": adv} if cur else {"lsb": None, "rsb": None, "advance": adv}),
        })
    result.close()
    context.close()
    # every other encoded glyph (the word space above all) with its advance,
    # so a caller can lay out any text from this one answer
    listed = set(names)
    for cp, name in sorted((font.getBestCmap() or {}).items()):
        if name in listed:
            continue
        listed.add(name)
        adv = hmtx[name][0]
        same = {"lsb": None, "rsb": None, "advance": adv}
        glyphs.append({"name": name, "unicode": cp, "spaced": False, "cur": dict(same), "new": dict(same)})
    per_1000 = 1000.0 / upm
    return {
        "location": pinned, "upm": upm, "looseness": float(looseness), "style": style,
        "glyphs": glyphs,
        "stats": {
            "glyphs": len(glyphs), "spaced": len(dl),
            "mean_lsb_delta_per_1000": round(sum(dl) / len(dl) * per_1000, 1) if dl else 0.0,
            "mean_rsb_delta_per_1000": round(sum(dr) / len(dr) * per_1000, 1) if dr else 0.0,
            "ms": round(ms, 1), "engine": engine.lib.kk2_engine_version().decode(),
        },
    }


from . import inject, sidecar  # noqa: E402,F401  (the sidecar and the font-stage injection)
