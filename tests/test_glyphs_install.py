"""``avar2-studio install-glyphs-plugins`` — the bundled Glyphs plugins
are symlinked into Glyphs' Plugins folder and the interpreter recorded
for the hub plugin's launcher; ``--uninstall`` takes only our links away.
"""

from __future__ import annotations

import json
import sys

from avar2_studio import glyphs_install

# The bundles the package must at least carry; a new tool joining the
# suite is picked up by bundled_plugins() without a change here.
EXPECTED = {
    "Avar2Studio.glyphsPlugin",
    "CornerRadii.glyphsReporter",
    "InstanceDelta.glyphsReporter",
    "MultiSourceEdit.glyphsTool",
    "ParametricMasters.glyphsReporter",
    "SlantMaster.glyphsReporter",
    "WidthMatcher.glyphsReporter",
}


def test_package_ships_the_seven_bundles_with_their_loaders():
    bundles = {b.name: b for b in glyphs_install.bundled_plugins()}
    assert EXPECTED <= set(bundles)
    for bundle in bundles.values():
        assert (bundle / "Contents" / "Info.plist").is_file()
        assert (bundle / "Contents" / "MacOS" / "plugin").is_file()
        assert (bundle / "Contents" / "Resources" / "plugin.py").is_file()


def test_install_links_every_bundle_and_records_the_interpreter(tmp_path):
    plugins_dir = tmp_path / "Plugins"
    config_path = tmp_path / "home" / "glyphs-plugin.json"
    lines = []

    status = glyphs_install.install(plugins_dir, config_path, out=lines.append)

    assert status == 0
    links = {p.name: p for p in plugins_dir.iterdir()}
    assert set(links) == {b.name for b in glyphs_install.bundled_plugins()}
    assert EXPECTED <= set(links)
    for name, link in links.items():
        assert link.is_symlink()
        assert link.resolve() == (glyphs_install.SOURCE_DIR / name).resolve()
    config = json.loads(config_path.read_text())
    assert config["python"] == sys.executable
    assert set(config["plugins"]) == set(links)
    assert any("Restart Glyphs" in line for line in lines)


def test_install_repoints_stale_links_but_keeps_real_directories(tmp_path):
    plugins_dir = tmp_path / "Plugins"
    plugins_dir.mkdir()
    stale = plugins_dir / "CornerRadii.glyphsReporter"
    stale.symlink_to(tmp_path / "gone" / "CornerRadii.glyphsReporter")  # dangling
    by_hand = plugins_dir / "WidthMatcher.glyphsReporter"
    by_hand.mkdir()
    lines = []

    status = glyphs_install.install(plugins_dir, tmp_path / "cfg.json", out=lines.append)

    assert status == 1
    assert stale.resolve() == (glyphs_install.SOURCE_DIR / "CornerRadii.glyphsReporter").resolve()
    assert by_hand.is_dir() and not by_hand.is_symlink()
    assert any(line.startswith("skipped") and "WidthMatcher" in line for line in lines)


def test_uninstall_removes_only_our_links_and_the_record(tmp_path):
    plugins_dir = tmp_path / "Plugins"
    config_path = tmp_path / "cfg.json"
    glyphs_install.install(plugins_dir, config_path, out=lambda _: None)
    foreign_target = tmp_path / "elsewhere" / "InstanceDelta.glyphsReporter"
    foreign_target.mkdir(parents=True)
    foreign = plugins_dir / "InstanceDelta.glyphsReporter"
    foreign.unlink()
    foreign.symlink_to(foreign_target)
    lines = []

    status = glyphs_install.uninstall(plugins_dir, config_path, out=lines.append)

    assert status == 0
    assert {p.name for p in plugins_dir.iterdir()} == {"InstanceDelta.glyphsReporter"}
    assert foreign.resolve() == foreign_target.resolve()
    assert not config_path.exists()
    assert any(line.startswith("kept") and "InstanceDelta" in line for line in lines)


def test_main_dispatches_install_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr(glyphs_install, "PLUGINS_DIR", tmp_path / "Plugins")
    monkeypatch.setattr(glyphs_install, "CONFIG_PATH", tmp_path / "cfg.json")

    assert glyphs_install.main([]) == 0
    assert (tmp_path / "cfg.json").exists()
    assert len(list((tmp_path / "Plugins").iterdir())) == len(glyphs_install.bundled_plugins())

    assert glyphs_install.main(["--uninstall"]) == 0
    assert not (tmp_path / "cfg.json").exists()
    assert list((tmp_path / "Plugins").iterdir()) == []
