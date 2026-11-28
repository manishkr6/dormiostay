"""Offline validation of sanitized recovery evidence."""

import argparse
from pathlib import Path

from app.db.recovery import RecoveryEvidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--observed-digest", required=True)
    args = parser.parse_args()
    try:
        RecoveryEvidence.model_validate_json(args.file.read_bytes()).check(args.observed_digest)
    except (ValueError, OSError):
        parser.exit(1, "Evidence rejected\n")
    print("Offline evidence consistent; external authorization required")


if __name__ == "__main__":
    main()
