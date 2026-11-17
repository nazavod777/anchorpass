"""Replace affected matrix jobs while preserving explicit run provenance."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


def identity(row):
    job = row["job"]
    return job["candidate"], job["profile"]["name"], job["round"]


def merge(base, replacement, *, base_path, replacement_path, reason):
    for raw in (base, replacement):
        if raw.get("sources_unchanged") is not True:
            raise ValueError("source integrity was not confirmed")
        if raw["smoke"] or len(raw["jobs"]) != raw["planned_jobs"]:
            raise ValueError("only complete measured runs may be combined")
        if any(row["validation_errors"] for row in raw["jobs"]):
            raise ValueError("invalid run cannot be combined")
        if len({identity(row) for row in raw["jobs"]}) != len(raw["jobs"]):
            raise ValueError("duplicate job identity")
    if base["manifest"] != replacement["manifest"]:
        raise ValueError("profile manifests differ")
    combined = copy.deepcopy(base)
    positions = {identity(row): index for index, row in enumerate(combined["jobs"])}
    for row in combined["jobs"]:
        row["evidence_run"] = "baseline"
    replaced = []
    for row in replacement["jobs"]:
        key = identity(row)
        if key not in positions:
            raise ValueError(f"replacement has no original job: {key}")
        original = combined["jobs"][positions[key]]
        if row["job"]["profile"] != original["job"]["profile"]:
            raise ValueError(f"replacement changes workload: {key}")
        changed = copy.deepcopy(row)
        changed["evidence_run"] = "replacement"
        combined["jobs"][positions[key]] = changed
        replaced.append(list(key))
    combined["provenance"] = {
        "baseline_path": str(base_path),
        "replacement_path": str(replacement_path),
        "reason": reason,
        "replaced_jobs": replaced,
        "replacement_environment": replacement["environment"],
        "replacement_builds": replacement["builds"],
        "note": "Only affected jobs were repeated; original observations remain at baseline_path. Replacement source hashes are independent of baseline hashes.",
    }
    return combined


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("replacement", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    base, replacement = (json.loads(path.read_text()) for path in (args.base, args.replacement))
    combined = merge(
        base,
        replacement,
        base_path=args.base.resolve(),
        replacement_path=args.replacement.resolve(),
        reason=args.reason,
    )
    combined["provenance"]["baseline_sha256"] = hashlib.sha256(args.base.read_bytes()).hexdigest()
    combined["provenance"]["replacement_sha256"] = hashlib.sha256(
        args.replacement.read_bytes()
    ).hexdigest()
    with args.output.open("x") as stream:
        json.dump(combined, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"jobs": len(combined["jobs"]), "replaced": len(replacement["jobs"])}))


if __name__ == "__main__":
    main()
