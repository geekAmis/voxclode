"""Entry point: python voxclode.py [audio-file]"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="voxclode",
        description="VoxCLode — colourful terminal studio for VoxCPM2 voice cloning.",
    )
    parser.add_argument(
        "audio",
        nargs="?",
        help="Optional reference audio file to pre-fill (wav/mp3/ogg/mp4/...).",
    )
    args = parser.parse_args()

    try:
        from voxclode.app import VoxClodeApp
    except ImportError as exc:  # textual is installed into .venv
        print(f"Не удалось импортировать VoxCLode: {exc}", file=sys.stderr)
        print("Установите зависимости: .\\scripts\\install.ps1", file=sys.stderr)
        return 1

    app = VoxClodeApp(initial_reference=args.audio)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
