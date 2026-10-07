"""MIT license and NOTICE must be inside the built wheel."""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path


def test_wheel_ships_license_and_notice(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        import pytest

        pytest.skip("uv is required to build the wheel")
    root = Path(__file__).resolve().parents[2]
    out = tmp_path / "wheel"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert any(name.endswith("LICENSE") for name in names)
    assert any(name.endswith("NOTICE") for name in names)
    assert any(name.endswith("vendor/NOTICE") or name.endswith("vendor/NOTICE".replace("/", "\\")) for name in names)
    assert any(name.endswith("py.typed") for name in names)
