"""Compute descriptive loader latencies and finite resource observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pressure.run import (  # noqa: E402
    EXPIRY_SEMANTICS,
    summarize,
    validate_dns_policy,
    validate_fixture_safety,
    validate_samples,
)

HISTORICAL_EXPIRY_SEMANTICS = "historical-fallback"


def evidence_digest(raw):
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_run(run, budget, historic_expiry=False, expiry_semantics=HISTORICAL_EXPIRY_SEMANTICS):
    """Validate outcomes against the workload, never against the observed errors."""
    if run["exit_code"] != 0 or run["resource_sampling_truncated"]:
        raise ValueError("failed run or truncated resource evidence")
    rules = {
        "warmup-refresh": (1, (None,), None),
        "warmup": (budget["warmup"], (None,), "matrix-bg"),
        "warm-verified-bg": (budget["warm_loads"], (None,), "matrix-bg"),
        "forced-signed-refresh": (budget["forced_refreshes"], (None,), None),
        "cold-signed-cache": (1, (None,), "matrix-bg"),
        "invalid-explicit": (8, ("ConfigInvalid",), None),
        "expired-explicit": (
            8,
            ("ConfigInvalid",)
            if expiry_semantics == HISTORICAL_EXPIRY_SEMANTICS
            and historic_expiry
            and run["candidate"] in {"go", "rust"}
            else ("ConfigExpired",),
            None,
        ),
        "invalid-automatic-fallback": (8, (None,), "matrix-bg"),
        "proxy-recovery": (4, (None,), "fixture-token"),
        "capacity-overload": (8, (None, "ResourceLimit"), "fixture-token"),
        "overload-recovery": (4, (None,), "fixture-token"),
        "dns-policy-socks5": (4, (None,), None),
        "dns-policy-socks5h": (4, (None,), None),
        "dns-proxy-only-remote": (4, (None,), None),
        "dns-proxy-only-local-negative": (4, ("TransportError", "ProxyConnectError"), None),
        "soak-equal-batch": (32, (None,), "fixture-token"),
    }
    if expiry_semantics == EXPIRY_SEMANTICS:
        rules["expired-automatic-floor"] = (8, ("ConfigExpired",), None)
        rules["expired-floor-recovery"] = (4, (None,), "matrix-bg")
    elif expiry_semantics == HISTORICAL_EXPIRY_SEMANTICS:
        rules["expired-automatic-fallback"] = (8, (None,), "matrix-bg")
    else:
        raise ValueError("unknown expiry semantics")
    for index in range(budget["equal_error_batches"]):
        rules[f"deadline-{index}"] = (budget["error_batch_size"], ("DeadlineExceeded",), None)
        rules[f"deadline-recovery-{index}"] = (4, (None,), "fixture-token")
        errors = (
            ("TransportError",)
            if run["candidate"] == "python"
            else ("ProxyAuthError", "ProxyConnectError")
        )
        rules[f"proxy-error-{index}"] = (budget["error_batch_size"], errors, None)
    counts = Counter(stage["name"] for stage in run["stages"])
    expected_counts = Counter({name: 1 for name in rules})
    expected_counts["cold-signed-cache"] = budget["cold_cache_loads"]
    expected_counts["soak-equal-batch"] = counts["soak-equal-batch"]
    if counts != expected_counts or not counts["soak-equal-batch"]:
        raise ValueError("missing, unexpected or duplicate workload stages")
    fixed = sum(len(s["samples"]) for s in run["stages"] if s["name"] != "soak-equal-batch")
    if fixed != budget["fixed_operations_per_candidate_round"]:
        raise ValueError("fixed operation budget does not match evidence")
    for stage in run["stages"]:
        name, samples, metrics = stage["name"], stage["samples"], stage["fixture_delta"]
        validate_fixture_safety(metrics)
        count, expected, value = rules[name]
        if len(samples) != count:
            raise ValueError("stage sample count mismatch")
        validate_samples(samples, expected, value)
        if name.startswith("deadline-") and "recovery" not in name:
            if any(s["elapsed_ns"] > (budget["deadline_ms"] + 100) * 1000000 for s in samples):
                raise ValueError("deadline control failed")
        if name in {
            "warm-verified-bg",
            "cold-signed-cache",
            "dns-proxy-only-local-negative",
        } or name.startswith("proxy-error-"):
            if metrics.get("requests", 0):
                raise ValueError("forbidden origin request in cache/proxy/DNS control")
        if name.startswith("proxy-error-") and metrics.get("proxy_auth_rejected", 0) < 1:
            raise ValueError("missing real proxy rejection")
        if name in {"dns-policy-socks5", "dns-policy-socks5h"}:
            validate_dns_policy(metrics, name.removeprefix("dns-policy-"))
        if name == "dns-proxy-only-remote" and (
            metrics.get("proxy_only_name_resolutions", 0) < 1 or metrics.get("path:/bundle", 0) != 4
        ):
            raise ValueError("remote-only DNS control missing")
        if name == "capacity-overload":
            if Counter(s["error"] or "success" for s in samples) != {
                "success": 2,
                "ResourceLimit": 6,
            }:
                raise ValueError("overload admission mismatch")
            if max(s["elapsed_ns"] for s in samples if s["error"]) >= min(
                s["elapsed_ns"] for s in samples if s["error"] is None
            ):
                raise ValueError("overload did not reject before admitted completion")
    actual_soak = sum(len(s["samples"]) for s in run["stages"] if s["name"] == "soak-equal-batch")
    if (
        actual_soak != run["soak_operations"]
        or not 0 < actual_soak <= budget["maximum_soak_operations"]
    ):
        raise ValueError("soak operation budget mismatch")
    if (
        not math.isfinite(run["soak_wall_seconds"])
        or run["soak_wall_seconds"] < budget["minimum_soak_seconds"]
    ):
        raise ValueError("soak duration shorter than planned")
    phases = Counter(p["phase"] for p in run["resource_observations"])
    if (
        phases["after-close"] != 1
        or phases["baseline-warmed"] != 1
        or phases["soak-batch"] != counts["soak-equal-batch"]
    ):
        raise ValueError("missing resource lifecycle observations")
    if not run["resource_samples_20ms"]:
        raise ValueError("missing resource samples")
    points = run["resource_observations"]
    if points[0]["phase"] != "baseline-warmed" or points[-1]["phase"] != "after-close":
        raise ValueError("resource lifecycle order mismatch")
    for index in range(budget["equal_error_batches"]):
        if phases[f"deadline-batch-{index}"] != 1 or phases[f"proxy-batch-{index}"] != 1:
            raise ValueError("missing equal error-batch resource observation")


def report(raw, exit_record):
    if exit_record.get("exit_code") != 0:
        raise ValueError("runner completion with original exit status zero is required")
    if exit_record.get("canonical_raw_sha256") != evidence_digest(raw):
        raise ValueError("completion record does not bind this raw evidence")
    expiry_semantics = raw["plan"].get("expiry_semantics", HISTORICAL_EXPIRY_SEMANTICS)
    if expiry_semantics not in {HISTORICAL_EXPIRY_SEMANTICS, EXPIRY_SEMANTICS}:
        raise ValueError("unknown expiry semantics")
    candidates = raw["plan"]["candidates"]
    if (
        not candidates
        or len(candidates) != len(set(candidates))
        or not set(candidates) <= {"python", "node", "go", "rust"}
    ):
        raise ValueError("invalid candidate set")
    expected = {
        (candidate, index)
        for candidate in candidates
        for index in range(raw["plan"]["budget"]["rounds"])
    }
    actual = [(run["candidate"], run["round"]) for run in raw["results"]]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("partial or duplicate run evidence")
    for run in raw["results"]:
        validate_run(
            run,
            raw["plan"]["budget"],
            exit_record.get("historical_expiry_revision") == "2",
            expiry_semantics,
        )
    rows, resources = [], []
    for candidate in candidates:
        runs = [run for run in raw["results"] if run["candidate"] == candidate]
        for name in ("warm-verified-bg", "cold-signed-cache", "forced-signed-refresh"):
            samples = [
                s
                for run in runs
                for stage in run["stages"]
                if stage["name"] == name
                for s in stage["samples"]
            ]
            rows.append({"candidate": candidate, "profile": name, **summarize(samples)})
        for run in runs:
            points = run["resource_observations"]
            before, after = points[0], points[-1]
            resource = {
                "candidate": candidate,
                "round": run["round"],
                "soak_operations": run["soak_operations"],
                "soak_seconds": run["soak_wall_seconds"],
                "after_close": after,
            }
            for field in ("fds", "threads", "rss_bytes"):
                resource[f"after_close_minus_warm_{field}"] = after[field] - before[field]
                resource[f"observed_peak_{field}"] = max(
                    point[field] for point in run["resource_samples_20ms"]
                )
                for phase in ("deadline-batch-", "proxy-batch-", "soak-batch"):
                    selected = [p[field] for p in points if p["phase"].startswith(phase)]
                    resource[f"last_minus_first_{phase.rstrip('-')}_{field}"] = (
                        selected[-1] - selected[0]
                    )
            resources.append(resource)
    errors = Counter(
        s["error"] or "success"
        for run in raw["results"]
        for stage in run["stages"]
        for s in stage["samples"]
    )
    return {
        "coverage": {
            "expiry_semantics": expiry_semantics,
            "current_expiry_floor_qualified": expiry_semantics == EXPIRY_SEMANTICS,
        },
        "latencies": rows,
        "resources": resources,
        "public_results": dict(errors),
        "total_operations": sum(errors.values()),
        "runs": len(raw["results"]),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exit-record", type=Path, required=True)
    args = parser.parse_args()
    data = report(json.loads(args.raw.read_text()), json.loads(args.exit_record.read_text()))
    if not data["coverage"]["current_expiry_floor_qualified"]:
        print(
            "Historical expiry fallback evidence does not qualify the current revision floor.",
            file=sys.stderr,
        )
    args.output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
