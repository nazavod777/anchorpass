"""Focused expiry negative control, independent of same-revision conflicts."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pressure.run import (  # noqa: E402
    CANDIDATES,
    EXPIRY_SEMANTICS,
    HERE,
    ROOT,
    Peer,
    read_line,
    summarize,
    terminate,
    validate_fixture_safety,
    validate_samples,
)


def operation(peer, op, count, expected, value=None):
    response = peer.call({"op": op, "count": count, "concurrency": 1})
    if response.get("ok") is not True or len(response.get("samples", [])) != count:
        raise AssertionError(f"invalid {op} response for {count} requested operations: {response}")
    samples = response["samples"]
    validate_samples(samples, expected, value)
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture_directory = output / "fixture"
    fixture_directory.mkdir()
    fixture = Peer(
        [sys.executable, str(HERE / "fixture.py"), str(fixture_directory)], output, "fixture"
    )
    commands = {
        "python": [sys.executable, str(HERE / "python_adapter.py")],
        "node": ["node", str(HERE / "node_adapter.cjs")],
        "go": [str(HERE / "go/pressure-go")],
        "rust": [str(HERE / "rust/target/release/pressure-rust")],
    }
    results = []
    plan = {
        "command": [sys.executable, *sys.argv],
        "expiry_semantics": EXPIRY_SEMANTICS,
        "initial_seed_revision": "2",
        "expired_revision_step": 1,
        "recovery_revision_step": 2,
        "seed_count": 1,
        "refresh_count": 8,
        "automatic_floor_count": 8,
        "recovery_count": 4,
        "candidates": CANDIDATES,
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (HERE / "fixture.py", Path(__file__))
        },
    }
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    try:
        metadata = read_line(fixture.process)["fixture"]
        for candidate in CANDIDATES:
            job = output / f"{candidate}.job.json"
            job.write_text(
                json.dumps(
                    {"fixture": metadata, "cache_path": str(output / f"{candidate}.cache.json")}
                )
            )
            peer = Peer([*commands[candidate], str(job)], output, candidate)
            try:
                fixture.call({"command": "mode:normal"})
                response = peer.call(
                    dict(
                        op="create",
                        offline=False,
                        proxy_route="http",
                        refresh_interval=0,
                        timeout_ms=1000,
                        max_in_flight=2,
                    )
                )
                if response.get("ok") is not True:
                    raise AssertionError(response)
                seed = operation(peer, "refresh", plan["seed_count"], (None,))
                fixture.call({"command": "mode:expired"})
                explicit = operation(peer, "refresh", plan["refresh_count"], ("ConfigExpired",))
                automatic = operation(
                    peer, "load", plan["automatic_floor_count"], ("ConfigExpired",)
                )
                fixture.call({"command": "mode:recover"})
                recovery = operation(peer, "load", plan["recovery_count"], (None,), "matrix-bg")
                validate_fixture_safety(fixture.call({"command": "state"}))
                peer.call({"op": "close"})
                code = peer.close()
                results.append(
                    {
                        "candidate": candidate,
                        "command": peer.command,
                        "exit_code": code,
                        "seed": seed,
                        "explicit": explicit,
                        "automatic_floor": automatic,
                        "recovery": recovery,
                        "explicit_summary": summarize(explicit),
                        "automatic_floor_summary": summarize(automatic),
                        "recovery_summary": summarize(recovery),
                    }
                )
            except BaseException:
                terminate(peer.process)
                with contextlib.suppress(Exception):
                    peer.close()
                raise
        validate_fixture_safety(fixture.call({"command": "state"}))
        code = fixture.close(fixture=True)
        (output / "raw.json").write_text(
            json.dumps({"plan": plan, "results": results, "fixture_exit_code": code}, indent=2)
            + "\n"
        )
        print(
            json.dumps(
                {
                    "candidates": len(results),
                    "exit_code": code,
                    "operations": sum(
                        len(r["seed"])
                        + len(r["explicit"])
                        + len(r["automatic_floor"])
                        + len(r["recovery"])
                        for r in results
                    ),
                }
            )
        )
    except BaseException:
        terminate(fixture.process)
        with contextlib.suppress(Exception):
            fixture.close()
        raise


if __name__ == "__main__":
    main()
