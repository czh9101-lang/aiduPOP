"""Packaging + import-cycle regression tests.

Two v2.0.2 regressions are guarded here, both of which only ever surfaced
for `pip install` users (the Hermes directory-plugin path loads the repo
root directly and masked them):

1. ``pyproject.toml`` used ``packages.find`` with bare-name includes, so
   setuptools registered the sub-packages as top-level ``controller``,
   ``cardkit``, ... instead of ``hermes_lark_streaming.controller``. The
   installed tree then had no ``hermes_lark_streaming`` package at all.
2. ``patching/hooks.py`` did a module-level ``from ..controller import
   get_controller`` while ``controller.linear_mixin`` imports from
   ``..patching`` — a hard cycle that raised ImportError whenever
   ``hermes_lark_streaming.controller`` was imported first.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_SUBPACKAGES = {
    "aowen",
    "cardkit",
    "config",
    "controller",
    "feishu",
    "flush",
    "patching",
    "plugin",
    "state",
}

PYPROJECTS = [
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "packaging" / "pyproject.aidupop.toml",
]


class TestWheelLayoutConfig:
    """Both package names must declare the same prefixed package list."""

    def test_all_pyprojects_exist(self) -> None:
        for path in PYPROJECTS:
            assert path.is_file(), f"missing {path}"

    def test_no_bare_name_package_discovery(self) -> None:
        """`packages.find` with bare includes is what broke 2.0.0/2.0.1."""
        for path in PYPROJECTS:
            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            setuptools_cfg = cfg.get("tool", {}).get("setuptools", {})
            assert "packages" in setuptools_cfg, (
                f"{path.name}: expected an explicit [tool.setuptools] packages "
                "list; automatic discovery registers sub-packages under their "
                "bare directory names and breaks relative imports once installed"
            )
            assert not isinstance(setuptools_cfg["packages"], dict), (
                f"{path.name}: packages must be an explicit list, not a "
                "find/find-namespace table"
            )

    def test_every_subpackage_is_prefixed(self) -> None:
        for path in PYPROJECTS:
            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            packages = cfg["tool"]["setuptools"]["packages"]
            assert "hermes_lark_streaming" in packages, (
                f"{path.name}: the root package itself must be listed, otherwise "
                "`import hermes_lark_streaming` fails after install"
            )
            declared = {
                p.split(".", 1)[1]
                for p in packages
                if p.startswith("hermes_lark_streaming.")
            }
            assert declared == EXPECTED_SUBPACKAGES, (
                f"{path.name}: declared sub-packages {sorted(declared)} != "
                f"{sorted(EXPECTED_SUBPACKAGES)}"
            )
            for p in packages:
                assert p == "hermes_lark_streaming" or p.startswith(
                    "hermes_lark_streaming."
                ), f"{path.name}: {p!r} is not under the hermes_lark_streaming prefix"

    def test_package_dir_and_data(self) -> None:
        for path in PYPROJECTS:
            cfg = tomllib.loads(path.read_text(encoding="utf-8"))
            setuptools_cfg = cfg["tool"]["setuptools"]
            assert setuptools_cfg["package-dir"]["hermes_lark_streaming"] == "."
            assert "plugin.yaml" in setuptools_cfg["package-data"]["hermes_lark_streaming"], (
                f"{path.name}: plugin.yaml must ship inside the package — "
                "__init__.py reads __version__ from it"
            )

    def test_declared_subpackages_exist_on_disk(self) -> None:
        for name in EXPECTED_SUBPACKAGES:
            assert (REPO_ROOT / name / "__init__.py").is_file(), (
                f"{name}/__init__.py is declared in pyproject but missing on disk"
            )

    def test_no_orphan_subpackage_on_disk(self) -> None:
        """A new sub-package must be added to both pyprojects, not just shipped."""
        skip = {"tests", "docs", "scripts", "packaging", "assets", "build", "dist"}
        on_disk = {
            d.name
            for d in REPO_ROOT.iterdir()
            if d.is_dir()
            and not d.name.startswith((".", "__"))
            and d.name not in skip
            and not d.name.endswith(".egg-info")
            and (d / "__init__.py").is_file()
        }
        assert on_disk == EXPECTED_SUBPACKAGES, (
            f"sub-packages on disk {sorted(on_disk)} differ from the declared set "
            f"{sorted(EXPECTED_SUBPACKAGES)} — update both pyproject files"
        )


class TestImportCycle:
    """Importing any sub-package first must not raise (v2.0.2 regression)."""

    _BOOTSTRAP = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'hermes_lark_streaming', {init!r},\n"
        "    submodule_search_locations=[{root!r}])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['hermes_lark_streaming'] = m\n"
        "spec.loader.exec_module(m)\n"
    )

    def _run(self, body: str) -> subprocess.CompletedProcess[str]:
        code = self._BOOTSTRAP.format(
            init=str(REPO_ROOT / "__init__.py"), root=str(REPO_ROOT)
        ) + body
        return subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_controller_imported_first(self) -> None:
        proc = self._run(
            "from hermes_lark_streaming.controller import StreamCardController, "
            "get_controller\n"
            "print('OK', StreamCardController.__name__)\n"
        )
        assert proc.returncode == 0, (
            "importing controller before patching must not hit the "
            f"hooks -> controller cycle:\n{proc.stderr[-2000:]}"
        )
        assert "OK StreamCardController" in proc.stdout

    def test_patching_imported_first(self) -> None:
        proc = self._run(
            "from hermes_lark_streaming.patching import apply_patches\n"
            "from hermes_lark_streaming.controller import get_controller\n"
            "print('OK', apply_patches.__name__)\n"
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "OK apply_patches" in proc.stdout

    def test_hooks_get_controller_is_lazy(self) -> None:
        """hooks must not resolve the controller at module import time."""
        proc = self._run(
            "import hermes_lark_streaming.patching.hooks as h\n"
            "import sys\n"
            "print('hooks_loaded', 'hermes_lark_streaming.patching.hooks' in sys.modules)\n"
            "print('callable', callable(h.get_controller))\n"
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "hooks_loaded True" in proc.stdout
        assert "callable True" in proc.stdout

    def test_root_version_matches_plugin_yaml(self) -> None:
        proc = self._run("print('VERSION', m.__version__)\n")
        assert proc.returncode == 0, proc.stderr[-2000:]
        version = proc.stdout.split("VERSION", 1)[1].strip().splitlines()[0]
        yaml_version = next(
            line.split(":", 1)[1].strip().strip('"').strip("'")
            for line in (REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines()
            if line.startswith("version:")
        )
        assert version == yaml_version, (
            f"__version__ {version!r} != plugin.yaml {yaml_version!r}"
        )
        assert version != "unknown", "plugin.yaml was not readable from the package"
