"""hatchling build hook: the native accelerators make the wheel platform-specific.

When ``src/avar2_studio/bin/`` holds the cargo-built ``spac-inject`` and
``libkinetikern2`` (gitignored build outputs, staged by release.yml's platform
job), force-include them as ``avar2_studio/bin/`` and tag the wheel
``py3-none-<plat>`` + non-pure so pip installs into platlib and the discovery
order in ``builtin_spac_widthaware._native_injector`` / ``kinetikern.engine_path``
finds the package-carried copies. The force-include must live here, not in
pyproject.toml: hatchling raises FileNotFoundError for a missing force-include
source, and bin/ is absent on a clean checkout. A tree without binaries builds
the pure ``py3-none-any`` wheel as before (fontTools SPAC fallback; Kinetikern
disabled). Decision: docs/decisions/0015.
"""

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class NativeAcceleratorsHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        bin_dir = Path(self.root) / "src" / "avar2_studio" / "bin"
        if not bin_dir.is_dir() or not any(bin_dir.iterdir()):
            return
        build_data["force_include"]["src/avar2_studio/bin"] = "avar2_studio/bin"
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{self._platform_tag()}"

    def _platform_tag(self):
        # The platform half of hatchling's WheelBuilder.get_best_matching_tag;
        # the binaries are Python-version-agnostic, so the tag stays py3-none.
        from packaging.tags import sys_tags

        tag = next(t for t in sys_tags() if "manylinux" not in t.platform and "musllinux" not in t.platform)
        plat = tag.platform
        if sys.platform == "darwin":
            from hatchling.builders.macos import process_macos_plat_tag

            compat = getattr(self.build_config, "macos_max_compat", False)
            plat = process_macos_plat_tag(plat, compat=compat)
        return plat
