"""Offline validation of sanitized render evidence."""

import argparse
from pathlib import Path

from app.db.render_readiness import RenderInventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        RenderInventory.model_validate_json(args.file.read_bytes()).check()
    except (ValueError, OSError):
        parser.exit(1, "Evidence rejected\n")
    print("Offline evidence consistent; external authorization required")


if __name__ == "__main__":
    main()
