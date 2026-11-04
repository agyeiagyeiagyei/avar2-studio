"""E1 — "UFO + designspace (zip)": sources the Google Fonts team can build.

Asked by the designer after the GF team could not rebuild from what the studio
hands over. The zip carries the masters as UFOs (brace layers included), a
designspace 5 with the avar2 mappings, the declared axis defaults, the named
instances and the hidden axes, plus a README.

Acceptance (findings.md, "E1 approved"): the designspace parses with
``DesignSpaceDocument.fromfile``, one mapping per NON-default CSV row, user
axes carry the declared defaults, hidden axes are hidden; a ``fontmake``
round trip builds and matches the studio's TTF within 3 u at every row; the
route and the CLI both produce it; no UI copy names a sidecar.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from avar2_studio import pipeline, sources_export, state
from avar2_studio.build import config_generator

REPO = Path(__file__).resolve().parents[1]
MINI = REPO / "examples" / "crispy-mini" / "sources"
FONTC = Path(sys.executable).with_name("fontc")
FONTMAKE = shutil.which("fontmake")
# The CSV row whose output IS the compiled origin (decision 0001).
DEFAULT_ROW = "Narrow Thin 144"
needs_fontc = pytest.mark.skipif(
    not FONTC.exists(), reason="fontc not next to the test interpreter")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A mini Crispy project: source, mappings CSV, committed metadata."""
    src = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(MINI / "CrispyMini.glyphs", src)
    csv = tmp_path / "CrispyMini-avar.csv"
    shutil.copy2(MINI / "CrispyMini-avar.csv", csv)
    meta_path = tmp_path / "CrispyMini-axis-metadata.json"
    shutil.copy2(MINI / "CrispyMini-axis-metadata.json", meta_path)
    # The transforms sidecar is what renders the SPAC axis into the sources.
    shutil.copy2(MINI / "CrispyMini-transforms.json",
                 tmp_path / "CrispyMini-transforms.json")
    (tmp_path / ".avar2-studio").mkdir()
    monkeypatch.setattr(state, "ORIGINAL_PATH", src)
    monkeypatch.setattr(state, "GLYPHS_PATH", src)
    monkeypatch.setattr(state, "PREVIEW_DIR", tmp_path / ".avar2-studio")
    state._DECLARED_RANGES_CACHE = {}
    return {"source": src, "csv": csv, "metadata": json.loads(meta_path.read_text())}


def _zip(project, tmp_path, **kw):
    out = tmp_path / "out" / "CrispyMini-sources.zip"
    return sources_export.build_sources_zip(
        project["source"], out, csv_path=project["csv"],
        metadata=project["metadata"], family_name="CrispyMini", **kw)


def _designspace(zip_path, tmp_path):
    with zipfile.ZipFile(zip_path) as z:
        name = [n for n in z.namelist() if n.endswith(".designspace")][0]
        (tmp_path / "ds").mkdir(exist_ok=True)
        target = tmp_path / "ds" / name
        target.write_bytes(z.read(name))
    return DesignSpaceDocument.fromfile(str(target))


def test_the_zip_carries_the_masters_the_designspace_and_a_readme(project, tmp_path):
    zip_path = _zip(project, tmp_path, hidden_axes=["opsz"])
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        readme = zf.read("README.txt").decode()

    assert [n for n in names if n.endswith(".designspace")], names[:5]
    ufos = [n for n in names if n.endswith("metainfo.plist")]
    masters = [n for n in ufos if " SPAC" not in n and "-SPAC" not in n]
    assert len(masters) == 8, f"one UFO per master (CrispyMini has 8): {masters}"
    assert len(ufos) == 24, "8 masters + 16 SPAC twins (E2)"
    assert "README.txt" in names
    assert readme.strip(), "the zip explains itself"

    doc = _designspace(zip_path, tmp_path)
    assert doc.formatVersion == "5.0"
    rows = config_generator.read_csv_mappings(project["csv"])
    assert len(doc.axisMappings) == len(rows) - 1, (
        "one mapping per non-default row — the default row maps to itself")
    # The sparse sources travel too: masters + brace layers.
    assert len(doc.sources) > 8, "brace layers must survive as sparse sources"
    assert any(s.layerName for s in doc.sources), "no sparse (brace) source"
    # User axes carry the declared defaults; hidden axes are hidden.
    axes = {a.tag: a for a in doc.axes}
    assert axes["opsz"].default == 144.0, axes["opsz"].default
    assert axes["opsz"].hidden is True, "hidden_axes must mark the axis"
    assert axes["opsz"].minimum == 12.0 and axes["opsz"].maximum == 144.0
    assert len(doc.instances) == len(rows), "one instance per named row"
    assert doc.instances[0].styleName == rows[0].instance_name


def test_the_readme_states_what_the_sources_do_not_carry(project, tmp_path):
    zip_path = _zip(project, tmp_path)
    with zipfile.ZipFile(zip_path) as zf:
        readme = zf.read("README.txt").decode()

    assert sources_export.RESTING_NOTE in readme, (
        "the spacing warning ships verbatim in the README")
    assert "not in the designspace <mappings>" in readme, (
        "the dropped default row must be explained")
    assert "fontmake -m CrispyMini.designspace -o variable" in readme
    assert "sha256:" in readme and "CrispyMini.glyphs" in readme
    # The UI/README rule: name the studio and the source file, never a sidecar.
    for word in ("sidecar", ".json", ".csv", "axis-metadata"):
        assert word not in readme.lower(), word


