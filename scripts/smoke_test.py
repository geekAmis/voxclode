"""Environment smoke test for the VoxCPM2 install.

Checks the Python interpreter, the installed voxcpm/torch versions and the
state of the CLI entry point without downloading model weights.
"""

from __future__ import annotations

import importlib.metadata as md
import platform
import shutil
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    print("python      :", sys.version.split()[0])
    print("executable  :", sys.executable)
    print("platform    :", platform.platform())
    print("project root:", root)

    ok = True
    for dist in (
        "voxcpm",
        "torch",
        "torchaudio",
        "transformers",
        "soundfile",
        "textual",
        "imageio-ffmpeg",
    ):
        try:
            print(f"{dist:<12}:", md.version(dist))
        except md.PackageNotFoundError:
            print(f"{dist:<12}: NOT INSTALLED")
            ok = False

    try:
        import torch

        print("torch cuda  :", torch.version.cuda)
        print("cuda ready  :", torch.cuda.is_available())
        if torch.cuda.is_available():
            print("gpu         :", torch.cuda.get_device_name(0))
    except Exception as exc:  # noqa: BLE001 - smoke test should report, not crash
        print("torch import failed:", exc)
        ok = False

    exe = shutil.which("voxcpm") or str(root / ".venv" / "Scripts" / "voxcpm.exe")
    print("voxcpm exe  :", exe, "(found)" if Path(exe).exists() else "(missing)")
    if not Path(exe).exists():
        ok = False

    try:
        import voxcpm  # noqa: F401

        print("import      : voxcpm OK")
    except Exception as exc:  # noqa: BLE001
        print("import      : FAILED ->", exc)
        ok = False

    print("RESULT      :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
