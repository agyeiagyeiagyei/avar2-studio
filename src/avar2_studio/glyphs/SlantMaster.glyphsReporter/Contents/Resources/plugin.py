# -*- coding: utf-8 -*-
# Copyright 2026 Agyei Archer. Licensed under the Apache License, Version 2.0.
"""Slant Master — Glyphs 3 Reporter plugin.

Starts the italic masters. The panel lists the SOURCES — the masters,
then the instances, interpolated on the fly — each with a tick box and
its own slant angle, kept in the source file. Apply shears every glyph of
every ticked source and appends each result as a NEW master; the uprights
are never touched. Until Apply, the Edit view shows the master being
edited sheared by its row's angle (blue) and the width reference
(magenta).

Width matching reuses Width Matcher's spacing contracts against a
REFERENCE master or instance of this font, or of any other open
document (values scaled by UPM).

Extracted from the "Slant Glyphs" script in docrepairtools (same author):
the math lives in slant_math.py / slant_extrema.py / slant_paths.py
beside this file; this file is the Glyphs glue, on the skeleton the
sibling reporters share (see the hub README's development notes).
"""

import copy as _copy
import os
import sys
import traceback

import objc
from AppKit import (
    NSAffineTransform,
    NSBezierPath,
    NSColor,
    NSNumberFormatter,
    NSNumberFormatterDecimalStyle,
)
from GlyphsApp import *
from GlyphsApp.plugins import *

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slant_math  # noqa: E402
import slant_paths  # noqa: E402

try:
    from vanilla import (
        Button,
        CheckBox,
        CheckBoxListCell,
        EditText,
        FloatingWindow,
        List,
        PopUpButton,
        TextBox,
    )
except ImportError:  # vanilla ships with Glyphs; guard for dev linting
    FloatingWindow = None


# Width Matcher keeps a live scratch instance in the font; it is neither
# a source nor a reference anyone means to pick.
EXCLUDED_INSTANCE_NAMES = ("Width Matcher Preview",)

# A source's slant angle lives in the source file, in the userData of its
# master or instance.
ANGLE_KEY = "xyz.avar2studio.slant-angle"
DEFAULT_ANGLE = 10.0
DEFAULT_SUFFIX = "Italic"

# The Reference that stands for "whichever source the row is": with
# several masters slanted in one go, each takes its own upright's widths.
# One reference for all would give a wide master a narrow one's advances.
OWN_SOURCE = {"kind": "own", "key": "own", "label": "Each source itself"}

GEN_BLUE = (0.10, 0.45, 0.95)      # the sheared source
REF_MAGENTA = (0.85, 0.25, 0.55)   # the width reference
EDIT_MARK = (0.55, 0.55, 0.55)     # the layer being edited
DOT_ORANGE = (0.95, 0.55, 0.10)    # extrema to insert / remove
DOT_GRAY = (0.50, 0.50, 0.50)      # extrema kept (gated, line joins)

PANEL_W = 340
PREVIEW_CACHE_MAX = 200

DEBUG = False  # flip to True for /tmp instrumentation while developing
_DEBUG_LOG = "/tmp/slantmaster-debug.log"


def _dbgexc(prefix=""):
    """Log the CURRENT exception with its traceback."""
    if not DEBUG:
        return
    try:
        _dbg("%s%s" % (prefix, traceback.format_exc()))
    except Exception:
        pass


def _dbg(msg):
    if not DEBUG:
        return
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write("%s\n" % msg)
    except Exception:
        pass


def _rgba(rgb, a):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a)