def test_the_ui_warning_matches_the_constant():
    """The modal shows the same sentence the README carries (pinned here so the
    two cannot drift)."""
    ui = (REPO / "frontend" / "src" / "export-formats.js").read_text()
    # A JS single-quoted literal escapes the apostrophe it contains; compare on
    # the unescaped text so the constant can stay greppable.
    assert sources_export.RESTING_NOTE in ui.replace("\\'", "'")


def test_a_missing_csv_is_a_usage_error(project, tmp_path):
    with pytest.raises(sources_export.SourcesError):
        sources_export.build_sources_zip(project["source"], tmp_path / "x.zip",
                                         csv_path=None, metadata=project["metadata"])


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
def test_fontmake_builds_the_sources_and_matches_the_studio_font(project, tmp_path,
                                                               monkeypatch):
    """The point of the item: what we hand over must build into the same font.

    Three things, because the first version of this test was green over a
    broken export (verifier, E1a): the fontmake build must CARRY the mappings
    (``avar`` version 2 with one entry per non-default row) and log no errors;
    the outlines and advances must match the studio's TTF at every non-default
    row's USER location (both fonts apply avar2 there, so this compares the
    same coordinate system); and the default row is compared separately
    against the unmapped build at ITS default, where it must rest exactly.

    The default row is the one row NOT in the mappings: its user location IS
    the axes' default, and the designspace's default source sits at the
    compiled origin, so a fontmake build must land on that origin to the unit.
    (The studio's TTF, by contrast, relocates its default there at build time —
    decision 0001; the zip's README states the difference.)
    """
    zip_path = _zip(project, tmp_path)
    work = tmp_path / "rt"
    work.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    ds = next(work.glob("*.designspace"))

    res = subprocess.run([FONTMAKE, "-m", str(ds), "-o", "variable",
                          "--output-dir", str(tmp_path / "fm")],
                         capture_output=True, text=True)
    assert res.returncode == 0, (res.stdout or res.stderr)[-1500:]
    built = next((tmp_path / "fm").glob("*.ttf"))

    rows = config_generator.read_csv_mappings(project["csv"])
    default_row = next(r for r in rows if r.instance_name == DEFAULT_ROW)

    # (a) the exported mappings survive the build.
    errors = [ln for ln in (res.stderr or "").splitlines() if "ERROR" in ln]
    assert errors == [], errors[:3]
    avar = TTFont(str(built))["avar"].table
    assert avar.Version == 0x00020000, "fontmake must build avar 2.0"
    # avar2 keeps one variation region per mapping (the VarData items are the
    # per-output-axis deltas, not the mappings).
    regions = avar.VarStore.VarRegionList.RegionCount
    assert regions == len(rows) - 1, (
        f"{regions} mappings, {len(rows) - 1} non-default rows")

    # (b) the studio's own build of the same source, at its normal setting.
    monkeypatch.delenv("AVAR2_RELOCATE_BUILD_DEFAULT", raising=False)
    cfg = pipeline._initialize_preview_config_from_glyphs()
    assert cfg and cfg.exists()
    config_generator.update_config(csv_path=project["csv"], config_path=cfg,
                                   backup=False, source_path=project["source"],
                                   metadata_path=pipeline._get_avar2_metadata_path())
    workdir = tmp_path / "work"
    workdir.mkdir()
    studio = pipeline._build_in_process(cfg, workdir, str(FONTC))
    built_tags = {a.axisTag for a in TTFont(str(built))["fvar"].axes} - {"SPAC"}
    studio_tags = {a.axisTag for a in TTFont(str(studio))["fvar"].axes} - {"SPAC"}
    assert built_tags == studio_tags, (built_tags, studio_tags)

    # (c) every non-default row, at its user location, both fonts mapped.
    worst_outline, worst_advance = 0.0, 0.0
    for row in rows:
        if row.instance_name == DEFAULT_ROW:
            continue
        loc = {t: float(v) for t, v in row.in_axes.items()}
        d, adv = _glyph_set_distance(studio, built, loc, ["a", "g", "o"])
        worst_outline, worst_advance = max(worst_outline, d), max(worst_advance, adv)
    assert worst_outline <= 3.0, f"outlines diverge by {worst_outline:.2f} u"
    assert worst_advance <= 1.0, f"advances diverge by {worst_advance:.2f} u"

    # (d) the default row, against the unmapped build at ITS default.
    plain = tmp_path / "plain.ttf"
    subprocess.run([str(FONTC), "-o", str(plain), str(project["source"]),
                    "--flatten-components", "--decompose-transformed-components"],
                   check=True, capture_output=True)
    # The default row is deliberately NOT in the mappings (gen-avar2 drops it
    # for the built font, and the README says so). Its user location IS the
    # axes' default, and the designspace's default source is the compiled
    # origin — so the exported sources must rest EXACTLY there. Anything but
    # 0.00 u means a mapping leaked into the default location (E1d's defect) or
    # the default source moved.
    user_loc = {t: float(v) for t, v in default_row.in_axes.items()}
    fm_set = TTFont(str(built)).getGlyphSet(location=user_loc)
    plain_set = TTFont(str(plain)).getGlyphSet()
    worst = 0.0
    for name in ("a", "g", "o"):
        pa, pb = _points(fm_set, name), _points(plain_set, name)
        assert len(pa) == len(pb), f"{name}: different point counts"
        for pt, qt in zip(pa, pb):
            worst = max(worst, ((pt[0] - qt[0]) ** 2 + (pt[1] - qt[1]) ** 2) ** 0.5)
    assert worst <= 1e-6, (
        f"the default row must rest EXACTLY on the compiled origin; it is "
        f"{worst:.4f} u away")


