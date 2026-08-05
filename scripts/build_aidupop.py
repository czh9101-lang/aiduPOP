#!/usr/bin/env python3
"""Build the `aidupop` PyPI alias distribution from this repo.

The canonical package is `hermes-lark-streaming` (root pyproject.toml).
`aidupop` is the aidu-family brand alias published from the *same* source
tree, sharing plugin.yaml as the single version source of truth.

Strategy: stage a clean copy of the repo in a temp dir, swap in
packaging/pyproject.aidupop.toml as its pyproject.toml, build there, and
copy the artifacts back to dist/. The working tree is never mutated, so an
interrupted build can never leave the repo in a half-renamed state.

Usage:
    python3 scripts/build_aidupop.py            # sdist + wheel
    python3 scripts/build_aidupop.py --wheel    # wheel only

Memory note: this server has ~3.5G RAM. We call setuptools directly with
`--no-isolation` instead of `python -m build`'s default isolated venv,
which otherwise pip-installs a fresh toolchain per run and has OOM-killed
the session before.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALIAS_PYPROJECT = REPO / "packaging" / "pyproject.aidupop.toml"

EXCLUDE = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "dist", "build",
    "*.egg-info", ".ruff_cache", ".venv", "venv",
)


def read_version() -> str:
    for line in (REPO / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no version: field in plugin.yaml")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wheel", action="store_true", help="wheel only, skip sdist")
    args = ap.parse_args()

    if not ALIAS_PYPROJECT.exists():
        raise SystemExit(f"missing {ALIAS_PYPROJECT}")

    version = read_version()
    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aidupop-build-") as tmp:
        stage = Path(tmp) / "aidupop"
        shutil.copytree(REPO, stage, ignore=EXCLUDE)

        # Swap in the alias metadata; setup.py still reads plugin.yaml for version.
        shutil.copy2(ALIAS_PYPROJECT, stage / "pyproject.toml")
        shutil.rmtree(stage / "packaging", ignore_errors=True)

        targets = ["bdist_wheel"] if args.wheel else ["sdist", "bdist_wheel"]
        cmd = [sys.executable, "setup.py", *targets]
        print(f"[aidupop] building {version} in {stage}")
        proc = subprocess.run(cmd, cwd=stage)
        if proc.returncode != 0:
            return proc.returncode

        built = sorted((stage / "dist").glob(f"aidupop-{version}*"))
        if not built:
            raise SystemExit(f"no artifacts matching aidupop-{version}* were produced")
        for f in built:
            shutil.copy2(f, dist / f.name)
            print(f"[aidupop] -> dist/{f.name}")

    print(f"[aidupop] done. Upload with:\n"
          f"    python3 -m twine upload dist/aidupop-{version}*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
