"""Shared pytest fixtures: throwaway copies of the example sources.

The studio writes its sidecars (``-control.json``, ``-transforms.json``)
and ``.avar2-studio/shadow/`` workdirs NEXT to the source file, so every
test that exercises a writing code path gets its own copy under
``tmp_path`` — ``examples/`` itself is never touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CRISPY_SOURCES = EXAMPLES / "crispy-mini" / "sources"
ROBOTO_SOURCES = EXAMPLES / "roboto-delta-mini" / "sources"


@pytest.fixture(scope="session", autouse=True)
def _isolated_user_transforms_dir(tmp_path_factory):
    """Point transform discovery at a throwaway directory.

    ``registry.discover()`` otherwise creates + reads
    ``~/.avar2-studio/transforms/`` — outside the repo, and whatever
    user scripts happen to live there would leak into REGISTRY and make
    the built-in assertions machine-dependent.
    """
    from avar2_studio.transforms import registry

    original = registry.user_transforms_dir
    isolated = tmp_path_factory.mktemp("user-transforms")
    registry.user_transforms_dir = lambda: isolated
    registry.discover(force=True)
    yield
    registry.user_transforms_dir = original


@pytest.fixture
def crispy_source(tmp_path):
    """A writable copy of the CrispyMini .glyphs source."""
    dst = tmp_path / "CrispyMini.glyphs"
    shutil.copy2(CRISPY_SOURCES / "CrispyMini.glyphs", dst)
    return dst


@pytest.fixture
def crispy_csv(tmp_path):
    """A writable copy of the CrispyMini avar2 mappings CSV."""
    dst = tmp_path / "CrispyMini-avar.csv"
    shutil.copy2(CRISPY_SOURCES / "CrispyMini-avar.csv", dst)
    return dst


@pytest.fixture
def roboto_source(tmp_path):
    """A writable copy of the RobotoDeltaMini .designspace + sibling UFOs.

    The designspace references its UFOs by relative filename, so the
    whole sources dir comes along.
    """
    dst = tmp_path / "sources"
    shutil.copytree(ROBOTO_SOURCES, dst)
    return dst / "RobotoDeltaMini.designspace"