def _glyph_set_distance(a, b, loc, glyphs):
    """(worst point distance, worst advance difference) at one USER location.

    ``getGlyphSet(location=…)`` normalises through ``avar``/avar2, so both
    fonts are interpolated in the coordinate system the mappings define — the
    comparison the acceptance asks for.
    """
    fa, fb = TTFont(str(a)), TTFont(str(b))
    ga, gb = fa.getGlyphSet(location=loc), fb.getGlyphSet(location=loc)
    worst, advances = 0.0, 0.0
    for name in glyphs:
        pa, pb = _points(ga, name), _points(gb, name)
        assert len(pa) == len(pb), f"{name}: different point counts"
        for p, q in zip(pa, pb):
            worst = max(worst, ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5)
        advances = max(advances, abs(ga[name].width - gb[name].width))
    return worst, advances


def _points(glyph_set, name):
    from fontTools.pens.recordingPen import RecordingPen
    pen = RecordingPen()
    glyph_set[name].draw(pen)
    return [pt for _op, pts in pen.value for pt in pts]


def _outline_distance(a, b, loc, glyphs):
    """Worst point distance between two fonts instantiated at ``loc``,
    comparing outlines only (advance widths are the spacing difference)."""
    fa, fb = TTFont(str(a)), TTFont(str(b))
    ia = instantiateVariableFont(fa, {"XTRA": loc.get("XTRA", 94.0),
                                      "XOPQ": loc.get("XOPQ", 2.0),
                                      "YOPQ": loc.get("YOPQ", 2.0)},
                                 inplace=True, optimize=False)
    ib = instantiateVariableFont(fb, {"XTRA": loc.get("XTRA", 94.0),
                                      "XOPQ": loc.get("XOPQ", 2.0),
                                      "YOPQ": loc.get("YOPQ", 2.0)},
                                 inplace=True, optimize=False)
    worst = 0.0
    for g in glyphs:
        ca = ia["glyf"][g].getCoordinates(ia["glyf"])[0]
        cb = ib["glyf"][g].getCoordinates(ib["glyf"])[0]
        assert len(ca) == len(cb), f"{g}: different point counts"
        for pa, pb in zip(ca, cb):
            worst = max(worst, ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5)
    return worst


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
@pytest.mark.parametrize("lo,hi", [(-20, 40), (-40, 40)])
def test_the_sources_carry_the_spacing_axis(project, tmp_path, lo, hi):
    """E2: the source-level SPAC renderer is the injector's arithmetic.

    Two builds of the same project: one with the SPAC twins in the sources, one
    with spacing switched off in the sidecar. The second is then spaced by the
    **injector** itself (`WidthAwareSpacTransform.apply`), and the two per-glyph
    ADVANCE CHANGES from ``SPAC = 0`` to each END of the range must agree
    within 1 u at every non-default mapping row — the source renderer and the
    binary one producing the same spacing is the whole claim.

    The symmetric range (−40…40) is E2h: with a twin at the maximum only, the
    sources were flat below the default (0.0 u at SPAC −40 against the
    injector's −88 u), because nothing sat at the negative end.

    Comparing against the studio's built TTF would also drag in the build-time
    default relocation (decision 0001), so the comparison is between the two
    renderers instead, on the same masters.
    """
    transforms = project["source"].parent / "CrispyMini-transforms.json"
    sidecar = json.loads(transforms.read_text())
    sidecar["transforms"][0]["params"].update({"min": lo, "max": hi})
    transforms.write_text(json.dumps(sidecar))

    zip_path = _zip(project, tmp_path)
    work = tmp_path / "rt"
    work.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    ds_path = next(work.glob("*.designspace"))
    doc = DesignSpaceDocument.fromfile(str(ds_path))
    spac = next((a for a in doc.axes if a.tag == "SPAC"), None)
    assert spac is not None, [a.tag for a in doc.axes]
    assert (spac.minimum, spac.default, spac.maximum) == (float(lo), 0.0, float(hi))
    twins = [s for s in doc.sources if s.location.get(spac.name) not in (None, 0)]
    assert len(twins) == 2 * 8, (
        f"{len(twins)} twin sources for 8 masters — one per end, symmetric "
        "range included (E2h)")
    ends = {s.location[spac.name] for s in twins}
    assert ends == {float(lo), float(hi)}, f"twins at both ends: {sorted(ends)}"

    res = subprocess.run([FONTMAKE, "-m", str(ds_path), "-o", "variable",
                          "--output-dir", str(tmp_path / "fm")],
                         capture_output=True, text=True)
    assert res.returncode == 0, (res.stdout or res.stderr)[-1500:]
    errors = [ln for ln in (res.stderr or "").splitlines() if "ERROR" in ln]
    assert errors == [], errors[:3]
    with_spac = next((tmp_path / "fm").glob("*.ttf"))
    assert TTFont(str(with_spac))["avar"].table.Version == 0x00020000, \
        "E1a's assert stays: the mappings must still build"

    # The same project with spacing off, then spaced by the injector.
    quiet = tmp_path / "quiet"
    quiet.mkdir()
    for name in ("CrispyMini.glyphs", "CrispyMini-avar.csv",
                 "CrispyMini-axis-metadata.json"):
        shutil.copy2(project["source"].parent / name, quiet / name)
    quiet_zip = _zip({"source": quiet / "CrispyMini.glyphs",
                      "csv": quiet / "CrispyMini-avar.csv",
                      "metadata": project["metadata"]}, quiet)
    quiet_work = quiet / "rt"
    quiet_work.mkdir()
    with zipfile.ZipFile(quiet_zip) as zf:
        zf.extractall(quiet_work)
    quiet_ds = next(quiet_work.glob("*.designspace"))
    assert not [a for a in DesignSpaceDocument.fromfile(str(quiet_ds)).axes
                if a.tag == "SPAC"], "no SPAC axis without the transform"
    resealed = subprocess.run([FONTMAKE, "-m", str(quiet_ds), "-o", "variable",
                               "--output-dir", str(quiet / "fm")],
                              capture_output=True, text=True)
    assert resealed.returncode == 0, (resealed.stderr or "")[-1500:]
    plain = next((quiet / "fm").glob("*.ttf"))

    from avar2_studio.transforms.base import BuildContext
    from avar2_studio.transforms.builtin_spac_widthaware import (
        WidthAwareSpacTransform,
    )

    target = quiet / "injected.ttf"
    shutil.copy2(plain, target)
    ctx = BuildContext(build_dir=quiet, source_path=project["source"],
                       glyphs_path=project["source"], family="CrispyMini")
    params = {"min": lo, "max": hi, "bias": 1.0, "scale": 1.25}
    # apply() returns the font it wrote — it may not be the file passed in.
    injected = WidthAwareSpacTransform().apply(target, params, ctx)

    rows = config_generator.read_csv_mappings(project["csv"])
    glyphs = ["a", "o", "g", "w", "i"]
    worst = 0.0
    for row in rows:
        if row.instance_name == DEFAULT_ROW:
            continue
        user = {t: float(v) for t, v in row.in_axes.items()}
        for name in glyphs:
            base = _one_metric(with_spac, name, "advance", {**user, "SPAC": 0.0})
            ref_base = _one_metric(injected, name, "advance", {**user, "SPAC": 0.0})
            for end in (lo, hi):
                moved = _one_metric(with_spac, name, "advance",
                                    {**user, "SPAC": float(end)})
                ref = _one_metric(injected, name, "advance",
                                  {**user, "SPAC": float(end)})
                worst = max(worst, abs((moved - base) - (ref - ref_base)))
    assert worst <= 1.0, f"the SPAC shape differs by {worst:.2f} u"


