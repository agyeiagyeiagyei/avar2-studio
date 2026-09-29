# -*- coding: utf-8 -*-
"""Width Matcher — Glyphs 3 Reporter plugin.

Create a new master whose advance widths match another master's, by hand:

- Pick a REFERENCE master in the panel.
- Axis sliders define a working instance (kept in the font as the
  "Width Matcher Preview" instance — the same object Glyphs would
  interpolate for the Preview area, so what you see is what you get).
  Sliders span from the master minimum up to 3x the master maximum,
  so instances can extrapolate past the top of the design space.
- The panel shows an overlay preview of the current glyph — reference
  master (gray) vs. generated instance (blue), centered on the
  reference's ink — with markers at both advance boxes, plus numeric
  advance AND ink (outline extent) readouts. The ink delta is the
  matching target.
- The Spacing popup picks how the saved master is spaced: the
  reference's sidebearings verbatim, or the reference's advance (plus
  an optional Adv offset) with the sidebearings redistributed —
  proportional to the reference's, centred, or keeping its LSB.
- "Save as Master" interpolates the working instance and appends it to
  the font's masters, copying every glyph's interpolated layer across
  and re-spacing each per the chosen mode (empty glyphs take the
  reference advance, plus the offset in the advance modes). Every glyph
  is measured and planned before any is moved — see width_spacing.py —
  and the new layers are put on the font's grid.

Matching itself is manual: nudge the sliders until the ink delta reads
zero.
Width/outline data comes from `instance.interpolatedFont` (Glyphs' own
engine, brace layers and extrapolation included).

NOTE on regen triggering: the original design debounced regeneration
through NSTimer, but in this Glyphs build the regen timer callback never
fired (debug log: hundreds of "regen scheduled", zero "regen timer
fired"). Regeneration is therefore synchronous, throttled to at most
one run per 0.5 s during slider drags, with foreground() catching any
trailing dirty state once the drag settles.
"""

import copy as _copy
import os
import sys
import time
import traceback

import objc
from AppKit import (
    NSAffineTransform,
    NSBezierPath,
    NSColor,
    NSImage,
    NSImageView,
)
from GlyphsApp import *
from GlyphsApp.plugins import *

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import width_spacing  # noqa: E402
from width_spacing import SPACING_MODES, SPACING_REF_SB  # noqa: E402

try:
    from vanilla import (
        Button,
        EditText,
        FloatingWindow,
        Group,
        PopUpButton,
        Slider,
        TextBox,
    )
except ImportError:  # vanilla ships with Glyphs; guard for dev linting
    FloatingWindow = None


REF_GRAY = (0.65, 0.65, 0.65)
GEN_BLUE = (0.10, 0.45, 0.95)

WORKING_INSTANCE_NAME = "Width Matcher Preview"

# Synchronous regen is throttled: never more than one run per this many
# seconds during a slider drag; a trailing dirty flag is picked up by
# foreground() once the user pauses for REGEN_IDLE seconds.
REGEN_THROTTLE = 0.5
REGEN_IDLE = 0.35

# Panel preview size (points).
PREVIEW_W = 296
PREVIEW_H = 220

DEBUG = False  # flip to True for /tmp instrumentation while developing
_DEBUG_LOG = "/tmp/widthmatcher-debug.log"


def _dbgexc(prefix=""):
    """Log the CURRENT exception with its traceback.

    Bare ``_dbg("EXCEPTION")`` records that something failed but not what,
    which is useless when the failure is a panel that silently never opens.
    """
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
            if msg == "EXCEPTION":
                f.write(traceback.format_exc())
    except Exception:
        pass


def _rgba(rgb, a):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a)


