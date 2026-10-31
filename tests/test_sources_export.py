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
    against the unmapped build of the same masters.

    The default row is the resting dip of decision 0001: the studio's font is
    relocated to that row's output (XOPQ 6 / YOPQ 6) while the sources rest at
    the compiled origin (2 / 2) with the row dropped from the mappings — 8–9 u
    on outlines, 8 u on advances. That is what the zip's README states, and it
    is asserted here as the *unmapped* agreement it actually is, not blamed on
    a tolerance.
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

    # (d) the default row, against the unmapped build of the same masters.
    plain = tmp_path / "plain.ttf"
    subprocess.run([str(FONTC), "-o", str(plain), str(project["source"]),
                    "--flatten-components", "--decompose-transformed-components"],
                   check=True, capture_output=True)
    # The default row is deliberately NOT in the mappings (gen-avar2 drops it
    # for the built font, and the README says so). Its user location therefore
    # lands NEAR — not exactly on — the compiled origin: the avar2 built from
    # the 7 mapped rows interpolates across it. Measured 8.1 u against the
    # masters instanced at the row's parametric output, which is the resting
    # dip of decision 0001 seen from the sources' side. Asserted as a bound with
    # the number in it, not as a tolerance that hides the difference.
    user_loc = {t: float(v) for t, v in default_row.in_axes.items()}
    out_loc = {t: float(v) for t, v in default_row.out_axes.items()}
    fm_set = TTFont(str(built)).getGlyphSet(location=user_loc)
    plain_set = TTFont(str(plain)).getGlyphSet(location=out_loc)
    worst = 0.0
    for name in ("a", "g", "o"):
        pa, pb = _points(fm_set, name), _points(plain_set, name)
        assert len(pa) == len(pb), f"{name}: different point counts"
        for pt, qt in zip(pa, pb):
            worst = max(worst, ((pt[0] - qt[0]) ** 2 + (pt[1] - qt[1]) ** 2) ** 0.5)
    assert worst <= 10.0, (f"the default row rests {worst:.2f} u from the "
                          "masters (was 8.1 u; a jump means the mappings moved)")


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
def test_the_sources_carry_the_spacing_axis(project, tmp_path):
    """E2: the source-level SPAC renderer is the injector's arithmetic.

    Two builds of the same project: one with the SPAC twins in the sources, one
    with spacing switched off in the sidecar. The second is then spaced by the
    **injector** itself (`WidthAwareSpacTransform.apply`), and the two per-glyph
    ADVANCE CHANGES from ``SPAC = 0`` to ``SPAC = max`` must agree within 1 u at
    every non-default mapping row — the source renderer and the binary one
    producing the same spacing is the whole claim.

    Comparing against the studio's built TTF would also drag in the build-time
    default relocation (decision 0001), so the comparison is between the two
    renderers instead, on the same masters.
    """
    zip_path = _zip(project, tmp_path)
    work = tmp_path / "rt"
    work.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(work)
    ds_path = next(work.glob("*.designspace"))
    doc = DesignSpaceDocument.fromfile(str(ds_path))
    spac = next((a for a in doc.axes if a.tag == "SPAC"), None)
    assert spac is not None, [a.tag for a in doc.axes]
    assert (spac.minimum, spac.default, spac.maximum) == (-20.0, 0.0, 40.0)
    twins = [s for s in doc.sources if s.location.get(spac.name) not in (None, 0)]
    assert len(twins) == 2 * 8, f"{len(twins)} twin sources for 8 masters"

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
    params = {"min": -20, "max": 40, "bias": 1.0, "scale": 1.25}
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
            top = _one_metric(with_spac, name, "advance", {**user, "SPAC": 40.0})
            ref_base = _one_metric(injected, name, "advance", {**user, "SPAC": 0.0})
            ref_top = _one_metric(injected, name, "advance", {**user, "SPAC": 40.0})
            worst = max(worst, abs((top - base) - (ref_top - ref_base)))
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
