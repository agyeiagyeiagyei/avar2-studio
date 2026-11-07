# -*- coding: utf-8 -*-
"""Metrics Parity — Glyphs 3 Reporter plugin (the bundle and its class keep
the identifier ParametricMasters, so links and preferences stay valid).

Audits parametric-master consistency: masters that share the axis values
driving horizontal metrics should share horizontal metrics. Masters are
grouped by a pair of axes (default XTRA + XOPQ — the horizontal
transparent and opaque factors), and any glyph whose advance width, LSB,
or RSB differs between masters of the same group is flagged.

- "Group by" popup offers every pair of the font's axes, so other
  hypotheses (e.g. XTRA + YOPQ) can be checked too.
- Font-wide by default; "Current glyph only" narrows the audit to the
  Edit view's glyph. Live updates while you draw (UPDATEINTERFACE,
  throttled to one scan per second, trailing scan on redraw).
- Only groups of 2+ masters are audited. Consensus is the first member's
  metrics; deviations over 1 unit are flagged.

Draws nothing into the Edit view — the report lives in the floating panel.
"""

import math
import time
import traceback

import objc
from GlyphsApp import *
from GlyphsApp.plugins import *

try:
    from vanilla import (
        Button,
        CheckBox,
        FloatingWindow,
        List,
        PopUpButton,
        TextBox,
    )
except ImportError:  # vanilla ships with Glyphs; guard for dev linting
    FloatingWindow = None


TOLERANCE = 1.0        # units; deviations beyond this are flagged
SCAN_THROTTLE = 1.0    # min seconds between live scans

DEBUG = False  # flip to True for /tmp instrumentation while developing
_DEBUG_LOG = "/tmp/parametricmasters-debug.log"


def _dbg(msg):
    if not DEBUG:
        return
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write("%s\n" % msg)
    except Exception:
        pass


def _dbgexc(prefix=""):
    if not DEBUG:
        return
    try:
        _dbg("%s%s" % (prefix, traceback.format_exc()))
    except Exception:
        pass


# ----------------------------------------------------------------------
# audit logic (plain font/master/layer access, no UI)
# ----------------------------------------------------------------------

def _master_axes(master):
    """Axis coordinates of a master as a list of floats."""
    try:
        return [float(v) for v in master.axes]
    except Exception:
        pass
    try:
        return [float(v) for v in master.axesValues()]
    except Exception:
        return []


def group_masters(font, idx_a, idx_b):
    """{(valA, valB): [master, ...]} for the axis pair — 2+ members only."""
    groups = {}
    for m in font.masters:
        axes = _master_axes(m)
        if len(axes) <= max(idx_a, idx_b):
            continue
        key = (round(axes[idx_a], 2), round(axes[idx_b], 2))
        groups.setdefault(key, []).append(m)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _axis_tags(font):
    """Axis tags (XTRA, XOPQ, …) index-aligned with a master's axes; the
    display name when a tag is missing."""
    tags = []
    for a in font.axes:
        tag = getattr(a, "axisTag", None) or getattr(a, "tag", None)
        tags.append(str(tag or a.name))
    return tags


# The metrics are measured from the outline, components drawn through,
# not read off layer.LSB / layer.RSB: Glyphs keeps those in whole units,
# so 26 and 24.95 read a unit apart and pass, and reading them re-aligns
# a composite as a side effect.

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_MAX_DEPTH = 8  # components of components; a glyph that contains itself ends here


def _compose(outer, inner):
    """The transform that applies ``inner`` first, then ``outer``."""
    return (
        outer[0] * inner[0] + outer[2] * inner[1],
        outer[1] * inner[0] + outer[3] * inner[1],
        outer[0] * inner[2] + outer[2] * inner[3],
        outer[1] * inner[2] + outer[3] * inner[3],
        outer[0] * inner[4] + outer[2] * inner[5] + outer[4],
        outer[1] * inner[4] + outer[3] * inner[5] + outer[5],
    )


