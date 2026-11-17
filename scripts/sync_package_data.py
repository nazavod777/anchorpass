"""Synchronize standalone package data; --check performs no writes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATIONS = ["python/src/anchorpass/data", "js/data", "go/data", "rust/data"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures = []
    for name in ("bundle.json", "keys.json"):
        source = (ROOT / "data/bundled" / name).read_bytes()
        for destination in DESTINATIONS:
            path = ROOT / destination / name
            if args.check:
                if not path.is_file() or path.read_bytes() != source:
                    failures.append(str(path.relative_to(ROOT)))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(source)
    if failures:
        print("Package data differs: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("PASS: standalone package data matches shared source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