def _one_metric(font_path, name, key, location):
    """One metric of one glyph at a location ('advance' or 'lsb')."""
    from fontTools.pens.boundsPen import BoundsPen

    font = TTFont(str(font_path))
    glyph_set = font.getGlyphSet(location=location)
    if key == "advance":
        return glyph_set[name].width
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.bounds[0] if pen.bounds else 0


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
def test_kinetikern_corrections_ride_the_sources(project, tmp_path):
    """E2 acceptance 3: the corrections are in the build, and off means off.

    A spaced style's brace carries the interpolated outline with the injector's
    arithmetic: LSB by ``left``, advance by ``left + right``. The check is a
    difference between two fontmake builds at that style — one with the
    Kinetikern entry, one without — so the interpolated outline cancels out and
    only the correction is being measured.
    """
    style = "Narrow Heavy 144"
    deltas = {"a": [-12, 7], "o": [5, -9]}
    spaced = {"version": 1, "enabled": True,
              "instances": [{"name": style, "looseness": -0.6, "deltas": deltas}]}

    def build(kinetikern, tag):
        out = tmp_path / tag / "CrispyMini-sources.zip"
        out.parent.mkdir(parents=True, exist_ok=True)
        zip_path = sources_export.build_sources_zip(
            project["source"], out, csv_path=project["csv"],
            metadata=project["metadata"], family_name="CrispyMini",
            kinetikern=kinetikern)
        work = tmp_path / tag / "rt"
        work.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work)
        ds = next(work.glob("*.designspace"))
        res = subprocess.run([FONTMAKE, "-m", str(ds), "-o", "variable",
                              "--output-dir", str(tmp_path / tag / "fm")],
                             capture_output=True, text=True)
        assert res.returncode == 0, (res.stdout or res.stderr)[-1500:]
        errors = [ln for ln in (res.stderr or "").splitlines() if "ERROR" in ln]
        assert errors == [], errors[:3]
        doc = DesignSpaceDocument.fromfile(str(ds))
        return next((tmp_path / tag / "fm").glob("*.ttf")), doc

    plain, plain_doc = build(None, "off")
    corrected, corrected_doc = build(spaced, "on")

    extra = len(corrected_doc.sources) - len(plain_doc.sources)
    assert extra >= 1, "a Kinetikern style adds at least one sparse source"

    row = next(r for r in config_generator.read_csv_mappings(project["csv"])
               if r.instance_name == style)
    user = {t: float(v) for t, v in row.in_axes.items()}
    for glyph, (left, right) in deltas.items():
        base = _one_metric(plain, glyph, "advance", {**user, "SPAC": 0.0})
        moved = _one_metric(corrected, glyph, "advance", {**user, "SPAC": 0.0})
        assert abs((moved - base) - (left + right)) <= 1.0, (
            f"{glyph}: the sources carry {moved - base:.1f} u, the correction "
            f"is {left + right} u")
        base_lsb = _one_metric(plain, glyph, "lsb", {**user, "SPAC": 0.0})
        moved_lsb = _one_metric(corrected, glyph, "lsb", {**user, "SPAC": 0.0})
        assert abs((moved_lsb - base_lsb) - left) <= 1.0, (
            f"{glyph}: LSB moved {moved_lsb - base_lsb:.1f} u, expected {left}")

    # The family switch off writes no braces at all.
    quiet = {"version": 1, "enabled": False, "instances": spaced["instances"]}
    _, quiet_doc = build(quiet, "disabled")
    assert len(quiet_doc.sources) == len(plain_doc.sources), \
        "with Kinetikern off, no braces are written"