def _segments(path):
    """A path's segments as point tuples: 4 points for a cubic, 2 for a
    line (any other run counts as the line between its on-curve ends)."""
    pts = [(float(n.position.x), float(n.position.y)) for n in path.nodes]
    on = [i for i, n in enumerate(path.nodes) if str(n.type) != "offcurve"]
    if len(on) < 2:
        return [(pts[i], pts[i]) for i in on]
    pairs = list(zip(on, on[1:]))
    if bool(path.closed):
        pairs.append((on[-1], on[0]))
    out = []
    for i, j in pairs:
        between = pts[i + 1:j] if j > i else pts[i + 1:] + pts[:j]
        out.append((pts[i], between[0], between[1], pts[j]) if len(between) == 2
                   else (pts[i], pts[j]))
    return out


def _outline(font, layer, master_id, transform=_IDENTITY, depth=0):
    """Every segment of a layer's drawn outline in one master."""
    t = transform
    segs = [tuple((t[0] * x + t[2] * y + t[4], t[1] * x + t[3] * y + t[5]) for x, y in seg)
            for path in layer.paths for seg in _segments(path)]
    if depth >= _MAX_DEPTH:
        return segs
    for comp in layer.components:
        base = font.glyphs[str(getattr(comp, "componentName", None) or comp.name)]
        held = base.layers[master_id] if base is not None else None
        if held is not None:
            segs += _outline(font, held, master_id,
                             _compose(t, tuple(float(v) for v in comp.transform)), depth + 1)
    return segs


def _turning_points(x0, x1, x2, x3):
    """x where a cubic turns back between its ends (dx/dt = 0)."""
    a = 3.0 * (x3 - 3.0 * x2 + 3.0 * x1 - x0)
    b = 6.0 * (x0 - 2.0 * x1 + x2)
    c = 3.0 * (x1 - x0)
    if abs(a) < 1e-9:
        roots = [] if abs(b) < 1e-9 else [-c / b]
    else:
        disc = b * b - 4.0 * a * c
        roots = [] if disc < 0 else [(-b + math.sqrt(disc)) / (2.0 * a),
                                     (-b - math.sqrt(disc)) / (2.0 * a)]
    return [(1 - t) ** 3 * x0 + 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3 * x3
            for t in roots if 1e-6 < t < 1.0 - 1e-6]


def metrics(font, layer, master):
    """(advance, LSB, RSB) of a layer, the sidebearings from its ink:
    along the master's italic angle, around half its x-height, which is
    how Glyphs measures them. A layer that draws nothing has none, and
    is compared by its advance."""
    tan = math.tan(math.radians(float(getattr(master, "italicAngle", 0.0) or 0.0)))
    pivot = float(getattr(master, "xHeight", 0.0) or 0.0) / 2.0
    xs = []
    for seg in _outline(font, layer, master.id):
        upright = [x - tan * (y - pivot) for x, y in seg]
        xs += [upright[0], upright[-1]]
        if len(upright) == 4:
            xs += _turning_points(*upright)
    width = float(layer.width)
    if not xs:
        return (width, 0.0, 0.0)
    return (width, min(xs), width - max(xs))


def _shown(value):
    """A deviation as the list shows it: whole when it is whole."""
    return "%.0f" % value if abs(value - round(value)) < 0.005 else "%.2f" % value


def _member_label(master, tags, pair_idx):
    """``name (YOPQ 275)``: the master plus its coordinates on every axis
    the group does NOT fix — the ones that tell its members apart."""
    axes = _master_axes(master)
    parts = [
        "%s %g" % (tags[i], axes[i])
        for i in range(min(len(tags), len(axes))) if i not in pair_idx
    ]
    return "%s (%s)" % (master.name, ", ".join(parts)) if parts else str(master.name)


