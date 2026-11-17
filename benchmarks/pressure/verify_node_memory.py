"""Diagnostic-only forced-GC control over a fixed number of equal solve batches."""

import contextlib
import hashlib
import json
import sys
from pathlib import Path

from run import HERE, Peer, read_line, read_process, terminate, validate_samples


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    budget = {"batches": 64, "batch_size": 128, "concurrency": 2, "warmup": 64, "settle_ms": 200}
    budget["total_solve_operations"] = budget["warmup"] + budget["batches"] * budget["batch_size"]
    plan = {
        "command": [sys.executable, *sys.argv],
        "budget": budget,
        "diagnostic_only": "--expose-gc; GC is not used in ordinary workload or package code",
        "source_hashes": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (HERE / "node_adapter.cjs", HERE / "fixture.py", Path(__file__))
        },
    }
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    fixture_dir = output / "fixture"
    fixture_dir.mkdir()
    fixture = Peer([sys.executable, str(HERE / "fixture.py"), str(fixture_dir)], output, "fixture")
    peer = None
    observations, samples = [], []
    try:
        metadata = read_line(fixture.process)["fixture"]
        job = output / "node.job.json"
        job.write_text(json.dumps({"fixture": metadata, "cache_path": str(output / "cache.json")}))
        peer = Peer(
            ["node", "--expose-gc", str(HERE / "node_adapter.cjs"), str(job)], output, "node"
        )
        response = peer.call(
            dict(
                op="create",
                offline=False,
                proxy_route="http",
                refresh_interval=3600,
                timeout_ms=1000,
                max_in_flight=2,
            )
        )
        if response.get("ok") is not True:
            raise AssertionError(response)
        seed = peer.call({"op": "refresh", "count": 1, "concurrency": 1})["samples"]
        validate_samples(seed, (None,))
        warmup = peer.call({"op": "solve", "count": budget["warmup"], "concurrency": 2})["samples"]
        validate_samples(warmup, (None,), "fixture-token")

        def observe(index):
            peer.call({"op": "settle", "milliseconds": budget["settle_ms"]})
            before = peer.call({"op": "resources", "collect": False})
            after = peer.call({"op": "resources", "collect": True})
            observations.append(
                {
                    "completed_equal_batches": index,
                    "solves_after_warmup": index * budget["batch_size"],
                    "before_gc": before,
                    "after_gc": after,
                    "process": read_process(peer.process.pid),
                }
            )

        observe(0)
        for index in range(budget["batches"]):
            batch = peer.call({"op": "solve", "count": budget["batch_size"], "concurrency": 2})[
                "samples"
            ]
            validate_samples(batch, (None,), "fixture-token")
            samples.extend(batch)
            observe(index + 1)
        peer.call({"op": "close"})
        observe(budget["batches"])
        code = peer.close()
        fixture_code = fixture.close(fixture=True)
        data = {
            "plan": plan,
            "seed": seed,
            "warmup": warmup,
            "samples": samples,
            "observations": observations,
            "exit_code": code,
            "fixture_exit_code": fixture_code,
            "resource_samples_20ms": peer.observations,
        }
        (output / "raw.json").write_text(json.dumps(data, indent=2) + "\n")
        print(json.dumps({"exit_code": code, "fixture_exit_code": fixture_code, "budget": budget}))
    except BaseException:
        if peer:
            terminate(peer.process)
            with contextlib.suppress(Exception):
                peer.close()
        terminate(fixture.process)
        with contextlib.suppress(Exception):
            fixture.close()
        raise


if __name__ == "__main__":
    main()