def test_the_sidecars_are_read_from_the_original_source(project, tmp_path):
    """E2e: the transforms sidecar resolves against the ORIGINAL, not the staging.

    The worker stages the active source as `.avar2-studio/active-<name>.glyphs`,
    so a sidecar lookup from that path finds nothing — which is how the real
    Crispy export lost its SPAC axis entirely (verifier, ROUND 22). The sidecar
    is beside the designer's file, and `original_path` is what says so.
    """
    staged_dir = project["source"].parent / ".avar2-studio"
    staged_dir.mkdir(exist_ok=True)
    active = staged_dir / f"active-{project['source'].name}"
    shutil.copy2(project["source"], active)

    def axes(**kwargs):
        out = tmp_path / f"_{len(list(tmp_path.glob('*.zip')))}.zip"
        sources_export.build_sources_zip(
            active, out, csv_path=project["csv"], metadata=project["metadata"],
            family_name="CrispyMini", **kwargs)
        work = out.with_suffix("")
        work.mkdir()
        with zipfile.ZipFile(out) as zf:
            zf.extractall(work)
        doc = DesignSpaceDocument.fromfile(str(next(work.glob("*.designspace"))))
        return {a.tag for a in doc.axes}

    assert "SPAC" in axes(original_path=project["source"]), (
        "the sidecar beside the original must reach the export")
    assert "SPAC" not in axes(), (
        "without original_path the staged copy has no sidecar — the defect this "
        "parameter exists to avoid")


def test_the_studio_placeholders_never_become_axes(project, tmp_path):
    """E1b: the metadata's build-time placeholders are not designspace axes.

    Crispy's metadata carries `grad`, `lcwd`, `ymod` and `spac` entries with no
    default, because the studio composes those axes at build time. Shipped as
    axes they produced duplicate names, a default of −1000 on `lcwd`/`ymod` and
    no findable default source — fontmake exited 1 (verifier, ROUND 22).
    """
    metadata = dict(project["metadata"])
    metadata.update({
        "GRAD": {"registered_tag": "grad", "display_name": "Grade",
                 "min": -10.0, "max": 10.0, "is_parametric": False},
        "LCWD": {"registered_tag": "lcwd", "display_name": "Lowercase width",
                 "min": -1000, "max": 1000, "is_parametric": False},
        "YMOD": {"registered_tag": "ymod", "display_name": "Horizontal correction",
                 "min": -1000, "max": 1000, "is_parametric": False},
        "SPAC": {"registered_tag": "spac", "display_name": "Spacing",
                 "min": -20.0, "max": 40.0, "is_parametric": False},
        # a real axis without a declared default is a placeholder too
        "XTRA2": {"registered_tag": "xtra2", "display_name": "Half width",
                  "min": 0.0, "max": 10.0, "is_parametric": False},
    })
    out = tmp_path / "placeholders.zip"
    sources_export.build_sources_zip(
        project["source"], out, csv_path=project["csv"], metadata=metadata,
        family_name="CrispyMini", original_path=project["source"])
    work = tmp_path / "rt-placeholders"
    work.mkdir()
    with zipfile.ZipFile(out) as zf:
        zf.extractall(work)
    doc = DesignSpaceDocument.fromfile(str(next(work.glob("*.designspace"))))
    tags = [a.tag for a in doc.axes]
    assert not [t for t in tags if t.lower() in
                {"grad", "lcwd", "ymod", "xtra2"}], tags
    assert tags.count("SPAC") == 1, f"one SPAC axis, from the twins: {tags}"
    assert len({t.lower() for t in tags}) == len(tags), f"unique tags: {tags}"
    assert len({a.name for a in doc.axes}) == len(doc.axes), \
        [a.name for a in doc.axes]
    assert doc.findDefault() is not None, "a default source must exist"


def test_an_unfinished_row_never_writes_an_empty_mapping_input(project, tmp_path):
    """E1d: a row with no traditional inputs takes the build's identity fill.

    Crispy's CSV line 27 (``Wide 400 MinOpsz,1420.1,149.9,122.1,,,,14.1``) has
    parametric outputs and no WGHT/WDTH/OPSZ. The export wrote ``<input />``,
    which fontmake reads as THE default location, so every other mapping
    landed offset by that row's output — 4,913 u on the real Crispy
    (verifier, ROUND 23).
    """
    from xml.etree import ElementTree as ET

    csv_path = project["csv"]
    text = csv_path.read_text(encoding="utf-8-sig").rstrip("\n")
    csv_path.write_text(
        text + "\nUnfinished Row,2000.0,50.0,40.0,,,\n", encoding="utf-8")

    out = tmp_path / "e1d.zip"
    sources_export.build_sources_zip(
        project["source"], out, csv_path=csv_path,
        metadata=project["metadata"], family_name="CrispyMini",
        original_path=project["source"])
    work = tmp_path / "e1d"
    work.mkdir()
    with zipfile.ZipFile(out) as zf:
        zf.extractall(work)
    tree = ET.parse(str(next(work.glob("*.designspace"))))
    inputs = tree.findall(".//mappings/mapping/input")
    assert inputs, "the designspace carries mappings"
    assert not [i for i in inputs if len(i) == 0], (
        "no mapping may carry an empty <input/> — fontmake reads it as the "
        "default location and offsets every other mapping (E1d)")
    doc = DesignSpaceDocument.fromfile(str(next(work.glob("*.designspace"))))
    name_of = {a.tag: a.name for a in doc.axes}
    row_inputs = [{d.get("name"): float(d.get("xvalue")) for d in i}
                  for i in inputs]
    assert {name_of["XTRA"]: 2000.0, name_of["XOPQ"]: 50.0,
            name_of["YOPQ"]: 40.0} in row_inputs, (
        "the unfinished row's input is its own parametric location, the same "
        f"identity fill the studio build uses: {row_inputs}")