def scan(font, groups, glyph_names, tags=None, pair_idx=()):
    """One row per (glyph, group) whose members disagree on adv/LSB/RSB.

    Consensus is the first member's values; the row reports the largest
    absolute deviation among the other members. The group is named by
    the pair's tagged values and the members carry the coordinates that
    distinguish them.
    """
    tags = tags or _axis_tags(font)
    rows = []
    for key, members in sorted(groups.items()):
        first_idx = list(font.masters).index(members[0])
        master_names = " · ".join(_member_label(m, tags, pair_idx) for m in members)
        if len(pair_idx) == 2 and max(pair_idx) < len(tags):
            group_label = "%s %g · %s %g" % (tags[pair_idx[0]], key[0], tags[pair_idx[1]], key[1])
        else:
            group_label = "%g · %g" % key
        for name in glyph_names:
            g = font.glyphs[name]
            if g is None:
                continue
            vals = []
            for m in members:
                layer = g.layers[m.id]
                if layer is None:
                    continue
                vals.append(metrics(font, layer, m))
            if len(vals) < 2:
                continue
            adv0, lsb0, rsb0 = vals[0]
            dadv = max(abs(v[0] - adv0) for v in vals[1:])
            dlsb = max(abs(v[1] - lsb0) for v in vals[1:])
            drsb = max(abs(v[2] - rsb0) for v in vals[1:])
            if max(dadv, dlsb, drsb) > TOLERANCE:
                rows.append({
                    "glyph": name,
                    "group": group_label,
                    "masters": master_names,
                    "dadv": _shown(dadv),
                    "dlsb": _shown(dlsb),
                    "drsb": _shown(drsb),
                    "_masterIndex": first_idx,
                })
    return rows


