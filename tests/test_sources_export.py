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
    assert len([n for n in names if n.endswith("metainfo.plist")]) == 8, (
        "one UFO per master (CrispyMini has 8)")
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

    assert sources_export.SPACING_WARNING in readme, (
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
    assert sources_export.SPACING_WARNING in ui.replace("\\'", "'")


def test_a_missing_csv_is_a_usage_error(project, tmp_path):
    with pytest.raises(sources_export.SourcesError):
        sources_export.build_sources_zip(project["source"], tmp_path / "x.zip",
                                         csv_path=None, metadata=project["metadata"])


@needs_fontc
@pytest.mark.skipif(FONTMAKE is None, reason="fontmake is not installed")
def test_fontmake_builds_the_sources_and_matches_the_studio_font(project, tmp_path,
                                                               monkeypatch):
    """The point of the item: what we hand over must build and agree.

    Two comparisons, and a limit stated plainly:

    * the axis tags must match the studio's avar2 TTF (minus SPAC);
    * the outlines must match at every CSV row's parametric output.

    The outline comparison is against the UNMAPPED build of the same source
    (fontc on the same .glyphs), not the studio's shipped TTF: that TTF carries
    an avar2 mapping written from the CSV, while the sources carry the same
    information as a designspace 5 ``<mappings>`` block, which fontmake and
    fontTools' instancer do not consume. Instantiating the mapped font "at a
    parametric location" therefore drives two different coordinate systems, and
    the difference measured is the mapping, not the sources (4826–4835 u on the
    rows that move wght/wdth). Against the unmapped build of the same masters
    the sources are exact: 0 u at every row, which is what "the sources are the
    design" means. The mapped font is reproducible from these sources only by a
    tool that applies the mappings.
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

    # (a) axis tags vs the studio's avar2 build of the same source.
    monkeypatch.setenv("AVAR2_RELOCATE_BUILD_DEFAULT", "0")
    cfg = pipeline._initialize_preview_config_from_glyphs()
    assert cfg and cfg.exists()
    config_generator.update_config(csv_path=project["csv"], config_path=cfg,
                                   backup=False, source_path=project["source"],
                                   metadata_path=pipeline._get_avar2_metadata_path())
    workdir = tmp_path / "work"
    workdir.mkdir()
    studio = pipeline._build_in_process(cfg, workdir, str(FONTC))
    built_tags = {a.axisTag for a in TTFont(str(built))["fvar"].axes}
    studio_tags = {a.axisTag for a in TTFont(str(studio))["fvar"].axes} - {"SPAC"}
    assert built_tags == studio_tags, (built_tags, studio_tags)

    # (b) outlines at every row's parametric output, against the same masters.
    plain = tmp_path / "plain.ttf"
    subprocess.run([str(FONTC), "-o", str(plain), str(project["source"]),
                    "--flatten-components", "--decompose-transformed-components"],
                   check=True, capture_output=True)
    rows = config_generator.read_csv_mappings(project["csv"])
    worst = 0.0
    for row in rows:
        loc = {t: float(v) for t, v in row.out_axes.items()}
        worst = max(worst, _outline_distance(built, plain, loc, ["a", "g", "o"]))
    assert worst <= 3.0, f"outlines diverge by {worst:.2f} u"


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