def test_the_validator_rejects_an_empty_or_default_mapping_input(project, tmp_path):
    """E1d: the two shapes fontmake misreads as "the default location"."""
    doc = _designspace(_zip(project, tmp_path), tmp_path)
    xtra = next(a for a in doc.axes if a.tag == "XTRA")
    make = config_generator.RowMapping

    empty = make(instance_name="No Inputs", in_axes={},
                 out_axes={"XTRA": 100.0}, out_axis_order=["XTRA"])
    with pytest.raises(sources_export.SourcesError, match="No Inputs"):
        sources_export._validate_designspace(doc, rows=[empty])

    at_default = make(instance_name="At Default",
                      in_axes={"XTRA": xtra.default},
                      out_axes={"XTRA": 100.0}, out_axis_order=["XTRA"])
    with pytest.raises(sources_export.SourcesError, match="At Default"):
        sources_export._validate_designspace(doc, rows=[at_default])

    # A row at the default location is the DEFAULT row: skipped, not rejected.
    sources_export._validate_designspace(doc, rows=[at_default],
                                         default_row_name="At Default")


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
def test_the_spacing_basis_is_the_built_font(project, tmp_path):
    """E2i: the amounts normalise against the built font, not the default source.

    The injector measures the font it is handed — the studio's build at ITS
    default (on Crispy the relocated default, mean ink ~1,750 u), not the
    designspace's default source (the 47-1-1 hairline master, mean 96.3 u).
    Measuring the source made every amount ~18× too strong (`i` +6 u where the
    injector gives +58). The renderer now takes the same measurement from the
    built font; this test passes a font whose ink differs from the source and
    checks the twin's amount follows IT.
    """
    from avar2_studio.transforms.builtin_spac_widthaware import _ink_width

    # A built font of the same project (no SPAC), then one glyph fattened so
    # its share of the mean ink is nothing like the source's.
    quiet = tmp_path / "quiet-basis"
    quiet.mkdir()
    for name in ("CrispyMini.glyphs", "CrispyMini-avar.csv",
                 "CrispyMini-axis-metadata.json"):
        shutil.copy2(project["source"].parent / name, quiet / name)
    quiet_zip = _zip({"source": quiet / "CrispyMini.glyphs",
                      "csv": quiet / "CrispyMini-avar.csv",
                      "metadata": project["metadata"]}, quiet)
    quiet_work = quiet / "rt"
    quiet_work.mkdir()
    with zipfile.ZipFile(quiet_zip) as zf:
        zf.extractall(quiet_work)
    res = subprocess.run([FONTMAKE, "-m", str(next(quiet_work.glob("*.designspace"))),
                          "-o", "variable", "--output-dir", str(quiet / "fm")],
                         capture_output=True, text=True)
    assert res.returncode == 0, (res.stderr or "")[-1200:]
    built = TTFont(str(next((quiet / "fm").glob("*.ttf"))))
    fat = built["glyf"]["i"]
    fat.coordinates.scale((8, 1)) if hasattr(fat.coordinates, "scale") else None
    if not hasattr(fat.coordinates, "scale"):  # simple glyph: x *= 8
        xs = fat.coordinates.x
        fat.coordinates.x = [int(round(x * 8)) for x in xs]
    basis_path = quiet / "basis.ttf"
    built.save(str(basis_path))

    basis = TTFont(str(basis_path))
    glyph_set = basis.getGlyphSet()
    inks = {n: _ink_width(glyph_set, n) for n in basis.getGlyphOrder()}
    inks = {n: w for n, w in inks.items() if w > 0}
    mean_ink = sum(inks.values()) / len(inks)
    expected_dx = round(40 * (inks["i"] / mean_ink) * 1.25)

    zip_path = _zip(project, tmp_path, ink_font=basis_path)
    work = tmp_path / "rt-basis"
    work.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    doc = DesignSpaceDocument.fromfile(str(next(work.glob("*.designspace"))))
    spac = next(a for a in doc.axes if a.tag == "SPAC")
    master = next(s for s in doc.sources
                  if not s.layerName and s.location.get(spac.name) is None)
    twin = next(s for s in doc.sources
                if s.styleName == f"{master.styleName} SPAC40")
    import ufoLib2

    master_width = ufoLib2.Font.open(str(work / master.filename))["i"].width
    twin_width = ufoLib2.Font.open(str(work / twin.filename))["i"].width
    assert twin_width - master_width == 2 * expected_dx, (
        f"the twin must use the built font's ink: {twin_width - master_width} "
        f"against the expected {2 * expected_dx}")

    # The source basis would give a different amount — the test can fail.
    source_basis = sources_export._ink_widths(
        ufoLib2.Font.open(str(work / master.filename)))
    mean_source = sum(source_basis.values()) / len(source_basis)
    source_dx = round(40 * (source_basis["i"] / mean_source) * 1.25)
    assert abs(source_dx - expected_dx) > 5, (
        "the fixture must make the two bases disagree, or it proves nothing")


def test_a_twin_never_carries_a_negative_advance():
    """E2h/E2i: the tightening is capped where a master cannot absorb it.

    Crispy's hairline master has `n` at 166 u while the injector's amount at
    SPAC −100 is 258 u of advance change — a negative width, which fontTools
    refuses to compile (`The width should not be negative: 'A'`). The cap keeps
    at least 1 u; everywhere the design can carry the amount, the twin is exact.
    """
    from avar2_studio.sources_export import _fit_dx

    assert _fit_dx(-129, 166, False) == -82, "simple: 166 + 2·(−82) = 2 u"
    assert _fit_dx(-156, 175, False) == -87
    assert _fit_dx(-129, 4000, False) == -129, "roomy masters keep the amount"
    assert _fit_dx(-300, 100, True) == -99, "composite: advance + dx ≥ 1"
    assert _fit_dx(129, 166, False) == 129, "loosening is never capped"
    for width in (0, 1, 2, 3, 66, 166):
        assert width + 2 * _fit_dx(-500, width, False) >= 0, width