class SlantMaster(ReporterPlugin):
    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "Slant Master"})
        self.keyboardShortcut = None
        self._panel = None
        self._panelFont = None
        self._lastLayer = None
        self._active = False               # mirrors the View toggle (willActivate)
        # Parameters — kept on self so they survive panel rebuilds. The
        # angles are not here: they are per source, in the source file.
        self.widthPct = 100.0
        self.heightPct = 100.0
        self.pivotChoice = slant_math.PIVOT_XHEIGHT
        self.decompose = True
        self.fixExtrema = False            # changes node counts — opt in
        self.setItalic = True
        self.axisValue = None              # text TYPED in the Axis field; None = default
        self.matchWidths = True
        self.spacingMode = slant_math.SPACING_ADV_KEEP_LSB
        self.advOffset = 0.0
        self.refKey = None                 # entry key (font|kind:name)
        self.nameSuffix = DEFAULT_SUFFIX   # new masters are "<source> <suffix>"
        # Entries and caches.
        self._sourceEntries = []
        self._refEntries = []
        self._rows = []                    # one per source entry: entry, use, angle
        self._ticks = {}                   # entry key -> ticked, for the session
        self._fillingRows = False          # the list is being filled, not edited
        self._axisShown = None             # the default the Axis field was filled with
        self._docSignature = None
        self._interpCache = {}             # entry key -> (interpFont, masterId)
        self._previewCache = {}
        self._lastCreatedMasterIds = []

    @objc.python_method
    def start(self):
        _dbg("start() called")
        self._forgetRestoredToggle()

    @objc.python_method
    def _nswindow(self):
        if self._panel is None:
            return None
        return self._panel.getNSWindow()

    # --- View toggle -----------------------------------------------------
    # Glyphs calls willActivate / willDeactivate on the reporter instance
    # when its View item is toggled, and again at launch for every reporter
    # listed in its ``visibleReporters`` default (whatever was on at quit).
    # The SDK's ReporterPlugin does not forward these to activate() /
    # deactivate() — only SelectTool does — so they are implemented here
    # directly, as real ObjC selectors (no @objc.python_method).

    def willActivate(self):
        try:
            self._active = True
            self._showPanel()
            self._redraw()
        except Exception:
            _dbgexc("willActivate: ")

    def willDeactivate(self):
        try:
            self._active = False
            self._hidePanel()
            self._redraw()
        except Exception:
            _dbgexc("willDeactivate: ")

    @objc.python_method
    def _forgetRestoredToggle(self):
        """Drop this reporter from Glyphs' ``visibleReporters`` default so
        the panel never opens on launch; the next View click puts it back
        for the session."""
        key = "visibleReporters"
        name = self.__class__.__name__
        try:
            current = Glyphs.defaults[key]
            if current and name in list(current):
                Glyphs.defaults[key] = [n for n in current if n != name]
        except Exception:
            _dbgexc("visibleReporters: ")

    @objc.python_method
    def _showPanel(self):
        if FloatingWindow is None:
            return
        if self._panel is None:
            self._build_panel()
        ns = self._nswindow()
        if ns is not None and not ns.isVisible():
            try:
                ns.makeKeyAndOrderFront_(None)
            except Exception:
                _dbgexc("showPanel: ")

    @objc.python_method
    def _hidePanel(self):
        ns = self._nswindow()
        if ns is not None and ns.isVisible():
            try:
                ns.orderOut_(None)
            except Exception:
                _dbgexc("hidePanel: ")

    @objc.python_method
    def _panelClosed(self, sender):
        """Panel's red X: turn the reporter off through Glyphs' own API
        (the View item follows) and drop the dead vanilla window — the
        next willActivate rebuilds it."""
        self._panel = None
        try:
            Glyphs.deactivateReporter(self)
        except Exception:
            _dbgexc("panelClosed: ")

    # ------------------------------------------------------------------
    # font / entries
    # ------------------------------------------------------------------

    @objc.python_method
    def _currentFont(self):
        try:
            if Glyphs.font is not None:
                return Glyphs.font
        except Exception:
            pass
        try:
            doc = Glyphs.currentDocument
            if doc is not None:
                return doc.font
        except Exception:
            pass
        if self._lastLayer is not None:
            try:
                return self._lastLayer.parent.font
            except Exception:
                pass
        return None

    @objc.python_method
    def _fontKey(self, font):
        try:
            fp = font.filepath
            if fp:
                return str(fp)
        except Exception:
            pass
        return "font-%d" % id(font)

    @objc.python_method
    def _docName(self, font):
        try:
            fp = font.filepath
            if fp:
                return os.path.basename(str(fp))
        except Exception:
            pass
        try:
            return "%s (unsaved)" % font.familyName
        except Exception:
            return "untitled"

    @objc.python_method
    def _entriesFor(self, font, prefix=""):
        """Masters first, then instances — the entry model Instance Delta
        uses, keyed by font and name so index shifts never move a pick."""
        fk = self._fontKey(font)
        out = []
        for m in font.masters:
            out.append({"font": font, "kind": "master", "name": str(m.name), "obj": m,
                        "key": "%s|master:%s" % (fk, m.name),
                        "label": "%sMaster: %s" % (prefix, m.name)})
        for inst in font.instances:
            if str(inst.name) in EXCLUDED_INSTANCE_NAMES:
                continue
            out.append({"font": font, "kind": "instance", "name": str(inst.name), "obj": inst,
                        "key": "%s|instance:%s" % (fk, inst.name),
                        "label": "%sInstance: %s" % (prefix, inst.name)})
        return out

    @objc.python_method
    def _openFonts(self):
        try:
            return list(Glyphs.fonts)
        except Exception:
            return []

    @objc.python_method
    def _docSignatureNow(self, font):
        sig = []
        fonts = self._openFonts() or [font]
        for f in fonts:
            try:
                sig.append((self._fontKey(f), len(f.masters), len(f.instances)))
            except Exception:
                pass
        return tuple(sig)

    @objc.python_method
    def _rebuildEntries(self, font):
        self._sourceEntries = self._entriesFor(font)
        refs = list(self._sourceEntries)
        fk = self._fontKey(font)
        for f in self._openFonts():
            if self._fontKey(f) == fk:
                continue
            refs += self._entriesFor(f, prefix="%s › " % self._docName(f))
        self._refEntries = [OWN_SOURCE] + refs
        self._docSignature = self._docSignatureNow(font)
        self._rows = [self._rowFor(e) for e in self._sourceEntries]

    @objc.python_method
    def _rowFor(self, entry):
        """A list row: the entry, whether it is ticked, and its angle.
        Uprights start ticked; a master that is slanted already, and an
        instance, are opt-in."""
        use = self._ticks.get(entry["key"])
        if use is None:
            use = entry["kind"] == "master" and not float(entry["obj"].italicAngle or 0.0)
        angle = entry["obj"].userData[ANGLE_KEY]
        return {"entry": entry, "use": bool(use),
                "angle": DEFAULT_ANGLE if angle is None else float(angle)}

    @objc.python_method
    def _tickedRows(self):
        return [r for r in self._rows if r["use"]]

    @objc.python_method
    def _setRowAngle(self, row, angle):
        """Take a row's new angle and write it to the source file."""
        if angle != row["angle"]:
            row["angle"] = angle
            row["entry"]["obj"].userData[ANGLE_KEY] = angle

    @objc.python_method
    def _rowForLayer(self, layer):
        """The ticked row of the master the layer on screen belongs to."""
        mid = layer.associatedMasterId
        for row in self._tickedRows():
            e = row["entry"]
            if e["kind"] == "master" and e["obj"].id == mid:
                return row
        return None

    @objc.python_method
    def _newName(self, entry):
        return "%s %s" % (entry["name"], self.nameSuffix)

    @objc.python_method
    def _entryByKey(self, entries, key):
        for e in entries:
            if e["key"] == key:
                return e
        return entries[0] if entries else None

    @objc.python_method
    def _refEntry(self):
        e = self._entryByKey(self._refEntries, self.refKey)
        if e is not None:
            self.refKey = e["key"]
        return e

    @objc.python_method
    def _refFor(self, src):
        """The reference a source is spaced against, or None with Match
        widths off."""
        if not self.matchWidths:
            return None
        ref = self._refEntry()
        return src if ref is OWN_SOURCE else ref

    @objc.python_method
    def _italAxis(self, font):
        """(index, tag) of the font's italic axis — 'ital' or 'slnt'."""
        try:
            for i, a in enumerate(font.axes):
                tag = str(getattr(a, "axisTag", None) or "").lower()
                if tag in ("ital", "slnt"):
                    return i, tag
        except Exception:
            _dbgexc("italAxis: ")
        return None, None

    @objc.python_method
    def _axisDefault(self, tag):
        """What the Axis field holds until something else is typed: 1 on
        an ital axis; on slnt, minus the angle of the first ticked row.
        It is ONE coordinate for every new master, whatever its own
        angle — the slanted masters have to share a position on the axis."""
        if tag == "ital":
            return "1"
        ticked = self._tickedRows()
        return "%g" % (0.0 - (ticked[0]["angle"] if ticked else DEFAULT_ANGLE))

    # ------------------------------------------------------------------
    # interpolation + layer lookup
    # ------------------------------------------------------------------

    @objc.python_method
    def _interpolated(self, entry):
        """An instance's interpolated font, cached per entry. Draw
        callbacks run for every glyph on screen, so interpolating there
        would make the Edit view unusable — the cache is dropped on
        Refresh, on a resync, and after Apply."""
        key = entry["key"]
        hit = self._interpCache.get(key)
        if hit is not None:
            return hit
        try:
            interp = entry["obj"].interpolatedFont
        except Exception:
            _dbgexc("interpolate: ")
            return None
        if interp is None or not len(interp.masters):
            return None
        hit = (interp, interp.masters[0].id)
        if len(self._interpCache) >= 2:
            self._interpCache.clear()
        self._interpCache[key] = hit
        return hit

    @objc.python_method
    def _entryMaster(self, entry):
        """The GSFontMaster the entry's layers belong to (an instance's
        interpolated master) — the pivot reads its metrics."""
        if entry["kind"] == "master":
            return entry["obj"]
        hit = self._interpolated(entry)
        return hit[0].masters[0] if hit else None

    @objc.python_method
    def _entryLayer(self, entry, glyphName):
        try:
            if entry["kind"] == "master":
                g = entry["font"].glyphs[glyphName]
                return None if g is None else g.layers[entry["obj"].id]
            hit = self._interpolated(entry)
            if not hit:
                return None
            interp, mid = hit
            g = interp.glyphs[glyphName]
            if g is None:
                return None
            layer = g.layers[mid]
            if layer is None and len(g.layers):
                layer = g.layers[0]
            return layer
        except Exception:
            _dbgexc("entryLayer: ")
            return None

    @objc.python_method
    def _entryGlyphNames(self, entry):
        try:
            if entry["kind"] == "master":
                font = entry["font"]
            else:
                hit = self._interpolated(entry)
                font = hit[0] if hit else None
            return set(str(g.name) for g in font.glyphs) if font is not None else set()
        except Exception:
            _dbgexc("entryGlyphNames: ")
            return set()

    @objc.python_method
    def _refLayerFor(self, entry, names, glyphName):
        """The reference's layer for a glyph: direct hit, else its variant
        base (a.smcp → a); None when the reference has neither."""
        target = slant_math.reference_name(names, glyphName)
        if target is None:
            return None
        return self._entryLayer(entry, target)

    @objc.python_method
    def _upmRatio(self, font, entry):
        try:
            return float(font.upm) / float(entry["font"].upm)
        except Exception:
            return 1.0

    # ------------------------------------------------------------------
    # geometry helpers
    # ------------------------------------------------------------------

    @objc.python_method
    def _matrixFor(self, master, angle):
        xh = float(getattr(master, "xHeight", 0.0) or 0.0)
        ch = float(getattr(master, "capHeight", 0.0) or 0.0)
        origin = slant_math.pivot_y(self.pivotChoice, xh, ch)
        return slant_math.shear_transform(angle, self.widthPct, self.heightPct, origin)

    @objc.python_method
    def _workingCopy(self, layer, decompose):
        """A detached copy to shear. Decomposition runs on the ATTACHED
        layer (components resolve only while it has a parent)."""
        try:
            if decompose and hasattr(layer, "copyDecomposedLayer"):
                return layer.copyDecomposedLayer()
            return layer.copy()
        except Exception:
            _dbgexc("workingCopy: ")
            return None

    @objc.python_method
    def _detach(self, obj):
        """A standalone copy of a master from the interpolated font: the
        interpolated font is a temporary, and a master handed over
        without copying loses its layers when it is released."""
        for attempt in (lambda: obj.copy(), lambda: _copy.copy(obj)):
            try:
                dup = attempt()
            except Exception:
                continue
            if dup is not None:
                return dup
        return obj

    @objc.python_method
    def _masterAxes(self, master):
        try:
            return [float(v) for v in master.axes]
        except Exception:
            pass
        try:
            return [float(v) for v in master.axesValues()]
        except Exception:
            return []

    @objc.python_method
    def _inkRect(self, layer):
        """(minx, miny, maxx, maxy) of a layer's drawn ink, or None for
        empty layers (space etc.)."""
        if layer is None:
            return None
        try:
            b = layer.bounds
            if b.size.width == 0 and b.size.height == 0:
                return None
            return (float(b.origin.x), float(b.origin.y),
                    float(b.origin.x + b.size.width),
                    float(b.origin.y + b.size.height))
        except Exception:
            return None

    @objc.python_method
    def _layerPath(self, layer, scale=1.0, dx=0.0):
        """A layer's outline as an NSBezierPath copy, optionally scaled
        (UPM ratio) and shifted (planned LSB)."""
        path = None
        for attr in ("completeBezierPath", "bezierPath"):
            try:
                path = getattr(layer, attr, None)
            except Exception:
                path = None
            if path is not None:
                break
        if path is None:
            return None
        try:
            p = path.copy()
            if scale != 1.0 or dx:
                t = NSAffineTransform.transform()
                t.translateXBy_yBy_(dx, 0.0)
                t.scaleBy_(scale)
                p.transformUsingAffineTransform_(t)
            return p
        except Exception:
            _dbgexc("layerPath: ")
            return None

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------

    @objc.python_method
    def _invalidateAll(self):
        self._interpCache.clear()
        self._previewCache.clear()

    @objc.python_method
    def _previewFor(self, font, glyphName, row):
        src = row["entry"]
        ref = self._refFor(src)
        try:
            stamp = str(getattr(font.glyphs[glyphName], "lastChange", ""))
        except Exception:
            stamp = ""
        key = (self._fontKey(font), glyphName, src["key"], ref["key"] if ref else None,
               row["angle"], self.widthPct, self.heightPct, self.pivotChoice,
               self.fixExtrema, self.spacingMode, self.advOffset, float(font.gridLength), stamp)
        pv = self._previewCache.get(key)
        if pv is not None:
            return pv
        pv = self._computePreview(font, glyphName, src, ref, row["angle"])
        if len(self._previewCache) >= PREVIEW_CACHE_MAX:
            self._previewCache.clear()
        self._previewCache[key] = pv if pv is not None else {}
        return pv

    @objc.python_method
    def _computePreview(self, font, glyphName, src, ref, angle):
        """The sheared source for one glyph, computed on a detached copy —
        the layers themselves are never touched before Apply. The
        spacing plan measures the ink as Apply will find it on the new
        master: along the slant when the master is to carry the angle."""
        srcLayer = self._entryLayer(src, glyphName)
        master = self._entryMaster(src)
        if srcLayer is None or master is None:
            return None
        work = self._workingCopy(srcLayer, True)  # always decomposed for the picture
        if work is None:
            return None
        matrix = self._matrixFor(master, angle)
        try:
            stats = slant_paths.slant_layer(work, matrix, fix_extrema=self.fixExtrema, log=_dbg,
                                            grid=float(font.gridLength))
        except Exception:
            _dbgexc("preview slant: ")
            return None
        measured = angle if self.setItalic else float(getattr(master, "italicAngle", 0.0) or 0.0)
        xh = float(getattr(master, "xHeight", 0.0) or 0.0) * self.heightPct / 100.0
        ink = slant_paths.ink_span(work, measured, xh / 2.0)
        try:
            adv = float(work.width)
        except Exception:
            adv = 0.0
        plan = None
        refInfo = None
        if ref is not None:
            names = self._entryGlyphNames(ref)
            refLayer = self._refLayerFor(ref, names, glyphName)
            if refLayer is not None:
                ratio = self._upmRatio(font, ref)
                try:
                    refAdv = float(refLayer.width) * ratio
                    refLSB = float(refLayer.LSB) * ratio
                    refRSB = float(refLayer.RSB) * ratio
                except Exception:
                    refAdv = refLSB = refRSB = None
                if refAdv is not None:
                    # Its own upright is on screen already; only another
                    # reference is worth drawing over it.
                    refInfo = {"path": None if ref is src else self._layerPath(refLayer, scale=ratio),
                               "adv": refAdv}
                    if ink is None:
                        plan = (None, None, slant_math.empty_advance(self.spacingMode, refAdv, self.advOffset))
                    else:
                        plan = slant_math.target_spacing(self.spacingMode, refLSB, refRSB, refAdv,
                                                         ink[1] - ink[0], self.advOffset)
        dx = 0.0
        if plan is not None and plan[0] is not None and ink is not None:
            dx = plan[0] - ink[0]
        if plan is not None:
            adv = plan[2]
        return {
            "path": self._layerPath(work, dx=dx),
            "dx": dx,
            "adv": adv,
            "plan": plan,
            "ref": refInfo,
            "stats": stats,
        }

    # ------------------------------------------------------------------
    # drawing
    # ------------------------------------------------------------------

    @objc.python_method
    def _vline(self, x, y0, y1, rgb, alpha, width):
        try:
            p = NSBezierPath.bezierPath()
            p.moveToPoint_((x, y0))
            p.lineToPoint_((x, y1))
            p.setLineWidth_(width)
            _rgba(rgb, alpha).set()
            p.stroke()
        except Exception:
            pass

    @objc.python_method
    def _dot(self, x, y, r, rgb, filled, width):
        try:
            p = NSBezierPath.bezierPathWithOvalInRect_(((x - r, y - r), (2 * r, 2 * r)))
            _rgba(rgb, 0.9).set()
            if filled:
                p.fill()
            else:
                p.setLineWidth_(width)
                p.stroke()
        except Exception:
            pass

    @objc.python_method
    def _metricsSpan(self, layer):
        try:
            m = layer.master
            return (float(m.descender), float(m.ascender))
        except Exception:
            return (-300.0, 1600.0)

    @objc.python_method
    def _draw(self, layer, pv):
        try:
            scale = float(self.getScale())
        except Exception:
            scale = 1.0
        lw = 1.0 / max(scale, 1e-6)
        dx = pv.get("dx", 0.0)
        path = pv.get("path")
        if path is not None:
            try:
                _rgba(GEN_BLUE, 0.20).set()
                path.fill()
                _rgba(GEN_BLUE, 0.85).set()
                path.setLineWidth_(lw)
                path.stroke()
            except Exception:
                _dbgexc("draw source: ")
        ref = pv.get("ref")
        if ref and ref.get("path") is not None:
            try:
                _rgba(REF_MAGENTA, 0.12).set()
                ref["path"].fill()
                _rgba(REF_MAGENTA, 0.5).set()
                ref["path"].setLineWidth_(lw)
                ref["path"].stroke()
            except Exception:
                _dbgexc("draw ref: ")
        y0, y1 = self._metricsSpan(layer)
        try:
            editAdv = float(layer.width)
        except Exception:
            editAdv = 0.0
        self._vline(0.0, y0, y1, EDIT_MARK, 0.45, lw)
        self._vline(editAdv, y0, y1, EDIT_MARK, 0.65, lw)
        self._vline(float(pv.get("adv", editAdv)), y0, y1, GEN_BLUE, 0.8, 1.5 * lw)
        if ref:
            self._vline(float(ref["adv"]), y0, y1, REF_MAGENTA, 0.8, 1.5 * lw)
        stats = pv.get("stats") or {}
        r = 3.0 * lw
        for x, y in stats.get("inserted_positions", []):
            self._dot(x + dx, y, r, DOT_ORANGE, True, lw)
        for x, y in stats.get("removed_positions", []):
            self._dot(x + dx, y, r, DOT_ORANGE, False, lw)
        for x, y in stats.get("kept_positions", []):
            self._dot(x + dx, y, r, DOT_GRAY, False, lw)

    @objc.python_method
    def _updateReadout(self, font, glyphName, pv, row):
        """One line for the ACTIVE glyph only — foreground() runs for every
        glyph in the tab. `row` is None when the master on screen has no
        ticked row, so nothing is drawn."""
        if self._panel is None or not hasattr(self._panel, "readout"):
            return
        try:
            sel = font.selectedLayers
            active = sel[0].parent.name if sel else None
        except Exception:
            active = None
        if active is not None and active != glyphName:
            return
        if row is None:
            self._panel.readout.set("%s — this master's row is not ticked" % glyphName)
            return
        ref = self._refFor(row["entry"])
        parts = ["%s — from %s at %g°" % (glyphName, row["entry"]["label"], row["angle"])]
        plan = pv.get("plan")
        if ref is not None:
            if plan is None:
                parts.append("no match in %s" % ref["label"])
            elif plan[0] is None:
                parts.append("empty: adv %.0f (%s)" % (plan[2], ref["label"]))
            else:
                parts.append("LSB %.0f / RSB %.0f / adv %.0f vs %s" % (plan[0], plan[1], plan[2], ref["label"]))
        ex = slant_paths.extrema_summary(pv.get("stats") or {})
        if ex:
            parts.append(ex)
        try:
            self._panel.readout.set(" · ".join(parts))
        except Exception:
            pass

    @objc.python_method
    def foreground(self, layer):
        """Glyphs 3 reporter draw entry: panel upkeep, resync, and the
        overlay for the glyph on screen. Glyphs only calls it while the
        View toggle is on."""
        self._lastLayer = layer
        self._showPanel()  # lazy: also covers a build that skips willActivate
        font = self._currentFont()
        if font is None:
            return
        if font is not self._panelFont or self._docSignatureNow(font) != self._docSignature:
            self._panelFont = font
            self._syncPanelToFont(font)
        try:
            glyph = layer.parent
            name = glyph.name if glyph is not None else None
        except Exception:
            name = None
        if name is None:
            return
        row = self._rowForLayer(layer)
        if row is None:
            self._updateReadout(font, name, None, None)
            return
        pv = self._previewFor(font, name, row)
        if not pv:
            return
        self._draw(layer, pv)
        self._updateReadout(font, name, pv, row)

    # Older Glyphs builds call the non-options variant.
    @objc.python_method
    def drawForegroundForLayer_(self, layer):
        self.foreground(layer)

    @objc.python_method
    def _redraw(self):
        try:
            self.controller.redraw()
        except Exception:
            try:
                Glyphs.redraw()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # panel
    # ------------------------------------------------------------------

    @objc.python_method
    def _build_panel(self):
        w = FloatingWindow((PANEL_W, 470), "Slant Master", closable=True)
        self._panel = w
        # The red X means "turn the reporter off", not "hide the panel".
        w.bind("close", self._panelClosed)
        y = 12
        # One row per source: tick it to slant it, at its own angle.
        angles = NSNumberFormatter.alloc().init()
        angles.setNumberStyle_(NSNumberFormatterDecimalStyle)
        angles.setUsesGroupingSeparator_(False)
        angles.setMaximumFractionDigits_(3)
        w.list = List((12, y, -12, 172), [],
                      columnDescriptions=[
                          dict(title="", key="use", cell=CheckBoxListCell(), width=22),
                          dict(title="Source", key="label", editable=False),
                          dict(title="Angle°", key="angle", width=60, formatter=angles),
                      ],
                      editCallback=self._rowsEdited,
                      allowsMultipleSelection=False, allowsSorting=False)
        y += 180
        w.allLabel = TextBox((12, y, 110, 20), "Set all angles to")
        w.allField = EditText((124, y, 50, 22), "%g" % DEFAULT_ANGLE)
        w.allButton = Button((182, y - 1, 60, 24), "Set", callback=self._setAllAngles)
        y += 30
        w.widthLabel = TextBox((12, y, 34, 20), "W %")
        w.widthField = EditText((46, y, 46, 22), "%g" % self.widthPct, callback=self._paramsChanged)
        w.heightLabel = TextBox((104, y, 34, 20), "H %")
        w.heightField = EditText((138, y, 46, 22), "%g" % self.heightPct, callback=self._paramsChanged)
        y += 30
        w.originLabel = TextBox((12, y, 60, 20), "Origin:")
        w.originPop = PopUpButton((74, y, -12, 22), list(slant_math.PIVOT_CHOICES),
                                  callback=self._paramsChanged)
        try:
            w.originPop.set(slant_math.PIVOT_CHOICES.index(self.pivotChoice))
        except Exception:
            pass
        y += 30
        w.decomposeBox = CheckBox((12, y, -12, 20), "Decompose components",
                                  value=self.decompose, callback=self._paramsChanged)
        y += 22
        w.extremaBox = CheckBox((12, y, -12, 20), "Fix extrema (insert/remove — changes node counts)",
                                value=self.fixExtrema, callback=self._paramsChanged)
        y += 22
        w.italicBox = CheckBox((12, y, -12, 20), "Set italic angle on new master",
                               value=self.setItalic, callback=self._paramsChanged)
        y += 24
        w.axisLabel = TextBox((12, y + 2, 222, 20), "", sizeStyle="small")
        w.axisField = EditText((238, y, 60, 22), "", callback=self._paramsChanged)
        y += 32
        w.matchBox = CheckBox((12, y, -12, 20), "Match widths", value=self.matchWidths,
                              callback=self._paramsChanged)
        y += 24
        w.refLabel = TextBox((12, y, 60, 20), "Reference:")
        w.refPop = PopUpButton((74, y, -12, 22), [""], callback=self._refChanged)
        y += 30
        w.spacingLabel = TextBox((12, y, 60, 20), "Spacing:")
        w.spacingPop = PopUpButton((74, y, -12, 22), list(slant_math.SPACING_MODES),
                                   callback=self._paramsChanged)
        try:
            w.spacingPop.set(int(self.spacingMode))
        except Exception:
            pass
        y += 30
        w.offsetLabel = TextBox((12, y, 80, 20), "Adv offset:")
        w.offsetField = EditText((94, y, 60, 22), "%g" % self.advOffset, callback=self._paramsChanged)
        y += 30
        w.suffixLabel = TextBox((12, y, 86, 20), "Name suffix:")
        w.suffixField = EditText((100, y, -12, 22), self.nameSuffix, callback=self._paramsChanged)
        y += 32
        w.applyButton = Button((12, y, 90, 24), "Apply", callback=self._apply)
        w.refreshButton = Button((110, y, 90, 24), "Refresh", callback=self._refresh)
        w.removeButton = Button((208, y, 120, 24), "Remove last", callback=self._removeLast)
        y += 32
        w.readout = TextBox((12, y, -12, 16), "", sizeStyle="small")
        y += 18
        w.status1 = TextBox((12, y, -12, 16), "", sizeStyle="small")
        y += 18
        w.status2 = TextBox((12, y, -12, 58), "", sizeStyle="small")  # wraps: a batch has more to say
        y += 64
        try:
            w.resize(PANEL_W, y)
        except Exception:
            pass
        try:
            self._panelFont = self._currentFont()
            self._syncPanelToFont(self._panelFont)
        except Exception:
            # A failure building the rows must NOT stop w.open().
            _dbgexc("panel: sync FAILED: ")
        w.open()
        ns = self._nswindow()
        # Keep the window out of macOS session restoration.
        ns.setRestorable_(False)
        ns.disableSnapshotRestoration()
        ns.orderOut_(None)

    @objc.python_method
    def _popIndex(self, pop, items):
        sel = pop.get()
        if isinstance(sel, (int, float)):
            return int(sel)
        try:
            return items.index(sel)
        except ValueError:
            return 0

    @objc.python_method
    def _syncPanelToFont(self, font):
        """(Re)build the source list, the reference popup and the axis
        row; drops the caches. Runs on font change, on a master/instance
        count change anywhere, and after Apply."""
        if self._panel is None or font is None:
            return
        w = self._panel
        self._rebuildEntries(font)
        try:
            w.refPop.setItems([e["label"] for e in self._refEntries] or ["(nothing to reference)"])
            r = self._refEntry()
            if r is not None:
                w.refPop.set(self._refEntries.index(r))
        except Exception:
            _dbgexc("sync popups: ")
        self._showRows()
        self._rowsChanged()
        self._invalidateAll()

    @objc.python_method
    def _showRows(self):
        """Fill the list from the rows. Filling fires the edit callback
        just as typing does, hence the flag."""
        self._fillingRows = True
        try:
            self._panel.list.set([{"use": r["use"], "label": r["entry"]["label"], "angle": r["angle"]}
                                  for r in self._rows])
        finally:
            self._fillingRows = False

    @objc.python_method
    def _rowsChanged(self):
        """What follows the ticks and the angles: the Axis default, the
        count on Apply, the preview."""
        self._showAxisDefault()
        self._panel.applyButton.setTitle("Apply (%d)" % len(self._tickedRows()))
        self._previewCache.clear()

    @objc.python_method
    def _showAxisDefault(self, skip=None):
        """Label the Axis row and, until something else is typed there,
        keep its default in step with the rows. `skip` is the field being
        typed in, which is left alone."""
        w = self._panel
        font = self._currentFont()
        if font is None:
            return
        idx, tag = self._italAxis(font)
        if idx is None:
            w.axisLabel.set("no ital/slnt axis: coordinates = the source's")
            w.axisField.set("")
            w.axisField.enable(False)
            self._axisShown = None
            return
        w.axisLabel.set("%s axis value for every new master:" % tag)
        w.axisField.enable(True)
        if self.axisValue is None and skip is not w.axisField:
            self._axisShown = self._axisDefault(tag)
            w.axisField.set(self._axisShown)

    @objc.python_method
    def _floatField(self, field, default):
        try:
            text = field.get().strip()
            return float(text) if text else default
        except Exception:
            return default

    @objc.python_method
    def _readParams(self):
        w = self._panel
        if w is None:
            return
        self.widthPct = self._floatField(w.widthField, 100.0)
        self.heightPct = self._floatField(w.heightField, 100.0)
        try:
            self.pivotChoice = slant_math.PIVOT_CHOICES[self._popIndex(w.originPop, list(slant_math.PIVOT_CHOICES))]
        except Exception:
            pass
        self.decompose = bool(w.decomposeBox.get())
        self.fixExtrema = bool(w.extremaBox.get())
        self.setItalic = bool(w.italicBox.get())
        self.matchWidths = bool(w.matchBox.get())
        try:
            self.spacingMode = self._popIndex(w.spacingPop, list(slant_math.SPACING_MODES))
        except Exception:
            pass
        self.advOffset = self._floatField(w.offsetField, 0.0)
        # The Axis field is pre-filled with its default, so only something
        # ELSE counts as typed — read the default back as a choice and it
        # stops following the angles.
        text = w.axisField.get().strip()
        self.axisValue = None if text in ("", self._axisShown) else text
        self.nameSuffix = w.suffixField.get().strip() or DEFAULT_SUFFIX

    @objc.python_method
    def _setStatus(self, line1, line2=""):
        if self._panel is None:
            return
        try:
            self._panel.status1.set(line1)
            self._panel.status2.set(line2)
        except Exception:
            pass

    # --- callbacks ---------------------------------------------------------

    @objc.python_method
    def _paramsChanged(self, sender):
        self._readParams()
        self._showAxisDefault(skip=sender)
        self._previewCache.clear()
        self._redraw()

    @objc.python_method
    def _rowsEdited(self, sender):
        """A tick or an angle changed in the list. An angle cell left
        empty has no number in it: the row keeps the angle it had."""
        if self._fillingRows:
            return
        kept = []
        for row, item in zip(self._rows, sender.get()):
            row["use"] = bool(item["use"])
            self._ticks[row["entry"]["key"]] = row["use"]
            try:
                angle = float(item["angle"])
            except (TypeError, ValueError):
                self._fillingRows = True
                try:
                    item["angle"] = row["angle"]
                finally:
                    self._fillingRows = False
                kept.append(row)
                continue
            self._setRowAngle(row, angle)
        if kept:
            self._setStatus("an angle has to be a number — kept %s"
                            % ", ".join("%g° for %s" % (r["angle"], r["entry"]["name"]) for r in kept))
        self._rowsChanged()
        self._redraw()

    @objc.python_method
    def _setAllAngles(self, sender):
        text = self._panel.allField.get().strip()
        try:
            angle = float(text)
        except ValueError:
            self._setStatus("an angle has to be a number: %s" % (text or "(empty)"))
            return
        for row in self._rows:
            self._setRowAngle(row, angle)
        self._showRows()
        self._rowsChanged()
        self._setStatus("every source set to %g°" % angle)
        self._redraw()

    @objc.python_method
    def _refChanged(self, sender):
        idx = self._popIndex(sender, [e["label"] for e in self._refEntries])
        if 0 <= idx < len(self._refEntries):
            self.refKey = self._refEntries[idx]["key"]
        self._previewCache.clear()
        self._redraw()

    @objc.python_method
    def _refresh(self, sender):
        self._invalidateAll()
        self._panelFont = None  # forces a resync on the next draw
        self._setStatus("caches dropped")
        self._redraw()

    # ------------------------------------------------------------------
    # apply
    # ------------------------------------------------------------------

    @objc.python_method
    def _spaceLayer(self, tgt, glyphName, ref, refNames, ratio, plan, unmatched):
        """Width Matcher's contract against the reference layer. Read after
        the master's italic angle is set, so LSB/RSB/ink are measured
        along the slant."""
        refLayer = self._refLayerFor(ref, refNames, glyphName)
        if refLayer is None:
            unmatched.append(glyphName)
            return
        try:
            refAdv = float(refLayer.width) * ratio
            refLSB = float(refLayer.LSB) * ratio
            refRSB = float(refLayer.RSB) * ratio
        except Exception:
            unmatched.append(glyphName)
            return
        mode, offset = self.spacingMode, self.advOffset
        if self._inkRect(tgt) is None:
            tgt.width = slant_math.empty_advance(mode, refAdv, offset)
            return
        ink = float(tgt.width) - float(tgt.LSB) - float(tgt.RSB)
        lsb, rsb, adv = slant_math.target_spacing(mode, refLSB, refRSB, refAdv, ink, offset)
        # LSB first: its setter moves the outline; the second write then
        # pins the other side (RSB) or the advance itself.
        tgt.LSB = lsb
        if mode == slant_math.SPACING_REF_SB:
            tgt.RSB = rsb
        else:
            tgt.width = adv
        plan[glyphName] = (lsb, rsb)

    @objc.python_method
    def _auditSpacing(self, font, masterId, plan):
        """Glyphs re-applies metrics keys when interface updates resume, so
        a sidebearing set a moment ago can be rewritten. Count them."""
        if not masterId or not plan:
            return 0
        drifted = 0
        for name, (wantLSB, wantRSB) in plan.items():
            try:
                g = font.glyphs[name]
                L = g.layers[masterId] if g is not None else None
                if L is None:
                    continue
                if abs(float(L.LSB) - wantLSB) > 0.5 or abs(float(L.RSB) - wantRSB) > 0.5:
                    drifted += 1
            except Exception:
                continue
        return drifted

    @objc.python_method
    def _some(self, names, limit=8):
        return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")

    @objc.python_method
    def _apply(self, sender):
        """One new master per ticked row. Everything that can refuse is
        checked before the first master is appended."""
        font = self._currentFont()
        if font is None:
            self._setStatus("no font")
            return
        self._readParams()
        rows = self._tickedRows()
        if not rows:
            self._setStatus("nothing is ticked")
            return
        names = [self._newName(r["entry"]) for r in rows]
        existing = [str(m.name) for m in font.masters]
        taken = [n for i, n in enumerate(names)
                 if (n in existing or names.count(n) > 1) and n not in names[:i]]
        if taken:
            self._setStatus("name in use: %s" % self._some(taken),
                            "nothing was created — untick those rows or change the name suffix")
            return
        italIdx, italTag = self._italAxis(font)
        italValue = None
        if italIdx is not None:
            text = self.axisValue if self.axisValue is not None else self._axisDefault(italTag)
            try:
                italValue = float(text)
            except ValueError:
                self._setStatus("the %s axis value has to be a number: %s" % (italTag, text),
                                "nothing was created")
                return
        # Interpolated sources and references are cached for the overlay;
        # what Apply writes has to come from the outlines as they are now.
        self._interpCache.clear()
        reports = []
        try:
            font.disableUpdateInterface()
        except Exception:
            pass
        try:
            for row, name in zip(rows, names):
                reports.append(self._applyOne(font, row, name, italIdx, italValue))
        finally:
            try:
                font.enableUpdateInterface()
            except Exception:
                pass
        self._lastCreatedMasterIds = [r["id"] for r in reports if r["id"]]
        for r in reports:
            r["drifted"] = self._auditSpacing(font, r["id"], r["plan"])
        self._showReport(reports, self._refEntry() if self.matchWidths else None, italIdx)
        self._panelFont = None  # resync: the master list changed
        self._invalidateAll()
        self._redraw()

    @objc.python_method
    def _applyOne(self, font, row, name, italIdx, italValue):
        """One ticked row → one new master, slanted by the row's angle.
        Returns what happened, for the report: a glyph that fails is
        named there and the rest still land."""
        src, angle = row["entry"], row["angle"]
        rep = {"name": name, "id": None, "copied": 0, "verified": 0, "error": None,
               "failed": [], "logs": [], "plan": {}, "unmatched": [], "incompatible": [],
               "drifted": 0,
               "totals": {"inserted": 0, "removed": 0, "gated": 0, "balanced": 0,
                          "line_kept": 0, "components_decomposed": 0}}
        srcMaster = self._entryMaster(src)
        if srcMaster is None:
            rep["error"] = "interpolation failed"
            return rep
        try:
            srcAxes = [float(v) for v in src["obj"].axes]
        except Exception:
            srcAxes = self._masterAxes(srcMaster)
        matrix = self._matrixFor(srcMaster, angle)
        ref = self._refFor(src)
        refNames = self._entryGlyphNames(ref) if ref is not None else set()
        ratio = self._upmRatio(font, ref) if ref is not None else 1.0
        landed = {}  # glyph name -> (paths, components) of the layer put there
        try:
            newMaster = self._detach(srcMaster)
            newMaster.name = name
            font.masters.append(newMaster)
            newMaster = font.masters[-1]
            newId = rep["id"] = newMaster.id
            # A master made here is not a source for the next Apply.
            self._ticks["%s|master:%s" % (self._fontKey(font), name)] = False
            axes = list(srcAxes)
            if italIdx is not None and italIdx < len(axes):
                axes[italIdx] = italValue
            try:
                newMaster.axes = axes
            except Exception:
                _dbgexc("axes: ")
            # The angle first: from here on Glyphs measures this master's
            # sidebearings along the slant, which the contracts rely on.
            if self.setItalic:
                try:
                    newMaster.italicAngle = float(angle)
                except Exception:
                    _dbgexc("italicAngle: ")
            if abs(self.heightPct - 100.0) > 1e-9:
                sy = self.heightPct / 100.0
                for attr in ("xHeight", "capHeight", "ascender", "descender"):
                    try:
                        setattr(newMaster, attr, float(getattr(newMaster, attr)) * sy)
                    except Exception:
                        pass
            for g in font.glyphs:
                srcLayer = self._entryLayer(src, g.name)
                if srcLayer is None:
                    continue
                work = self._workingCopy(srcLayer, self.decompose)
                if work is None:
                    continue
                try:
                    g.beginUndo()
                except Exception:
                    pass
                try:
                    work.layerId = newId
                    work.associatedMasterId = newId
                    # Onto the font's grid (0: the font asks for none) — a
                    # shear leaves every node and anchor between grid points.
                    st = slant_paths.slant_layer(work, matrix, fix_extrema=self.fixExtrema,
                                                 keep_components=not self.decompose,
                                                 log=rep["logs"].append, grid=float(font.gridLength))
                    g.layers[newId] = work
                    landed[g.name] = (len(work.paths), len(work.components))
                    rep["copied"] += 1
                    for k in rep["totals"]:
                        rep["totals"][k] += st.get(k, 0)
                    if st["structure_changed"]:
                        rep["incompatible"].append(g.name)
                    if ref is not None:
                        self._spaceLayer(g.layers[newId], g.name, ref, refNames, ratio,
                                         rep["plan"], rep["unmatched"])
                except Exception as exc:
                    rep["failed"].append((g.name, "%s: %s" % (type(exc).__name__, exc)))
                    print("Slant Master — %s, glyph %s:\n%s" % (name, g.name, traceback.format_exc()))
                finally:
                    try:
                        g.endUndo()
                    except Exception:
                        pass
        except Exception as exc:
            rep["error"] = "%s: %s" % (type(exc).__name__, exc)
            print("Slant Master — %s:\n%s" % (name, traceback.format_exc()))
        # Prove the layers landed on THIS master rather than trusting the
        # assignment. Appending a master gives every glyph an empty layer
        # for it, so a layer being there proves nothing — it has to hold
        # what was put there.
        for gname, shapes in landed.items():
            try:
                L = font.glyphs[gname].layers[rep["id"]]
                if (L is not None and str(L.layerId) == str(rep["id"])
                        and (len(L.paths), len(L.components)) == shapes):
                    rep["verified"] += 1
            except Exception:
                _dbgexc("verify %s: " % gname)
        for msg in rep["logs"]:
            print("Slant Master — %s: %s" % (name, msg))
        return rep

    @objc.python_method
    def _showReport(self, reports, ref, italIdx):
        made = [r for r in reports if r["id"]]
        counts = sorted(set(r["copied"] for r in made))
        line1 = "%d master(s) created" % len(made)
        if len(counts) == 1:
            line1 += ", %d glyphs each" % counts[0]
        elif counts:
            line1 += ", %d to %d glyphs" % (counts[0], counts[-1])
        notes = []
        for r in reports:
            wrong = []
            if r["error"]:
                wrong.append(r["error"])
            if r["failed"]:
                wrong.append("%d glyph(s) failed: %s (%s)" % (
                    len(r["failed"]), self._some([n for n, _ in r["failed"]]), r["failed"][0][1]))
            if r["verified"] != r["copied"]:
                wrong.append("only %d of %d layers landed" % (r["verified"], r["copied"]))
            if r["logs"]:
                wrong.append("%d extrema step(s) fell back or failed" % len(r["logs"]))
            if wrong:
                notes.append("%s — %s" % (r["name"], "; ".join(wrong)))
        if notes:
            line1 += " — %d with problems" % len(notes)
            notes.append("details in the Macro panel")
        if ref is not None:
            notes.append("widths vs %s" % (ref["label"][0].lower() + ref["label"][1:]
                                           if ref is OWN_SOURCE else ref["label"]))
            unmatched = sum(len(r["unmatched"]) for r in reports)
            if unmatched:
                notes.append("%d unmatched" % unmatched)
        if italIdx is None:
            notes.append("no ital/slnt axis: coordinates = the source's")
        totals = {}
        for r in reports:
            for k, v in r["totals"].items():
                totals[k] = totals.get(k, 0) + v
        ex = slant_paths.extrema_summary(totals)
        if ex:
            notes.append(ex)
        incompatible = []
        for r in reports:
            incompatible += [n for n in r["incompatible"] if n not in incompatible]
        if incompatible:
            notes.append("%d glyph(s) changed node structure (won't interpolate with the uprights): %s"
                         % (len(incompatible), self._some(incompatible)))
        if totals.get("components_decomposed"):
            notes.append("%d component(s) decomposed (non-identity transform)" % totals["components_decomposed"])
        drifted = sum(r["drifted"] for r in reports)
        if drifted:
            notes.append("%d drifted after update (metrics keys)" % drifted)
        if made:
            notes.append("master append is not undoable — Remove last deletes them")
        self._setStatus(line1, " · ".join(notes))

    @objc.python_method
    def _removeLast(self, sender):
        """Delete the masters the last Apply created."""
        font = self._currentFont()
        ids = self._lastCreatedMasterIds
        if font is None or not ids:
            self._setStatus("nothing to remove (only masters created this session)")
            return
        removed, stuck = [], []
        for m in [m for m in font.masters if m.id in ids]:
            mname = str(m.name)
            try:
                del font.masters[list(font.masters).index(m)]
            except Exception:
                try:
                    font.masters.remove(m)
                except Exception:
                    _dbgexc("remove master: ")
                    stuck.append(m)
                    continue
            removed.append(mname)
        self._lastCreatedMasterIds = [m.id for m in stuck]
        self._panelFont = None
        self._invalidateAll()
        if stuck:
            self._setStatus("could not remove %s — use Font Info › Masters"
                            % self._some([str(m.name) for m in stuck]))
        elif removed:
            self._setStatus("removed %d master(s): %s" % (len(removed), self._some(removed)))
        else:
            self._setStatus("the created masters are no longer in the font")
        self._redraw()

    # ------------------------------------------------------------------
    # boilerplate
    # ------------------------------------------------------------------

    @objc.python_method
    def __file__(self):
        return __file__