class WidthMatcher(ReporterPlugin):
    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "Width Matcher"})
        self.keyboardShortcut = None
        self.referenceMasterId = None
        self.axisValues = []               # one float per font axis
        self._panel = None
        self._panelFont = None             # font the panel rows were built for
        self._previewView = None           # NSImageView inside the panel
        self._lastLayer = None
        self._previewGlyphName = None
        self._widthCache = {}              # glyphName -> generated advance width
        self._interpFont = None            # last interpolated font (outline source)
        self._interpMasterId = None
        self._dirty = False                # axis values changed since last regen
        self._lastChangeAt = 0.0
        self._lastRegenAt = 0.0
        self._axisRows = []                # [(label, slider, field), ...]
        self._masterItems = []             # popup titles, index-aligned w/ masters
        self._masterIds = []               # master ids, index-aligned w/ the titles
        self._masterName = None            # last used new-master name (persists)
        self._nameShown = None             # the default the name field was filled with
        self._status = ""                  # survives the panel being rebuilt
        self._spacingMode = SPACING_REF_SB  # how the saved master gets spaced
        self._advOffset = 0.0              # units added to the target advance
        self._loggedForeground = False      # one-shot foreground trace
        self._active = False               # mirrors the View toggle (willActivate)

    @objc.python_method
    def start(self):
        _dbg("start() called")
        # Deliberately NOT building the panel here: building it creates the
        # "Width Matcher Preview" instance in the font (via _syncPanelToFont
        # → _requestRegen → _workingInstance) — a side effect no one asked
        # for at launch. willActivate builds it when the user turns the
        # reporter on.
        self._forgetRestoredToggle()

    @objc.python_method
    def _nswindow(self):
        """The panel's real NSWindow (see CornerRadii: vanilla forwards
        isVisible but not orderOut_/makeKeyAndOrderFront_)."""
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
            self._dropWorkingInstance()
            self._redraw()
        except Exception:
            _dbgexc("willDeactivate: ")

    @objc.python_method
    def _forgetRestoredToggle(self):
        """Drop this reporter from Glyphs' ``visibleReporters`` default so
        the panel never opens on launch: Glyphs re-enables every reporter
        in that list when it starts, and these panels are wanted on demand
        only. The user's next View click puts it back for the session."""
        key = "visibleReporters"
        name = self.__class__.__name__
        try:
            current = Glyphs.defaults[key]
            if current and name in list(current):
                Glyphs.defaults[key] = [n for n in current if n != name]
                _dbg("start: dropped %s from %s" % (name, key))
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
        self._previewView = None
        self._dropWorkingInstance()
        try:
            Glyphs.deactivateReporter(self)
        except Exception:
            _dbgexc("panelClosed: ")

    # ------------------------------------------------------------------
    # font / layer resolution
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
    def _currentGlyph(self):
        if self._lastLayer is not None:
            try:
                return self._lastLayer.parent
            except Exception:
                pass
        try:
            layer = self.controller.activeLayer()
            if layer is not None:
                return layer.parent
        except Exception:
            pass
        return None

    @objc.python_method
    def _masterAxes(self, master):
        """Axis coordinates of a master as a plain list of floats."""
        try:
            return [float(v) for v in master.axes]
        except Exception:
            pass
        try:
            return [float(v) for v in master.axesValues()]
        except Exception:
            _dbg("EXCEPTION")
        return []

    @objc.python_method
    def _referenceMaster(self, font):
        if self.referenceMasterId is not None:
            for m in font.masters:
                if m.id == self.referenceMasterId:
                    return m
        try:
            m = font.selectedFontMaster
            if m is not None:
                self.referenceMasterId = m.id
                return m
        except Exception:
            pass
        if len(font.masters):
            self.referenceMasterId = font.masters[0].id
            return font.masters[0]
        return None

    # ------------------------------------------------------------------
    # working instance + interpolation
    # ------------------------------------------------------------------

    @objc.python_method
    def _workingInstance(self, font):
        """The preview instance, kept in font.instances while the tool is
        in use so the user sees it in Font Info and Glyphs' own
        interpolation applies. It is scratch: switched off for export,
        and taken out again when the panel goes (_dropWorkingInstance),
        so it is neither exported nor saved with the file."""
        for inst in font.instances:
            try:
                if inst.name == WORKING_INSTANCE_NAME:
                    inst.active = False  # one left in the file by an earlier version exports
                    return inst
            except Exception:
                pass
        try:
            inst = GSInstance()
            inst.name = WORKING_INSTANCE_NAME
            inst.active = False
            font.instances.append(inst)
            _dbg("working instance created")
            return inst
        except Exception:
            _dbg("EXCEPTION")
        return None

    @objc.python_method
    def _dropWorkingInstance(self):
        fonts = []
        for font in (self._panelFont, self._currentFont()):
            if font is not None and not any(font is f for f in fonts):
                fonts.append(font)
        for font in fonts:
            for inst in [i for i in font.instances if i.name == WORKING_INSTANCE_NAME]:
                font.instances.remove(inst)
        self._interpFont = None
        self._interpMasterId = None

    @objc.python_method
    def _applyAxisValues(self, inst):
        try:
            inst.axes = list(self.axisValues)
            readback = [float(v) for v in inst.axes]
            if readback != list(self.axisValues):
                _dbg("axes set drifted: wanted %r got %r"
                     % (list(self.axisValues), readback))
        except Exception:
            _dbg("EXCEPTION")

    @objc.python_method
    def _requestRegen(self):
        """Mark dirty and run a synchronous regen unless one happened
        very recently — during a continuous slider drag this yields one
        interpolation per REGEN_THROTTLE seconds; foreground() catches
        the trailing dirty state once the drag pauses."""
        self._dirty = True
        self._lastChangeAt = time.time()
        if time.time() - self._lastRegenAt >= REGEN_THROTTLE:
            self._runRegen()

    @objc.python_method
    def _runRegen(self):
        self._dirty = False
        self._lastRegenAt = time.time()
        self._regenWidths()
        self._updatePreview()

    @objc.python_method
    def _regenWidths(self):
        """Re-interpolate the working instance and cache per-glyph
        advance widths. Keeps the interpolated font around as the
        outline source for the panel preview."""
        font = self._currentFont()
        if font is None:
            _dbg("regen: no current font")
            return
        inst = self._workingInstance(font)
        if inst is None:
            _dbg("regen: no working instance")
            return
        self._applyAxisValues(inst)
        t0 = time.time()
        try:
            interp = inst.interpolatedFont
        except Exception:
            _dbg("EXCEPTION")
            return
        if interp is None or not len(interp.masters):
            _dbg("regen: interpolatedFont returned no masters")
            return
        mid = interp.masters[0].id
        cache = {}
        for g in interp.glyphs:
            try:
                layer = g.layers[mid]
                if layer is None and len(g.layers):
                    layer = g.layers[0]
                if layer is not None:
                    cache[g.name] = float(layer.width)
            except Exception:
                _dbg("EXCEPTION")
        self._widthCache = cache
        self._interpFont = interp
        self._interpMasterId = mid
        _dbg("regen: %d widths in %.2fs" % (len(cache), time.time() - t0))

    @objc.python_method
    def _interpLayer(self, glyphName):
        """The generated instance's layer for a glyph, or None."""
        if self._interpFont is None or self._interpMasterId is None:
            return None
        try:
            g = self._interpFont.glyphs[glyphName]
            if g is None:
                return None
            layer = g.layers[self._interpMasterId]
            if layer is None and len(g.layers):
                layer = g.layers[0]
            return layer
        except Exception:
            _dbg("EXCEPTION")
        return None

    # ------------------------------------------------------------------
    # panel
    # ------------------------------------------------------------------

    @objc.python_method
    def _build_panel(self):
        w = FloatingWindow((320, 120), "Width Matcher", closable=True)
        self._panel = w
        # The red X means "turn the reporter off", not "hide the panel".
        w.bind("close", self._panelClosed)
        y = 12
        w.refLabel = TextBox((12, y, 70, 20), "Reference:")
        w.refPop = PopUpButton((82, y, 226, 22), [""],
                               callback=self._referenceChanged)
        y += 30
        self._axesY = y
        _dbg("panel: building rows")
        try:
            self._syncPanelToFont(self._currentFont())
        except Exception:
            # A failure building the rows must NOT stop w.open() — otherwise
            # the reporter has no window at all and no way back, which reads
            # to the user as "nothing happens when I turn it on".
            _dbgexc("panel: row build FAILED: ")
        w.open()
        _dbg("panel: opened")
        ns = self._nswindow()
        # Keep the window out of macOS session restoration.
        ns.setRestorable_(False)
        ns.disableSnapshotRestoration()
        # Build hidden, per start()'s contract.
        ns.orderOut_(None)

    @objc.python_method
    def _syncPanelToFont(self, font):
        """(Re)build the master popup, one slider row per font axis, the
        preview, and the readout/action block. Called on font change."""
        if self._panel is None:
            return
        w = self._panel
        # drop previous axis rows and bottom block
        for i, (label, slider, field) in enumerate(self._axisRows):
            for ctrl in (label, slider, field):
                try:
                    ctrl.getNSView().removeFromSuperview()
                except Exception:
                    pass
            # vanilla refuses setattr over an existing attribute
            # ("can't replace vanilla attribute") — delete them too
            for attr in ("axisLabel_%d" % i, "axisSlider_%d" % i,
                         "axisField_%d" % i):
                try:
                    delattr(w, attr)
                except Exception:
                    pass
        self._axisRows = []
        if self._previewView is not None:
            try:
                self._previewView.removeFromSuperview()
            except Exception:
                pass
            self._previewView = None
        for attr in ("previewBox", "readoutAdv", "readoutInk", "readoutPlan",
                     "spacingLabel", "spacingPop", "offsetLabel", "offsetField",
                     "offsetHint", "refEcho", "nameLabel",
                     "nameField", "saveButton", "refreshButton", "statusLine"):
            if hasattr(w, attr):
                try:
                    getattr(w, attr).getNSView().removeFromSuperview()
                except Exception:
                    pass
                delattr(w, attr)
        if font is None:
            return

        # reference master popup
        self._masterItems = self._masterTitles(font)
        self._masterIds = [m.id for m in font.masters]
        try:
            w.refPop.setItems(self._masterItems)
        except Exception:
            _dbg("EXCEPTION")
        ref = self._referenceMaster(font)
        if ref is not None:
            try:
                w.refPop.set(self._masterIds.index(ref.id))
            except Exception:
                pass

        # axis values default to the reference master's coordinates
        n_axes = len(font.axes)
        if len(self.axisValues) != n_axes:
            self.axisValues = (
                self._masterAxes(ref)[:n_axes] if ref is not None else [0.0] * n_axes
            )

        y = self._axesY
        for i, axis in enumerate(font.axes):
            values = [self._masterAxes(m)[i] for m in font.masters
                      if len(self._masterAxes(m)) > i]
            lo = min(values) if values else 0.0
            hi = max(values) if values else 100.0
            # Extrapolation: slider covers the master minimum up to
            # 3x the master maximum, so instances can extrapolate
            # past the top of the design space.
            hi = max(hi * 3.0, lo + 1.0)
            label = TextBox((12, y + 2, 100, 18), str(axis.name), sizeStyle="small")
            slider = Slider((116, y, 116, 20),
                            minValue=lo, maxValue=hi,
                            value=self.axisValues[i],
                            callback=self._sliderChanged)
            field = EditText((240, y, 68, 22), "%g" % self.axisValues[i],
                             callback=self._axisFieldChanged)
            setattr(w, "axisLabel_%d" % i, label)
            setattr(w, "axisSlider_%d" % i, slider)
            setattr(w, "axisField_%d" % i, field)
            self._axisRows.append((label, slider, field))
            y += 26
        y += 6

        # preview: NSImageView filling a vanilla Group (the Group does
        # vanilla's coordinate handling; the image view fills it)
        w.previewBox = Group((12, y, PREVIEW_W, PREVIEW_H))
        try:
            view = NSImageView.alloc().initWithFrame_(
                ((0, 0), (PREVIEW_W, PREVIEW_H)))
            view.setImageFrameStyle_(0)      # no frame
            view.setImageAlignment_(5)       # bottom (moot: the image fills the view)
            w.previewBox._nsObject.addSubview_(view)
            self._previewView = view
        except Exception:
            _dbg("EXCEPTION")
        y += PREVIEW_H + 6

        w.readoutAdv = TextBox((12, y, 296, 16), "", sizeStyle="small")
        y += 18
        w.readoutInk = TextBox((12, y, 296, 16), "", sizeStyle="small")
        y += 18
        # What the SAVED layer will actually carry — the live Adv readout
        # above reports the interpolated instance's own spacing, which the
        # save overwrites, so without this line the panel shows a number
        # you never get.
        w.readoutPlan = TextBox((12, y, 296, 16), "", sizeStyle="small")
        y += 22
        w.spacingLabel = TextBox((12, y, 70, 20), "Spacing:")
        w.spacingPop = PopUpButton((82, y, 226, 22), SPACING_MODES,
                                   callback=self._spacingChanged)
        try:
            w.spacingPop.set(self._spacingMode)
        except Exception:
            _dbg("EXCEPTION")
        y += 26
        w.offsetLabel = TextBox((12, y, 70, 20), "Adv offset:")
        w.offsetField = EditText((82, y, 70, 22), "%g" % self._advOffset,
                                 callback=self._offsetChanged)
        w.offsetHint = TextBox((160, y + 4, 148, 16), "units, advance modes",
                               sizeStyle="small")
        y += 30
        w.nameLabel = TextBox((12, y, 70, 20), "New name:")
        self._nameShown = self._defaultName(ref)
        w.nameField = EditText((82, y, 226, 22), self._masterName or self._nameShown)
        y += 30
        # Spell out where the spacing comes from: the popup is scrolled out
        # of sight by the time you press Save, and picking up the wrong
        # master's sidebearings is invisible until you inspect the result.
        w.refEcho = TextBox((12, y, 296, 16),
                            "spacing from: %s" % (ref.name if ref is not None else "-"),
                            sizeStyle="small")
        y += 20
        w.saveButton = Button((12, y, 140, 26), "Save as Master",
                              callback=self._saveAsMaster)
        w.refreshButton = Button((160, y, 90, 26), "Refresh",
                                 callback=self._refresh)
        y += 34
        # Filled from self._status: a Save rebuilds these rows (the master
        # list changed), and what it had to say must not go with them.
        w.statusLine = TextBox((12, y, 296, 72), self._status, sizeStyle="small")
        y += 80
        try:
            w.resize(320, y)
        except Exception:
            _dbg("EXCEPTION")  # resize failing leaves controls unreachable
        self._requestRegen()

    # ------------------------------------------------------------------
    # panel callbacks
    # ------------------------------------------------------------------

    @objc.python_method
    def _masterTitles(self, font):
        """One popup row per master. A popup keeps a single row per
        title, so masters that share a name are numbered."""
        seen, titles = {}, []
        for m in font.masters:
            name = str(m.name)
            seen[name] = seen.get(name, 0) + 1
            titles.append(name if seen[name] == 1 else "%s (%d)" % (name, seen[name]))
        return titles

    @objc.python_method
    def _popIndex(self, pop):
        """The row a popup shows. vanilla's PopUpButton.get() is the title
        in some builds and the index in others — accept both."""
        sel = pop.get()
        if isinstance(sel, (int, float)):
            return int(sel)
        try:
            return self._masterItems.index(sel)
        except ValueError:
            return 0

    @objc.python_method
    def _defaultName(self, ref):
        return "%s matched" % ref.name if ref is not None else "Matched"

    @objc.python_method
    def _referenceChanged(self, sender):
        font = self._currentFont()
        if font is None:
            return
        idx = self._popIndex(sender)
        if 0 <= idx < len(self._masterIds):
            self.referenceMasterId = self._masterIds[idx]
        ref = self._referenceMaster(font)
        w = self._panel
        if w is not None and hasattr(w, "refEcho"):
            w.refEcho.set("spacing from: %s" % (ref.name if ref is not None else "-"))
            # The name field is pre-filled; only something else was typed.
            if w.nameField.get() == self._nameShown:
                self._nameShown = self._defaultName(ref)
                w.nameField.set(self._nameShown)
        self._updatePreview()

    @objc.python_method
    def _spacingChanged(self, sender):
        try:
            self._spacingMode = int(sender.get())
        except Exception:
            self._spacingMode = SPACING_REF_SB
        _dbg("spacing mode -> %d" % self._spacingMode)
        self._updatePreview()

    @objc.python_method
    def _offsetChanged(self, sender):
        try:
            self._advOffset = float((sender.get() or "").strip() or 0)
        except (TypeError, ValueError):
            self._advOffset = 0.0   # keep typing usable; bad text reads as 0
        self._updatePreview()

    @objc.python_method
    def _refresh(self, sender):
        """Interpolate again: the preview keeps what it interpolated until
        a slider moves, so it does not see an edit to the masters."""
        self._runRegen()

    @objc.python_method
    def _rowIndex(self, sender, column):
        for i, row in enumerate(self._axisRows):
            if row[column] is sender:
                return i
        return None

    @objc.python_method
    def _sliderChanged(self, sender):
        i = self._rowIndex(sender, 1)
        if i is None:
            return
        self.axisValues[i] = float(sender.get())
        try:
            self._axisRows[i][2].set("%g" % self.axisValues[i])
        except Exception:
            pass
        self._requestRegen()

    @objc.python_method
    def _axisFieldChanged(self, sender):
        i = self._rowIndex(sender, 2)
        if i is None:
            return
        try:
            self.axisValues[i] = float(sender.get())
        except (TypeError, ValueError):
            return
        try:
            self._axisRows[i][1].set(self.axisValues[i])
        except Exception:
            pass
        self._runRegen()  # discrete edit — interpolate immediately

    @objc.python_method
    def _setStatus(self, text):
        self._status = text
        if self._panel is not None and hasattr(self._panel, "statusLine"):
            try:
                self._panel.statusLine.set(text)
            except Exception:
                pass

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
    # measuring and planning (shared by the preview and the save)
    # ------------------------------------------------------------------

    @objc.python_method
    def _layerOf(self, font, glyphName, masterId):
        g = font.glyphs[glyphName]
        return None if g is None else g.layers[masterId]

    @objc.python_method
    def _slant(self, master):
        """How Glyphs measures a master's sidebearings: along its italic
        angle, around half its x-height."""
        return (float(getattr(master, "italicAngle", 0.0) or 0.0),
                float(getattr(master, "xHeight", 0.0) or 0.0) / 2.0)

    @objc.python_method
    def _landing(self, interp, glyphName, work, broken, grid):
        """A glyph's interpolated layer as it will land: a copy of its
        own, on the grid. The glyphs it is built from come along, since
        measuring it means drawing them. ``work`` keeps what was made,
        ``broken`` what could not be — a glyph built from one of those
        fails with it."""
        if glyphName in broken:
            raise broken[glyphName]
        if glyphName not in work:
            work[glyphName] = None  # a glyph that contains itself ends here
            try:
                g = interp.glyphs[glyphName]
                if g is not None and len(g.layers):
                    layer = self._detach(g.layers[0])
                    width_spacing.round_layer(layer, grid)
                    work[glyphName] = layer
                    for comp in layer.components:
                        self._landing(interp, width_spacing.component_name(comp), work, broken, grid)
            except Exception as exc:
                del work[glyphName]
                broken[glyphName] = exc
                raise
        return work[glyphName]

    @objc.python_method
    def _plan(self, font, ref, master, glyphName, work):
        """Where a glyph's new layer goes (width_spacing.plan), or None
        when the reference has no layer for it — it then keeps the
        spacing it was interpolated with. ``master`` is the master the
        layer lands on, for the slant it is measured along."""
        refLayer = self._layerOf(font, glyphName, ref.id) if ref is not None else None
        if refLayer is None:
            return None
        angle, pivot = self._slant(master)
        span = width_spacing.ink_span(work[glyphName], work.get, angle, pivot)
        refAngle, refPivot = self._slant(ref)
        refSpan = width_spacing.ink_span(
            refLayer, lambda name: self._layerOf(font, name, ref.id), refAngle, refPivot)
        return {"ref": refSpan, "gen": span,
                "to": width_spacing.plan(self._spacingMode, self._advOffset, refSpan,
                                         float(refLayer.width), span,
                                         float(work[glyphName].width), float(font.gridLength))}

    @objc.python_method
    def _some(self, names, limit=8):
        return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")

    # ------------------------------------------------------------------
    # save as master
    # ------------------------------------------------------------------

    @objc.python_method
    def _saveAsMaster(self, sender):
        """Glyphs' 'Instance as Master', driven by the working instance:
        interpolate, append the master, put each glyph's layer on it.

        In three passes. Every glyph is copied, rounded, measured and
        planned first; only then is anything moved, each layer by its own
        shift with its components making up for the shift of what they
        draw; and what landed is checked last. Planned one glyph at a
        time, a composite is measured against a base that has moved
        already or has not landed yet."""
        font = self._currentFont()
        if font is None:
            self._setStatus("no font")
            return
        name = "Matched"
        try:
            name = self._panel.nameField.get().strip() or name
        except Exception:
            pass
        self._masterName = name  # keep it across the post-save rebuild
        existing = [m.name for m in font.masters]
        if name in existing:
            self._setStatus("name in use: %s" % name)
            _dbg("save: master name %r already exists" % name)
            return
        inst = self._workingInstance(font)
        if inst is None:
            self._setStatus("no instance")
            return
        # The reference is the row the popup shows. The id can point at a
        # master the user never chose — _referenceMaster falls back to
        # font.selectedFontMaster (whatever is active in the Edit view)
        # whenever no explicit choice has been made.
        try:
            idx = self._popIndex(self._panel.refPop)
            if 0 <= idx < len(self._masterIds):
                self.referenceMasterId = self._masterIds[idx]
        except Exception:
            _dbgexc("save: reference popup: ")
        ref = self._referenceMaster(font)
        _dbg("save: reference master %r (%s); axisValues=%r; spacing mode %d"
             % (None if ref is None else ref.name,
                None if ref is None else ref.id,
                list(self.axisValues), self._spacingMode))
        self._applyAxisValues(inst)
        grid = float(font.gridLength)
        work, broken, plans, landed = {}, {}, {}, {}
        failed, absent = [], []
        newMaster = None
        error = None

        def failure(glyphName, exc):
            failed.append((glyphName, "%s: %s" % (type(exc).__name__, exc)))
            print("Width Matcher — %s, glyph %s:\n%s" % (name, glyphName, traceback.format_exc()))

        try:
            font.disableUpdateInterface()
        except Exception:
            pass
        try:
            interp = inst.interpolatedFont
            if interp is None or not len(interp.masters):
                self._setStatus("interpolation failed")
                _dbg("save: interpolatedFont returned no masters")
                return
            newMaster = self._detach(interp.masters[0])
            newMaster.name = name
            font.masters.append(newMaster)
            newMaster = font.masters[-1]
            # The interpolated master does NOT reliably carry the
            # instance's axis coordinates — set them explicitly so the
            # new master sits where the sliders put it.
            try:
                newMaster.axes = list(self.axisValues)
                readback = [float(v) for v in newMaster.axes]
                if readback != list(self.axisValues):
                    _dbg("save: master axes drifted: wanted %r got %r"
                         % (list(self.axisValues), readback))
            except Exception:
                _dbg("EXCEPTION")
            # 1. copy, round, measure and plan — nothing in the font moves
            for g in font.glyphs:
                try:
                    if self._landing(interp, g.name, work, broken, grid) is None:
                        absent.append(g.name)
                        continue
                    plans[g.name] = self._plan(font, ref, newMaster, g.name, work)
                except Exception as exc:
                    failure(g.name, exc)
            shifts = dict((n, p["to"]["shift"] if p else 0.0) for n, p in plans.items())
            # 2. move and land
            for g in font.glyphs:
                if g.name not in plans:
                    continue
                layer, plan = work[g.name], plans[g.name]
                try:
                    # Re-key: the copy still identifies as the interpolated
                    # font's master, so Glyphs would file it under the wrong
                    # id and the Edit view would show an empty master.
                    layer.layerId = newMaster.id
                    layer.associatedMasterId = newMaster.id
                    width_spacing.shift_layer(layer, shifts[g.name],
                                              lambda base: shifts.get(base, 0.0), grid)
                    layer.width = plan["to"]["width"] if plan else width_spacing.snap(float(layer.width), grid)
                    g.layers[newMaster.id] = layer
                    landed[g.name] = width_spacing.shapes(layer)
                except Exception as exc:
                    failure(g.name, exc)
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            print("Width Matcher — %s:\n%s" % (name, traceback.format_exc()))
        finally:
            try:
                font.enableUpdateInterface()
            except Exception:
                pass
        if newMaster is None or getattr(newMaster, "id", None) not in [m.id for m in font.masters]:
            self._setStatus("error — nothing was saved: %s" % (error or "the master could not be added"))
            return
        # 3. Prove the layers landed on THIS master rather than trusting
        # the assignment. Appending a master gives every glyph an empty
        # layer for it, so a layer being there proves nothing — it has to
        # hold what was put there.
        verified = 0
        for glyphName, shapes in landed.items():
            L = self._layerOf(font, glyphName, newMaster.id)
            if (L is not None and str(L.layerId) == str(newMaster.id)
                    and width_spacing.shapes(L) == shapes):
                verified += 1
        # Measured again now that the interface is live: Glyphs re-applies
        # metrics keys and automatic alignment when updates resume, and
        # most of this font's glyphs are keyed off another (=H, =O), so a
        # layer can be moved underneath us.
        moved = self._movedSince(font, newMaster, plans, landed)
        notes = ["saved %s: %d glyphs" % (name, len(landed))]
        if error:
            notes.append("stopped by %s" % error)
        if verified != len(landed):
            notes.append("only %d of %d layers landed" % (verified, len(landed)))
        if failed:
            notes.append("%d glyph(s) failed: %s (%s)"
                         % (len(failed), self._some([n for n, _ in failed]), failed[0][1]))
        if absent:
            notes.append("%d not in the instance: %s" % (len(absent), self._some(absent)))
        unfit = [n for n in landed if plans.get(n) and not plans[n]["to"]["fits"]]
        if unfit:
            notes.append("%d kept the spacing they were interpolated with, the reference's "
                         "leaves them no room: %s" % (len(unfit), self._some(unfit)))
        if moved:
            notes.append("%d moved after the save: %s (metrics keys or automatic alignment)"
                         % (len(moved), self._some(moved)))
        if failed or error:
            notes.append("details in the Macro panel")
        _dbg("save: " + "; ".join(notes))
        self._syncPanelToFont(font)
        self._setStatus("; ".join(notes))
        self._redraw()

    @objc.python_method
    def _movedSince(self, font, master, plans, landed):
        """The glyphs whose layer no longer measures what was planned for
        it. Measured from the outline, against the planned values: those
        are on the grid already, so anything over half a unit is a move
        and not a rounding."""
        angle, pivot = self._slant(master)
        moved = []
        for glyphName in landed:
            plan = plans.get(glyphName)
            if not plan:
                continue
            L = self._layerOf(font, glyphName, master.id)
            if L is None:
                continue
            span = width_spacing.ink_span(
                L, lambda name: self._layerOf(font, name, master.id), angle, pivot)
            if abs(float(L.width) - plan["to"]["width"]) > 0.5 or (
                    span is not None and plan["to"]["lsb"] is not None
                    and abs(span[0] - plan["to"]["lsb"]) > 0.5):
                moved.append(glyphName)
        return moved

    # ------------------------------------------------------------------
    # preview rendering
    # ------------------------------------------------------------------

    @objc.python_method
    def _pathRect(self, path):
        """(minx, miny, maxx, maxy) for an NSBezierPath, or None."""
        if path is None:
            return None
        try:
            b = path.controlPointBounds()
            if b.size.width == 0 and b.size.height == 0:
                return None
            return (b.origin.x, b.origin.y,
                    b.origin.x + b.size.width, b.origin.y + b.size.height)
        except Exception:
            return None

    @objc.python_method
    def _detach(self, obj):
        """A standalone copy of a master/layer from the interpolated font.

        ``interpolatedFont``'s masters and layers belong to THAT font. Handing
        them straight to the real font leaves it holding objects owned by a
        temporary: the next regen replaces ``_interpFont``, the interpolated
        font is released, and the saved master loses every layer — it appears
        in the master list with no glyphs at all. Copying first makes the real
        font the owner, so the master survives.
        """
        failure = None
        for attempt in (lambda: obj.copy(), lambda: _copy.copy(obj)):
            try:
                dup = attempt()
            except Exception as exc:
                failure = failure or exc
                continue
            if dup is not None:
                return dup
        # Never the original: that is the object the docstring warns about.
        raise failure or RuntimeError("could not copy %r" % obj)

    @objc.python_method
    def _updatePreview(self):
        """Render reference vs. generated glyph into the panel's image
        view, plus advance-width markers; refresh the numeric readout."""
        if self._panel is None:
            return
        font = self._currentFont()
        glyph = self._currentGlyph()
        if font is None or glyph is None or self._previewView is None:
            return
        ref = self._referenceMaster(font)
        if ref is None:
            return
        try:
            refLayer = glyph.layers[ref.id]
        except Exception:
            refLayer = None
        genLayer = self._interpLayer(glyph.name)
        refW = float(refLayer.width) if refLayer is not None else None
        genW = self._widthCache.get(glyph.name)
        # Measured and planned exactly as Save will, on a copy of the
        # glyph as it will land, so the line below is what Save writes.
        plan = None
        if self._interpFont is not None and len(self._interpFont.masters):
            try:
                work = {}
                if self._landing(self._interpFont, glyph.name, work, {}, float(font.gridLength)) is not None:
                    plan = self._plan(font, ref, self._interpFont.masters[0], glyph.name, work)
            except Exception:
                _dbgexc("preview plan: ")
        refInk = plan["ref"] if plan else None
        genInk = plan["gen"] if plan else None
        self._updateReadout(glyph.name, refW, genW, refInk, genInk, plan)

        W, H = PREVIEW_W, PREVIEW_H
        img = NSImage.alloc().initWithSize_((W, H))
        img.lockFocus()
        try:
            refPath = refLayer.bezierPath if refLayer is not None else None
            genPath = genLayer.bezierPath if genLayer is not None else None
            asc = float(ref.ascender)
            desc = float(ref.descender)

            # The generated glyph is drawn with its INK centered on the
            # reference's ink, not left-aligned at the origin — the user
            # matches outline extents (sidebearings are copied from the
            # reference at save time), so aligned outlines must read as
            # aligned in the overlay.
            genDx = 0.0
            if refInk is not None and genInk is not None:
                genDx = (refInk[0] + refInk[1]) / 2.0 \
                      - (genInk[0] + genInk[1]) / 2.0
            elif refW is not None and genW is not None:
                genDx = (refW - genW) / 2.0
            if genPath is not None and genDx:
                try:
                    sh = NSAffineTransform.transform()
                    sh.translateXBy_yBy_(genDx, 0)
                    genPath = sh.transformBezierPath_(genPath)
                except Exception:
                    _dbg("EXCEPTION")

            # union bounds: both outlines plus the full metric box
            # (width lines and asc/desc must fit even for empty glyphs)
            minx, miny, maxx, maxy = 0.0, desc, 0.0, asc
            for dx, wdt in ((0.0, refW), (genDx, genW)):
                if wdt is not None:
                    minx = min(minx, dx)
                    maxx = max(maxx, dx + wdt)
            for r in (self._pathRect(refPath), self._pathRect(genPath)):
                if r is None:
                    continue
                minx = min(minx, r[0])
                miny = min(miny, r[1])
                maxx = max(maxx, r[2])
                maxy = max(maxy, r[3])
            spanx = max(maxx - minx, 1.0)
            spany = max(maxy - miny, 1.0)
            scale = min(W / spanx, H / spany) * 0.9
            # center the union box in the view
            ox = (W - spanx * scale) / 2.0 - minx * scale
            oy = (H - spany * scale) / 2.0 - miny * scale

            t = NSAffineTransform.transform()
            t.translateXBy_yBy_(ox, oy)
            t.scaleBy_(scale)

            def xformed(path):
                if path is None:
                    return None
                try:
                    return t.transformBezierPath_(path)
                except Exception:
                    _dbg("EXCEPTION")
                    return None

            def vx(x):
                return x * scale + ox

            def vy(yv):
                return yv * scale + oy

            # metric lines: baseline + both edges of each advance box
            # (left edges matter now that the generated box is centered)
            line = NSBezierPath.bezierPath()
            line.setLineWidth_(0.5)
            _rgba((0.5, 0.5, 0.5), 0.35).set()
            line.moveToPoint_((vx(minx), vy(0)))
            line.lineToPoint_((vx(maxx), vy(0)))
            line.stroke()
            for dx, wdt, col in ((0.0, refW, REF_GRAY),
                                 (genDx, genW, GEN_BLUE)):
                if wdt is None:
                    continue
                for edge in (dx, dx + wdt):
                    marker = NSBezierPath.bezierPath()
                    marker.setLineWidth_(0.7)
                    _rgba(col, 0.55).set()
                    marker.moveToPoint_((vx(edge), vy(desc)))
                    marker.lineToPoint_((vx(edge), vy(asc)))
                    marker.stroke()

            rp = xformed(refPath)
            if rp is not None:
                _rgba(REF_GRAY, 0.30).set()
                rp.fill()
                _rgba(REF_GRAY, 0.75).set()
                rp.setLineWidth_(0.6)
                rp.stroke()
            gp = xformed(genPath)
            if gp is not None:
                _rgba(GEN_BLUE, 0.25).set()
                gp.fill()
                _rgba(GEN_BLUE, 0.85).set()
                gp.setLineWidth_(0.8)
                gp.stroke()
        except Exception:
            _dbg("EXCEPTION")
        finally:
            img.unlockFocus()
        try:
            self._previewView.setImage_(img)
        except Exception:
            _dbg("EXCEPTION")

    @objc.python_method
    def _updateReadout(self, glyphName, refW, genW, refInk, genInk, plan=None):
        """``refInk`` / ``genInk`` are (left, right) of the ink, ``plan``
        what _plan answered."""
        if self._panel is None or not hasattr(self._panel, "readoutAdv"):
            return
        if refW is None:
            adv = "%s — no layer in reference master" % glyphName
        elif genW is None:
            adv = "%s — Adv Ref %.0f · Gen …" % (glyphName, refW)
        else:
            adv = "%s — Adv Ref %.0f · Gen %.0f (Δ %+.0f)" % (
                glyphName, refW, genW, genW - refW)
        if refInk is None or genInk is None:
            ink = ""
        else:
            refInkW = refInk[1] - refInk[0]
            genInkW = genInk[1] - genInk[0]
            ink = "Ink Ref %.0f · Gen %.0f (Δ %+.0f)" % (
                refInkW, genInkW, genInkW - refInkW)
        # What Save as Master would actually produce. The Adv line above is
        # the interpolated instance's OWN spacing, which the save discards;
        # this is the number to trust.
        if not plan:
            planTxt = ""
        elif plan["to"]["lsb"] is None:
            planTxt = "Saved: no ink - Adv %.0f" % plan["to"]["width"]
        elif not plan["to"]["fits"]:
            planTxt = "Saved: as interpolated, Adv %.0f - no room for this spacing" % plan["to"]["width"]
        else:
            to = plan["to"]
            planTxt = "Saved: LSB %.0f - RSB %.0f - Adv %.0f" % (to["lsb"], to["rsb"], to["width"])
            if refW is not None:
                planTxt += " (%s %+.0f)" % ("Adv", to["width"] - refW)
        try:
            self._panel.readoutAdv.set(adv)
            self._panel.readoutInk.set(ink)
            if hasattr(self._panel, "readoutPlan"):
                self._panel.readoutPlan.set(planTxt)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # reporter entry point: bookkeeping only (no Edit-view drawing)
    # ------------------------------------------------------------------

    @objc.python_method
    def foreground(self, layer):
        """Glyphs 3 reporter draw entry (drawForegroundForLayer_* is the
        Glyphs 2 API). Nothing is drawn into the Edit view — the preview
        lives in the panel — but this is the reliable pulse for glyph
        tracking and trailing regens. Glyphs only calls it while the View
        toggle is on, so it needs no gate of its own."""
        self._lastLayer = layer
        if not self._loggedForeground:
            self._loggedForeground = True
            _ns0 = self._nswindow()
            _dbg("foreground: first call, panel=%s ns=%s visible=%s"
                 % (self._panel is not None,
                    _ns0 is not None,
                    "n/a" if _ns0 is None else _ns0.isVisible()))
        self._showPanel()  # lazy: also covers a build that skips willActivate

        font = self._currentFont()
        # Rebuild on a master-count change too, not just a font change:
        # _masterItems maps popup index -> font.masters[index], so adding or
        # deleting a master while the panel is open silently shifts every
        # selection after it and the reference resolves to the wrong master.
        if font is not None and (font is not self._panelFont
                                 or len(font.masters) != len(self._masterItems)):
            self._panelFont = font
            self._syncPanelToFont(font)

        # trailing regen once a slider drag has settled
        if self._dirty \
                and time.time() - self._lastChangeAt > REGEN_IDLE \
                and time.time() - self._lastRegenAt >= REGEN_THROTTLE:
            self._runRegen()

        # re-render the preview when the glyph on screen changed
        try:
            glyph = layer.parent
            name = glyph.name if glyph is not None else None
        except Exception:
            name = None
        if name != self._previewGlyphName:
            self._previewGlyphName = name
            self._updatePreview()

    # Older Glyphs builds call the non-options variant.
    @objc.python_method
    def drawForegroundForLayer_(self, layer):
        self.foreground(layer)

    # ------------------------------------------------------------------
    # boilerplate
    # ------------------------------------------------------------------

    @objc.python_method
    def __file__(self):
        return __file__
