"""``avar2-studio install-glyphs-plugins``: put the bundled Glyphs 3 plugins
into Glyphs' Plugins folder.

Every bundle in ``avar2_studio/glyphs/`` — the ``avar2 Studio`` hub plugin
and the design tools it lists — is symlinked, not copied, so upgrading the
package (or editing a checkout installed with ``pip install -e``) shows up
on the next Glyphs launch without re-running this. The interpreter that ran
the install is recorded in ``~/.avar2-studio/glyphs-plugin.json``; the hub
plugin's *Open in avar2 Studio* starts the server from it.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Callable, List

PLUGINS_DIR = Path.home() / "Library" / "Application Support" / "Glyphs 3" / "Plugins"
CONFIG_PATH = Path.home() / ".avar2-studio" / "glyphs-plugin.json"
SOURCE_DIR = Path(__file__).resolve().parent / "glyphs"
BUNDLE_SUFFIXES = (".glyphsPlugin", ".glyphsReporter", ".glyphsTool")


def bundled_plugins(source_dir: Path = SOURCE_DIR) -> List[Path]:
    return sorted(p for p in source_dir.iterdir() if p.is_dir() and p.suffix in BUNDLE_SUFFIXES)


def _version() -> str:
    try:
        return metadata.version("avar2-studio")
    except metadata.PackageNotFoundError:
        return "unknown"


def install(plugins_dir: Path = PLUGINS_DIR, config_path: Path = CONFIG_PATH,
            source_dir: Path = SOURCE_DIR, out: Callable[[str], None] = print) -> int:
    """Link every bundle into ``plugins_dir`` and write the launcher config.

    An existing symlink of the same name is re-pointed (a stale link from an
    older location is the common case); a real directory is left alone and
    reported, since it was put there by hand. Returns the exit status.
    """
    bundles = bundled_plugins(source_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for bundle in bundles:
        target = plugins_dir / bundle.name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            out(f"skipped  {target}: exists and is not a symlink — remove it, then rerun")
            status = 1
            continue
        target.symlink_to(bundle)
        out(f"linked   {target} -> {bundle}")
    config = {
        "python": sys.executable,
        "version": _version(),
        "plugins": [b.name for b in bundles],
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    out(f"wrote    {config_path}")
    out("Restart Glyphs: the tools are under Window → avar2 Studio.")
    return status


def uninstall(plugins_dir: Path = PLUGINS_DIR, config_path: Path = CONFIG_PATH,
              source_dir: Path = SOURCE_DIR, out: Callable[[str], None] = print) -> int:
    """Remove the links ``install`` made (dangling ones included) and the
    config; a link that points somewhere else is not ours and is kept."""
    for bundle in bundled_plugins(source_dir):
        target = plugins_dir / bundle.name
        if not target.is_symlink():
            if target.exists():
                out(f"kept     {target}: not a symlink, so not installed by avar2-studio")
            continue
        if target.exists() and target.resolve() != bundle.resolve():
            out(f"kept     {target}: points elsewhere ({target.resolve()})")
            continue
        target.unlink()
        out(f"removed  {target}")
    if config_path.exists():
        config_path.unlink()
        out(f"removed  {config_path}")
    return 0


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="avar2-studio install-glyphs-plugins",
                                 description="Install the bundled Glyphs 3 plugins (symlinks) "
                                             "and record this Python for the Glyphs menu.")
    ap.add_argument("--uninstall", action="store_true", help="remove the links and the record")
    args = ap.parse_args(argv)
    if args.uninstall:
        return uninstall(PLUGINS_DIR, CONFIG_PATH)
    return install(PLUGINS_DIR, CONFIG_PATH)