def _rect(font, name, x0, width, height=700):
    glyph = font.newGlyph(name)
    pen = glyph.getPen()
    pen.moveTo((x0, 0))
    pen.lineTo((x0 + width, 0))
    pen.lineTo((x0 + width, height))
    pen.lineTo((x0, height))
    pen.closePath()
    glyph.width = width + 2 * x0
    return glyph


def test_a_composite_tracks_like_the_injector_and_flipped_ones_decompose():
    """E2j: composites open ONE side like the injector, and are never shifted twice.

    The injector moves a composite's LEFT phantom only (advance and left side
    by `dx`, right side unchanged), and it moves the base glyph too — so the
    composite inherits the base's move. Shifting the composite AND leaving the
    inherited move in place opened Cacute +246 and closed it −123 on the real
    Crispy. Transformed components (`w` = flipped `m`, `W` = flipped `M`) are
    decomposed by the studio's build, so a twin has to decompose them too.
    """
    import ufoLib2
    from ufoLib2.objects import Component

    from avar2_studio.sources_export import _shift_spacing

    font = ufoLib2.Font()
    font.info.unitsPerEm = 1000
    _rect(font, "m", 100, 600)
    _rect(font, "n", 100, 500)
    _rect(font, "acute", 200, 100)
    plain = font.newGlyph("M")                      # plain composite of m
    plain.components.append(Component("m", (1, 0, 0, 1, 0, 0)))
    plain.width = 800
    accented = font.newGlyph("nacute")              # two translated components
    accented.components.append(Component("n", (1, 0, 0, 1, 0, 0)))
    accented.components.append(Component("acute", (1, 0, 0, 1, 0, 0)))
    accented.width = 700
    flipped = font.newGlyph("w")                    # transformed → decomposed
    flipped.components.append(Component("m", (-1, 0, 0, 1, 700, 0)))
    flipped.width = 800

    twin = copy.deepcopy(font)                      # what the writer does
    inks = {name: 600.0 for name in ("m", "n", "acute", "M", "nacute", "w")}
    _shift_spacing(twin, font, inks, 600.0, kind="spac", value=100.0,
                   bias=1.0, scale=1.0)

    m, M = twin["m"], twin["M"]
    assert m.width == 800 + 200, "simple: advance + 2·dx"
    assert M.width == 800 + 100, "composite: advance + dx (the injector's rule)"
    ox = M.components[0].transformation[4]
    assert ox == 0, (
        "the composite must not add a second shift on top of the move its base "
        f"already carries (component offset {ox})")
    nacute = twin["nacute"]
    assert nacute.width == 700 + 100
    assert [c.transformation[4] for c in nacute.components] == [0, 0]
    w = twin["w"]
    assert not w.components, "a transformed component is decomposed"
    assert w.width == 800 + 200, "…and then tracks like a simple glyph"
    assert w.contours, "the decomposed outlines are there"


def test_the_readme_names_and_hashes_the_original_source(project, tmp_path):
    """E1c: the README describes the DESIGNER's file, not the staged shadow.

    The worker reads the masters from `.avar2-studio/active-<name>.glyphs`;
    naming and hashing that file told the Google Fonts team nothing (the staged
    copy is deleted, and its bytes are not what the designer has). The composed
    list is the writer's too: the enabled transforms the build itself reads.
    """
    import hashlib

    staged_dir = project["source"].parent / ".avar2-studio"
    staged_dir.mkdir(exist_ok=True)
    active = staged_dir / f"active-{project['source'].name}"
    shutil.copy2(project["source"], active)
    with active.open("a", encoding="utf-8") as fh:   # different bytes on purpose
        fh.write("\n")

    sidecar = project["source"].parent / "CrispyMini-transforms.json"
    sidecar.write_text(json.dumps({"version": 1, "transforms": [
        {"type": "spac_widthaware", "enabled": True,
         "params": {"min": -20, "max": 40, "bias": 1.0, "scale": 1.25}},
        {"type": "slant", "enabled": False, "params": {}},
    ]}))

    out = tmp_path / "orig.zip"
    sources_export.build_sources_zip(
        active, out, csv_path=project["csv"], metadata=project["metadata"],
        family_name="CrispyMini", original_path=project["source"])
    with zipfile.ZipFile(out) as zf:
        readme = zf.read("README.txt").decode()

    assert f"Source file: {project['source'].name}" in readme, readme
    assert f"sha256: {hashlib.sha256(project['source'].read_bytes()).hexdigest()}" in readme, (
        "the hash is the designer's file, not the staged copy")
    assert "spac_widthaware" in readme, "the enabled transform is named"
    assert "slant" not in readme, "a disabled transform is not composed"