class ParametricMasters(ReporterPlugin):

    @objc.python_method
    def settings(self):
        self.menuName = Glyphs.localize({"en": "Metrics Parity"})
        self.keyboardShortcut = None
        self._panel = None
        self._panelFont = None
        self._lastLayer = None
        self._pairs = []                 # [(idxA, idxB, nameA, nameB)]
        self._pairItems = []             # popup titles, index-aligned
        self._pair = ("XTRA", "XOPQ")    # selected axes, by name or by tag
        self.currentGlyphOnly = False
        self.live = True
        self._dirty = False              # edits arrived since last scan
        self._lastScanAt = 0.0
        self._currentGlyphName = None
        self._active = False             # mirrors the View toggle (willActivate)

    @objc.python_method
    def start(self):
        self._forgetRestoredToggle()
        try:
            Glyphs.addCallback(self._updateInterface_, UPDATEINTERFACE)
        except Exception:
            _dbgexc("subscribe: ")

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
            self._scan()  # fill the list now, not on the next interface update
        except Exception:
            _dbgexc("willActivate: ")

    def willDeactivate(self):
        try:
            self._active = False
            self._hidePanel()
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

    # NOT @objc.python_method — Glyphs.addCallback needs an ObjC selector.
    def _updateInterface_(self, sender):
        if not self._active or not self.live:
            return
        self._dirty = True
        self._maybeScan()

    # --- font / layer resolution ---------------------------------------

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

    # --- panel -----------------------------------------------------------

    @objc.python_method
    def _nswindow(self):
        if self._panel is None:
            return None
        try:
            return self._panel.getNSWindow()
        except Exception:
            return None

    @objc.python_method
    def _build_panel(self):
        w = FloatingWindow((720, 420), "Metrics Parity", closable=True,
                           minSize=(560, 260))
        self._panel = w
        # The red X means "turn the reporter off", not "hide the panel" —
        # foreground() would re-show it on the next draw.
        w.bind("close", self._panelClosed)
        y = 12
        w.pairLabel = TextBox((12, y, 70, 20), "Group by:")
        w.pairPop = PopUpButton((82, y, 200, 22), [""],
                                callback=self._pairChanged)
        w.liveBox = CheckBox((300, y, 60, 20), "Live", value=self.live,
                             callback=self._liveChanged)
        w.refreshButton = Button((-92, y - 2, 80, 24), "Refresh",
                                 callback=self._refreshClicked)
        y += 28
        w.scopeBox = CheckBox((12, y, 200, 20), "Current glyph only",
                              value=self.currentGlyphOnly,
                              callback=self._scopeChanged)
        y += 24
        w.summary = TextBox((12, y, -12, 16), "", sizeStyle="small")
        y += 18
        # What the last double-click did — its own line, because every
        # scan rewrites the summary above.
        w.note = TextBox((12, y, -12, 16), "", sizeStyle="small")
        y += 22
        cols = [
            dict(title="Glyph", key="glyph", width=100, editable=False),
            dict(title="Group", key="group", width=150, editable=False),
            dict(title="Masters (distinguishing coords)", key="masters", width=270, editable=False),
            dict(title="Δadv", key="dadv", width=50, editable=False),
            dict(title="ΔLSB", key="dlsb", width=50, editable=False),
            dict(title="ΔRSB", key="drsb", width=50, editable=False),
        ]
        w.list = List((10, y, -10, -10), [],
                      columnDescriptions=cols,
                      doubleClickCallback=self._openGlyph,
                      allowsMultipleSelection=False,
                      autohidesScrollers=False)
        font = self._currentFont()
        if font is not None:
            self._panelFont = font
            self._syncPairsToFont(font)
        w.open()
        ns = self._nswindow()
        if ns is not None:
            # Keep the window out of macOS session restoration so it can't
            # resurrect at launch independent of the View toggle.
            ns.setRestorable_(False)
            ns.disableSnapshotRestoration()

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

    @objc.python_method
    def _setStatus(self, text):
        if self._panel is not None and hasattr(self._panel, "summary"):
            try:
                self._panel.summary.set(text)
            except Exception:
                pass

    # --- panel callbacks -------------------------------------------------

    @objc.python_method
    def _syncPairsToFont(self, font):
        """Rebuild the Group-by popup: every pair of the font's axes."""
        if self._panel is None or not hasattr(self._panel, "pairPop"):
            return
        names = [str(a.name) for a in font.axes]
        self._pairs = []
        self._pairItems = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                self._pairs.append((i, j, names[i], names[j]))
                self._pairItems.append("%s + %s" % (names[i], names[j]))
        self._panel.pairPop.setItems(self._pairItems or ["(no axes)"])
        found = self._pairIndices(font)
        for k, (i, j, na, nb) in enumerate(self._pairs):
            if found is not None and set(found) == set((i, j)):
                self._pair = (na, nb)
                self._panel.pairPop.set(k)
                return
        if self._pairs:
            self._pair = (self._pairs[0][2], self._pairs[0][3])
            self._panel.pairPop.set(0)

    @objc.python_method
    def _pairIndices(self, font):
        """Where the selected pair sits among the font's axes, or None.
        An axis answers to its name and to its tag: the default pair is
        XTRA + XOPQ, which are tags — the axes are usually NAMED
        X-Transparency and X-Opacity."""
        names = [str(a.name) for a in font.axes]
        tags = _axis_tags(font)
        found = []
        for wanted in self._pair:
            where = names.index(wanted) if wanted in names else (
                tags.index(wanted) if wanted in tags else None)
            if where is None:
                return None
            found.append(where)
        return tuple(found)

    @objc.python_method
    def _pairChanged(self, sender):
        sel = sender.get()
        # vanilla's PopUpButton.get() is the index in some builds and the
        # title in others — accept both instead of failing silently.
        if isinstance(sel, (int, float)):
            idx = int(sel)
        else:
            try:
                idx = self._pairItems.index(sel)
            except ValueError:
                idx = -1
        if 0 <= idx < len(self._pairs):
            _, _, na, nb = self._pairs[idx]
            self._pair = (na, nb)
        self._scan()

    @objc.python_method
    def _scopeChanged(self, sender):
        self.currentGlyphOnly = bool(sender.get())
        self._scan()

    @objc.python_method
    def _liveChanged(self, sender):
        self.live = bool(sender.get())

    @objc.python_method
    def _refreshClicked(self, sender):
        self._dirty = True
        self._scan()

    @objc.python_method
    def _setNote(self, text):
        if self._panel is not None and hasattr(self._panel, "note"):
            try:
                self._panel.note.set(text)
            except Exception:
                pass

    @objc.python_method
    def _clickedIndex(self, sender):
        """The row that was double-clicked. The table view knows it even
        when the selection is gone (a rescan or a focus change between
        the two clicks); the selection is the fallback."""
        try:
            row = int(sender.getNSTableView().clickedRow())
            if row >= 0:
                return row
        except Exception:
            pass
        sel = sender.getSelection()
        return sel[0] if sel else None

    @objc.python_method
    def _openGlyph(self, sender):
        """Double-click: open the glyph in a new tab at the group's first
        master. Every outcome is written to the panel — a row that does
        nothing when clicked, and says nothing, is the failure to avoid."""
        idx = self._clickedIndex(sender)
        items = sender.get()
        if idx is None or not 0 <= idx < len(items):
            self._setNote("double-click a row to open its glyph")
            return
        row = items[idx]
        name = str(row["glyph"])
        font = self._currentFont()
        if font is None:
            self._setNote("no font to open %s in" % name)
            return
        try:
            tab = font.newTab("/" + name)
        except Exception as e:
            _dbgexc("open glyph: ")
            self._setNote("could not open %s: %s" % (name, e))
            return
        master = int(row.get("_masterIndex", 0))
        try:
            # On the NEW tab: a fresh tab inherits the previous tab's
            # master, so font.masterIndex set beforehand never landed.
            if tab is not None:
                tab.masterIndex = master
            else:
                font.masterIndex = master
            shown = str(font.masters[master].name)
        except Exception as e:
            _dbgexc("open glyph, master: ")
            self._setNote("opened %s, but could not switch master: %s" % (name, e))
            return
        self._setNote("opened %s at %s" % (name, shown))

    # --- scanning ----------------------------------------------------------

    @objc.python_method
    def _maybeScan(self):
        if not self._dirty:
            return
        if time.time() - self._lastScanAt < SCAN_THROTTLE:
            return  # trailing scan is picked up by foreground()
        self._scan()

    @objc.python_method
    def _scan(self):
        self._dirty = False
        self._lastScanAt = time.time()
        if self._panel is None:
            return
        font = self._currentFont()
        if font is None:
            self._setStatus("no font")
            return
        found = self._pairIndices(font)
        if found is None:
            self._setStatus("axes %s not in this font" % (self._pair,))
            return
        idx_a, idx_b = found
        names = [str(a.name) for a in font.axes]
        groups = group_masters(font, idx_a, idx_b)
        if self.currentGlyphOnly and self._currentGlyphName:
            glyph_names = [self._currentGlyphName]
        else:
            glyph_names = [g.name for g in font.glyphs]
        try:
            rows = scan(font, groups, glyph_names, _axis_tags(font), (idx_a, idx_b))
        except Exception:
            _dbgexc("scan: ")
            self._setStatus("scan error — see log")
            return
        self._showRows(rows)
        scope = "current glyph" if self.currentGlyphOnly else "%d glyphs" % len(glyph_names)
        # A glyph has a row for every group it disagrees in.
        flagged = len(set(r["glyph"] for r in rows))
        self._setStatus(
            "%s + %s — %d groups · %s · %d glyphs flagged%s"
            % (names[idx_a], names[idx_b], len(groups), scope, flagged,
               ", in %d rows" % len(rows) if len(rows) != flagged else "")
        )

    @objc.python_method
    def _showRows(self, rows):
        """Replace the list only when the rows changed, and keep the
        selected row selected: live scans run on every interface update,
        and rewriting the list under the user's hands is what navigation
        must not suffer. Compared against what the list HOLDS, not a
        cache, so a rebuilt panel (empty list) is always filled."""
        lst = self._panel.list
        try:
            if list(lst.get()) == rows:
                return
        except Exception:
            pass
        selected = None
        try:
            sel = lst.getSelection()
            if sel:
                prev = lst.get()[sel[0]]
                selected = (prev["glyph"], prev["group"])
        except Exception:
            pass
        try:
            lst.set(rows)
            if selected is not None:
                for i, r in enumerate(rows):
                    if (r["glyph"], r["group"]) == selected:
                        lst.setSelection([i])
                        break
        except Exception:
            _dbgexc("list: ")

    # --- reporter entry point (no Edit-view drawing) -----------------------

    @objc.python_method
    def foreground(self, layer):
        """Glyphs 3 reporter draw entry — panel upkeep and the trailing
        live scan. Nothing is drawn into the Edit view."""
        self._lastLayer = layer
        self._showPanel()  # lazy: also covers a build that skips willActivate
        font = self._currentFont()
        if font is not None and font is not self._panelFont:
            self._panelFont = font
            self._syncPairsToFont(font)
            self._dirty = True
        try:
            glyph = layer.parent
            name = glyph.name if glyph is not None else None
        except Exception:
            name = None
        if name != self._currentGlyphName:
            self._currentGlyphName = name
            if self.currentGlyphOnly:
                self._dirty = True
        self._maybeScan()

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
