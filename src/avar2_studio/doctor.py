"""Environment check for avar2-studio.

Invoked as ``avar2-studio doctor``. Validates that every external
dependency the tool needs is present, and prints a fix-it command for
each one that's missing.

Exits 0 if every check passes, 1 otherwise — so it's CI-safe.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional


# ANSI colour helpers — only emit colour if stdout is a TTY so logs and
# captured output stay clean.
def _supports_colour() -> bool:
    return sys.stdout.isatty()


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _supports_colour() else s


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _supports_colour() else s


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m" if _supports_colour() else s


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    fix: Optional[str] = None


def _run_version(binary: str, *args: str) -> Optional[str]:
    """Return ``binary --version`` output as a single line, or None if it fails."""
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0] if out else None


def check_python() -> CheckResult:
    """Python version meets the package requires-python."""
    major, minor = sys.version_info[:2]
    needed = (3, 10)
    if (major, minor) >= needed:
        return CheckResult(
            name="Python",
            ok=True,
            detail=f"{sys.version.split()[0]} at {sys.executable}",
        )
    return CheckResult(
        name="Python",
        ok=False,
        detail=f"{sys.version.split()[0]} (needs >= {needed[0]}.{needed[1]})",
        fix="install a newer Python (https://www.python.org/downloads/)",
    )


def check_fontc() -> CheckResult:
    """`fontc` Rust binary is on PATH (required for builds)."""
    path = shutil.which("fontc")
    if not path:
        return CheckResult(
            name="fontc",
            ok=False,
            detail="not on PATH",
            fix="brew install fontc    # or: cargo install fontc",
        )
    version = _run_version("fontc", "--version") or "version unknown"
    return CheckResult(name="fontc", ok=True, detail=f"{version} at {path}")


def check_gftools() -> CheckResult:
    """`gftools-builder` is on PATH (the builder we shell out to)."""
    path = shutil.which("gftools-builder")
    if not path:
        return CheckResult(
            name="gftools-builder",
            ok=False,
            detail="not on PATH",
            fix="pipx install gftools    # or: pip install gftools",
        )
    version = _run_version("gftools-builder", "--version") or "version unknown"
    return CheckResult(name="gftools-builder", ok=True, detail=f"{version} at {path}")


def check_glyphslib() -> CheckResult:
    """``glyphsLib`` Python package, needed to read .glyphs files."""
    spec = importlib.util.find_spec("glyphsLib")
    if spec is None:
        return CheckResult(
            name="glyphsLib",
            ok=False,
            detail="not importable",
            fix="pip install glyphsLib    # (should have come with avar2-studio's deps)",
        )
    try:
        import glyphsLib  # noqa: F401
        version = getattr(glyphsLib, "__version__", "version unknown")
    except Exception as e:
        return CheckResult(
            name="glyphsLib",
            ok=False,
            detail=f"failed to import: {e}",
            fix="pip install --upgrade glyphsLib",
        )
    return CheckResult(name="glyphsLib", ok=True, detail=str(version))


def check_static_bundle() -> CheckResult:
    """The pre-built React bundle that the server serves from ``static/``."""
    try:
        from . import server as _server  # noqa: F401
    except Exception:
        # Should never happen — but if it does, defer to the static check
        pass

    here = Path(__file__).parent
    bundle = here / "static" / "index.html"
    if bundle.exists():
        return CheckResult(
            name="frontend bundle",
            ok=True,
            detail=f"present at {bundle}",
        )
    return CheckResult(
        name="frontend bundle",
        ok=False,
        detail=f"missing index.html under {here / 'static'}",
        fix="cd frontend && npm ci && npm run build && rsync -a build/ ../src/avar2_studio/static/",
    )


def check_stale_processes() -> CheckResult:
    """Listening processes that look like avar2-studio servers or
    Fontra. A forgotten server from an old session squats its port and
    serves stale code (or, on Fontra's fixed :8001, silently kills the
    embedded editor's next launch: the fresh spawn dies on bind while
    the port probe sees the squatter and reports success)."""
    import os

    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return CheckResult(
            name="stale processes", ok=True,
            detail="lsof unavailable — check skipped",
        )

    # -F output: repeated blocks of "p<pid>" then "n<addr:port>" lines.
    listeners: dict = {}
    pid = None
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            port = line.rsplit(":", 1)[-1]
            if port.isdigit():
                listeners.setdefault(pid, set()).add(int(port))

    me = os.getpid()
    suspects = []
    fontra_squatter = False
    for lpid, ports in sorted(listeners.items()):
        if lpid == me:
            continue
        try:
            ps = subprocess.run(
                ["ps", "-p", str(lpid), "-o", "etime=,command="],
                capture_output=True, text=True, timeout=5,
            )
            info = ps.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            info = ""
        cmd = info.split(None, 1)[1] if len(info.split(None, 1)) == 2 else ""
        if not ("avar2_studio" in cmd or "avar2-studio" in cmd
                or "fontra" in cmd.lower()):
            continue
        age = info.split(None, 1)[0] if info else "?"
        plist = ",".join(str(p) for p in sorted(ports))
        suspects.append(f"pid {lpid} (up {age}) on :{plist}")
        if 8001 in ports:
            fontra_squatter = True

    if not suspects:
        return CheckResult(
            name="stale processes", ok=True, detail="none listening",
        )
    listing = "; ".join(suspects)
    if fontra_squatter:
        return CheckResult(
            name="stale processes", ok=False,
            detail=f"a process holds Fontra's port 8001 — {listing}",
            fix="kill <pid>  # the embedded editor cannot launch while :8001 is taken",
        )
    return CheckResult(
        name="stale processes", ok=True,
        detail=f"found: {listing} — fine if it's a session you're using; "
               "otherwise kill <pid>",
    )


def check_install_shadowing() -> CheckResult:
    """A concrete site-packages copy of avar2_studio coexisting with a
    different active import path — a stale ``pip install`` (vs the
    editable checkout) serves old code and an old frontend bundle,
    which surfaces as the UI calling the wrong API port."""
    import site

    from . import __file__ as pkg_init
    active = Path(pkg_init).parent.resolve()

    site_dirs = []
    try:
        site_dirs.extend(site.getsitepackages())
    except Exception:
        pass
    if site.getusersitepackages():
        site_dirs.append(site.getusersitepackages())

    for sp in site_dirs:
        candidate = Path(sp) / "avar2_studio"
        # Editable installs may plant a package-data helper dir here
        # (e.g. static/ only) — only a copy with __init__.py can
        # actually shadow the import.
        if (candidate / "__init__.py").exists() and candidate.resolve() != active:
            return CheckResult(
                name="install shadowing", ok=False,
                detail=f"active import is {active}, but a second install "
                       f"exists at {candidate}",
                fix=f"{sys.executable} -m pip uninstall avar2-studio  "
                    "# removes the stale copy",
            )
    return CheckResult(
        name="install shadowing", ok=True, detail=f"single install: {active}",
    )


def check_fontra() -> CheckResult:
    """Fontra powers the embedded outline editor. Optional — the rest
    of the studio works without it."""
    have = importlib.util.find_spec("fontra") is not None
    have_glyphs = importlib.util.find_spec("fontra_glyphs") is not None
    if have and have_glyphs:
        return CheckResult(name="fontra (optional)", ok=True, detail="installed")
    missing = [n for n, ok in (("fontra", have), ("fontra-glyphs", have_glyphs)) if not ok]
    return CheckResult(
        name="fontra (optional)", ok=True,
        detail=f"{' + '.join(missing)} not installed — the embedded outline "
               "editor is disabled. To enable: pip install "
               "git+https://github.com/fontra/fontra.git "
               "git+https://github.com/fontra/fontra-glyphs.git",
    )


CHECKS: List[Callable[[], CheckResult]] = [
    check_python,
    check_fontc,
    check_gftools,
    check_glyphslib,
    check_static_bundle,
    check_fontra,
    check_install_shadowing,
    check_stale_processes,
]


def run() -> int:
    """Run every check, print results, and return an exit code."""
    print("avar2-studio doctor — checking environment\n")

    name_w = max(len(c().name) for c in CHECKS) + 1
    results: List[CheckResult] = []

    for check in CHECKS:
        result = check()
        results.append(result)
        mark = _green("✓") if result.ok else _red("✗")
        print(f"  {mark} {result.name:<{name_w}} {_dim(result.detail)}")

    failures = [r for r in results if not r.ok]

    if not failures:
        print(f"\n{_green('All checks passed.')} You can run avar2-studio on a .glyphs file:")
        print("\n    avar2-studio /path/to/MyFont.glyphs\n")
        return 0

    print(f"\n{_red(f'{len(failures)} check(s) failed.')} Fix-it commands:\n")
    for r in failures:
        if r.fix:
            print(f"  # {r.name}: {r.detail}")
            print(f"  {r.fix}\n")
    return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