def test_the_cli_zip_and_the_dialog_zip_have_the_same_content(project, tmp_path):
    """E1c: both paths produce the same zip, named after the family.

    The dialog goes through the build worker (`pipeline._sources_pipeline`),
    the CLI through `cli._write_sources`. Nothing user-visible may depend on
    which one wrote it.
    """
    from avar2_studio import cli, server

    dialog_dir = tmp_path / "dialog"
    dialog_zip = pipeline._sources_pipeline(
        project["source"], dialog_dir, hidden_axes=["opsz"],
        family_name="CrispyMini")

    cli_dir = tmp_path / "cli"
    cli_dir.mkdir()

    class Args:  # the two fields _write_sources reads
        out = cli_dir
        source = project["source"]

    monkeypatch_original = state.ORIGINAL_PATH
    try:
        cli_zip = cli._write_sources(Args(), state, server, ["opsz"])
    finally:
        state.ORIGINAL_PATH = monkeypatch_original
    assert cli_zip is not None

    assert dialog_zip.name == cli_zip.name == "CrispyMini-sources.zip", (
        f"{dialog_zip.name} vs {cli_zip.name}")

    def contents(path):
        with zipfile.ZipFile(path) as zf:
            names = sorted(zf.namelist())
            ds = [n for n in names if n.endswith(".designspace")][0]
            return names, zf.read("README.txt"), zf.read(ds)

    dialog = contents(dialog_zip)
    cli_side = contents(cli_zip)
    assert dialog[0] == cli_side[0], "same files in the zip"
    assert dialog[1] == cli_side[1], "same README"
    assert dialog[2] == cli_side[2], "same designspace"


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
def test_a_glyph_with_a_production_name_still_gets_its_spacing(project, tmp_path):
    """E2i-b: the built font says `uni0061`, the sources say `a`.

    The ink basis comes from the built font, whose glyph order uses PRODUCTION
    names; the twins are UFOs and use SOURCE names. The lookup used to skip
    every renamed glyph silently — on the real Crispy that was `Euro` and ~43
    composites, unspaced at both ends (verifier, ROUND 24).
    """
    import glyphsLib
    import ufoLib2

    from avar2_studio.transforms.builtin_spac_widthaware import _ink_width

    source = glyphsLib.load(str(project["source"]))
    source.glyphs["a"].production = "uni0061"
    renamed = tmp_path / "Renamed.glyphs"
    source.save(str(renamed))
    shutil.copy2(project["source"].parent / "CrispyMini-avar.csv",
                 tmp_path / "Renamed-avar.csv")
    shutil.copy2(project["source"].parent / "CrispyMini-axis-metadata.json",
                 tmp_path / "Renamed-axis-metadata.json")
    shutil.copy2(project["source"].parent / "CrispyMini-transforms.json",
                 tmp_path / "Renamed-transforms.json")

    quiet = sources_export.build_sources_zip(
        renamed, tmp_path / "quiet.zip", csv_path=tmp_path / "Renamed-avar.csv",
        metadata=project["metadata"], family_name="Renamed")
    work = tmp_path / "quiet"
    work.mkdir()
    with zipfile.ZipFile(quiet) as zf:
        zf.extractall(work)
    res = subprocess.run([FONTMAKE, "-m", str(next(work.glob("*.designspace"))),
                          "-o", "variable", "--output-dir", str(tmp_path / "fm")],
                         capture_output=True, text=True)
    assert res.returncode == 0, (res.stderr or "")[-1200:]
    built = next((tmp_path / "fm").glob("*.ttf"))
    assert "uni0061" in TTFont(str(built)).getGlyphOrder(), \
        "the compiler applies the production name"

    zip_path = sources_export.build_sources_zip(
        renamed, tmp_path / "spaced.zip", csv_path=tmp_path / "Renamed-avar.csv",
        metadata=project["metadata"], family_name="Renamed", ink_font=built)
    work = tmp_path / "spaced"
    work.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    doc = DesignSpaceDocument.fromfile(str(next(work.glob("*.designspace"))))
    spac = next(a for a in doc.axes if a.tag == "SPAC")
    master = next(s for s in doc.sources
                  if not s.layerName and s.location.get(spac.name) is None)
    twin = next(s for s in doc.sources
                if s.styleName == f"{master.styleName} SPAC40")
    master_width = ufoLib2.Font.open(str(work / master.filename))["a"].width
    twin_width = ufoLib2.Font.open(str(work / twin.filename))["a"].width
    assert twin_width != master_width, (
        "the source-named glyph must take the built font's amount, not be "
        "skipped because the built font calls it uni0061")

    basis = TTFont(str(built))
    ink = _ink_width(basis.getGlyphSet(), "uni0061")
    inks = sources_export._ink_widths_from_build(built)
    mean = sum(inks.values()) / len(inks)
    assert twin_width - master_width == 2 * round(40 * (ink / mean) * 1.25)


def test_a_mixed_glyph_flattens_and_tracks_like_a_simple_one():
    """E2j-b: `Q` is `O` plus a tail contour; a compiler decomposes it.

    A TrueType composite cannot carry its own contours, so the studio's build
    flattens mixed glyphs and they track on BOTH sides. The twin used to keep
    the component and apply the composite rule.
    """
    import copy as _copy

    import ufoLib2
    from ufoLib2.objects import Component

    from avar2_studio.sources_export import _shift_spacing

    font = ufoLib2.Font()
    font.info.unitsPerEm = 1000
    o = font.newGlyph("O")
    pen = o.getPen()
    pen.moveTo((100, 0))
    pen.lineTo((500, 0))
    pen.lineTo((500, 700))
    pen.lineTo((100, 700))
    pen.closePath()
    o.width = 700
    q = font.newGlyph("Q")
    q.components.append(Component(baseGlyph="O", transformation=(1, 0, 0, 1, 0, 0)))
    tail = q.getPen()
    tail.moveTo((450, 0))
    tail.lineTo((620, -80))
    tail.lineTo((450, -120))
    tail.closePath()
    q.width = 700

    twin = _copy.deepcopy(font)
    inks = {"O": 400.0, "Q": 400.0}
    _shift_spacing(twin, font, inks, 400.0, kind="spac", value=100,
                   bias=1.0, scale=1.0)

    assert not twin["Q"].components, "mixed glyphs are flattened like the build does"
    assert twin["Q"].width == 900, "advance +2dx, as a simple glyph"
    assert twin["O"].width == 900
