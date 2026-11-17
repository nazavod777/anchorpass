"""Compute descriptive matrix statistics and a standalone interactive chart."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(raw):
    grouped = defaultdict(list)
    counts = Counter()
    capabilities = defaultdict(dict)
    reasons = {}
    for row in raw["jobs"]:
        result, job = row["result"], row["job"]
        counts["jobs"] += 1
        if row["validation_errors"]:
            counts["invalid_jobs"] += 1
            continue
        name, profile = job["candidate"], job["profile"]
        if result["status"] == "unsupported":
            counts["unsupported_jobs"] += 1
            reasons[f"{name}/{profile['name']}"] = result["reason"]
            if profile["expected_error"]:
                capabilities[name][profile["name"]] = "unsupported"
            continue
        counts["executed_jobs"] += 1
        samples, recovery = result["samples"], result.get("recovery") or []
        failed = sum(not item["ok"] for item in samples)
        failed_recovery = sum(not item["ok"] for item in recovery)
        counts["measured_observations"] += len(samples)
        counts["failed_observations"] += failed
        counts["recovery_observations"] += len(recovery)
        counts["failed_recovery_observations"] += failed_recovery
        if profile["expected_error"]:
            capabilities[name][profile["name"]] = (
                "passed" if failed + failed_recovery == 0 else "failed"
            )
        else:
            grouped[(name, profile["name"])].append(row)
    groups = []
    required_controls = [item["name"] for item in raw["manifest"]["capabilities"]]
    for (candidate, profile), rows in sorted(grouped.items()):
        samples = [sample for row in rows for sample in row["result"]["samples"]]
        values = [sample["elapsed_ns"] / 1000000 for sample in samples if sample["ok"]]
        round_p50 = [
            statistics.median(
                [s["elapsed_ns"] / 1000000 for s in row["result"]["samples"] if s["ok"]]
            )
            for row in rows
            if any(sample["ok"] for sample in row["result"]["samples"])
        ]
        raw_profile = rows[0]["job"]["profile"]
        groups.append(
            {
                "candidate": candidate,
                "profile": profile,
                "kind": raw_profile["kind"],
                "rounds": len(rows),
                "successful_rounds": len(round_p50),
                "samples": len(samples),
                "failed": sum(not sample["ok"] for sample in samples),
                "p50_ms": statistics.median(values) if values else None,
                "p95_ms": percentile(values, 0.95) if values else None,
                "p99_ms": percentile(values, 0.99) if values else None,
                "round_p50_min_ms": min(round_p50) if round_p50 else None,
                "round_p50_max_ms": max(round_p50) if round_p50 else None,
                "throughput_rps": statistics.median(
                    sum(sample["ok"] for sample in row["result"]["samples"])
                    * 1000000000
                    / row["result"]["wall_ns"]
                    for row in rows
                ),
                "cpu_ms_per_op": statistics.median(
                    row["result"]["cpu_ns"] / len(row["result"]["samples"]) / 1000000
                    for row in rows
                ),
                "rss_peak_mib": max(
                    row["execution"]["resources"]["rss_peak_bytes"] or 0 for row in rows
                )
                / 1048576,
                "threads_peak": max(
                    row["execution"]["resources"]["threads_peak"] or 0 for row in rows
                ),
                "fds_peak": max(row["execution"]["resources"]["fds_peak"] or 0 for row in rows),
                "fixture_cpu_fraction_max": max(
                    row["fixture"].get("cpu_ns", 0) / max(row["fixture"].get("wall_ns", 1), 1)
                    for row in rows
                ),
                "fixture_lag_peak_ms": max(
                    row["fixture"].get("loop_lag_peak_ns", 0) for row in rows
                )
                / 1000000,
                "capabilities_passed": sum(
                    value == "passed" for value in capabilities[candidate].values()
                ),
                "capabilities_total": len(raw["manifest"]["capabilities"])
                if raw_profile["kind"] in {"transport", "flow"}
                else None,
                "controls_qualified": all(
                    capabilities[candidate].get(control) == "passed"
                    for control in required_controls
                )
                if raw_profile["kind"] in {"transport", "flow"} and required_controls
                else None,
            }
        )
    return {
        "schema_version": 1,
        "smoke": raw["smoke"],
        "counts": dict(counts),
        "groups": groups,
        "capabilities": dict(capabilities),
        "unsupported_reasons": reasons,
        "provenance": raw.get("provenance"),
    }


def public_raw(raw):
    # Synthetic fixture credentials are unnecessary in distributable reports.
    copied = json.loads(json.dumps(raw))
    for row in copied["jobs"]:
        for name, address in row["job"]["fixture"]["proxies"].items():
            parsed = urlsplit(address)
            row["job"]["fixture"]["proxies"][name] = urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc.split("@")[-1],
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
    return copied


def markdown(raw, summary, original):
    def number(value, places=3):
        return f"{value:.{places}f}" if value is not None else "no successes"

    counts = summary["counts"]
    provenance = raw.get("provenance")
    rerun_note = (
        f"Selective remeasurement: {len(provenance['replaced_jobs'])} affected jobs from `{provenance['replacement_path']}` replace their baseline observations. Reason: {provenance['reason'].rstrip('.')}. The selected combined observations, both runs' source hashes and replaced identities are preserved in raw.json.gz. Replaced original observations are not included in that archive; their recorded source is `{provenance['baseline_path']}`. Unaffected jobs retain their original observations."
        if provenance
        else "These observations come from one baseline run."
    )
    lines = [
        "# Local TLS/proxy transport matrix",
        "",
        f"Original evidence: `{original.parent}`. Reproduce with `make bench-matrix BENCH_OUTPUT=/tmp/anchorpass-matrix-new`, then `benchmarks/report_matrix.py`. Full per-job results are retained in `raw.json.gz`; synthetic proxy credentials are omitted from that distributable copy.",
        "",
        rerun_note,
        "",
        f"Recorded jobs: {counts['jobs']}; executed: {counts.get('executed_jobs', 0)}; unsupported: {counts.get('unsupported_jobs', 0)}; invalid harness jobs: {counts.get('invalid_jobs', 0)}. Measured observations: {counts.get('measured_observations', 0)}; failed: {counts.get('failed_observations', 0)}. Recovery observations: {counts.get('recovery_observations', 0)}; failed: {counts.get('failed_recovery_observations', 0)}.",
        "",
        "Each performance cell combines independently launched processes across the configured rounds. p50/p95/p99 use successful samples only; failures remain separately visible and disqualify capability controls. Quantiles are descriptive. Round median ranges expose variation; there is no confidence-interval or universal-winner claim. Peak RSS includes startup, warmup and cleanup and is externally sampled, not an exact high-water mark.",
        "",
        "## Capability outcomes",
        "",
        "`passed` means the expected negative result and successful recovery. `unsupported` is unverified in this exact adapter, not a pass. A raw candidate qualifies only when every required control passes and its selected workload has no failures. These are candidate-adapter qualifications, distinct from package conformance tests.",
        "",
        "| Candidate | "
        + " | ".join(item["name"] for item in raw["manifest"]["capabilities"])
        + " |",
        "| --- | " + " | ".join("---" for _ in raw["manifest"]["capabilities"]) + " |",
    ]
    for candidate, outcomes in sorted(summary["capabilities"].items()):
        lines.append(
            "| "
            + candidate
            + " | "
            + " | ".join(
                outcomes.get(item["name"], "missing") for item in raw["manifest"]["capabilities"]
            )
            + " |"
        )
    for profile in sorted({group["profile"] for group in summary["groups"]}):
        lines.extend(
            [
                "",
                f"## {profile}",
                "",
                "| Candidate | Samples/failures | p50 / p95 / p99 ms | Round p50 range ms | ops/s | CPU ms/op | Sampled peak MiB |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for group in summary["groups"]:
            if group["profile"] == profile:
                lines.append(
                    f"| {group['candidate']} | {group['samples']}/{group['failed']} | {number(group['p50_ms'])} / {number(group['p95_ms'])} / {number(group['p99_ms'])} | {number(group['round_p50_min_ms'])}–{number(group['round_p50_max_ms'])} | {number(group['throughput_rps'], 1)} | {number(group['cpu_ms_per_op'])} | {number(group['rss_peak_mib'], 2)} |"
                )
    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- All traffic is loopback and uses verified TLS. The fixture blocks external forwarding. No public service was load-tested.",
            "- Node clients share the qualified TLS/proxy connector. Fetch, Undici and Axios/fetch share Undici HTTP machinery; native HTTPS, Axios/http and Got use node:https. Resty shares net/http; fasthttp is a separate HTTP backend. These distinctions are part of the comparison.",
            "- Flow profiles measure version GET, anchor GET and reload POST as one operation, with one total deadline, per-operation cookies and the same fixture protocol fields. Delayed flow profiles apply the configured delay to every step. Raw requests, full flows and public package APIs are reported separately.",
            "- Raw adapters use their language's parsers; Rust uses a minimal fixture parser while package API profiles use the production parser. Small cross-language differences cannot be attributed solely to the HTTP library.",
            "- Sync transports use bounded workers; async uses native tasks. Node blocking concurrency one only. Runtime/framework costs and thread topology are part of those measured configurations.",
            "- Warm means a reused client after warmup; cold includes construction and cleanup. Update preparation is outside solve timing. Package API settings and cache strategies are documented in each adapter README.",
            "- Raw Rust compression features are isolated from the API binary. Unsupported raw-adapter TLS/proxy controls remain explicit capability gaps; package-level trust qualification is separate.",
            "- Python requests/HTTPX sync idle timeouts can fail the total deadline. HTTPX may retain a failed nested-TLS connection slot; the repeat-failure control records the actual result. Aiohttp's no-retry adapter uses a version-pinned internal switch.",
            "- Go operation timeout returns promptly; a detached net/http handshake can live until its bounded connect timeout or Client.Close. This is separately tested and documented.",
            "- Fixture CPU fraction and loop lag are included in summary.json. High fixture load or overlapping round medians prevent attributing small differences solely to a client.",
            "- Fixture CPU fraction covers the entire adapter lifetime, including startup, warmup and cleanup; its low average cannot rule out a fixture ceiling during the measured batch.",
            "- These finite batches do not prove long-term leak freedom, live acceptance, platform portability or update-server resilience. Package conformance supplies separate evidence.",
            "",
            "## Unsupported adapter explanations",
            "",
        ]
    )
    for key, reason in sorted(summary["unsupported_reasons"].items()):
        lines.append(f"- `{key}`: {reason}")
    lines.extend(
        [
            "",
            "## Environment",
            "",
            "```json",
            json.dumps(
                {key: value for key, value in raw["environment"].items() if key != "source_hashes"},
                indent=2,
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def chart(summary):
    data = json.dumps(summary, ensure_ascii=False).replace("</", "<\\/")
    return (
        """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{font-family:var(--font-sans,system-ui);color:var(--foreground);font-size:14px}body{margin:0}p{color:var(--muted-foreground);line-height:1.5}label{display:inline-flex;gap:8px;align-items:center;margin:0 12px 12px 0}select{background:var(--card);color:var(--foreground);border:1px solid var(--border);padding:7px;border-radius:var(--radius,6px);max-width:100%}.row{display:grid;grid-template-columns:minmax(145px,30%) 1fr 72px;align-items:center;gap:8px;margin:15px 0}.bar{height:16px;background:var(--chart-1,var(--primary,#536fd4));border-radius:3px;min-width:2px}.name{overflow-wrap:anywhere}.value{font-family:var(--font-mono,monospace);text-align:right}small{color:var(--muted-foreground)}table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 4px;border-bottom:1px solid var(--border);text-align:right}th:first-child,td:first-child{text-align:left}.scroll{overflow:auto}.badge{color:var(--warning)}#bars{height:180px;overflow:auto}.note{font-size:12px}h3{font-size:16px;margin:0 0 8px}
</style><h3>Локальные TLS-запросы: время, CPU и память</h3><p>Медиана успешных операций. Меньше — быстрее. Варианты с полным допуском показаны первыми; ⚠ означает проваленную, отсутствующую или неподдерживаемую обязательную проверку. Это не рейтинг принятия капчи.</p><label>Группа <select id="group"><option value="python-async">Python async</option><option value="python-sync">Python sync</option><option value="node">Node</option><option value="go">Go</option><option value="rust">Rust</option><option value="api">Публичный API</option></select></label><label>Профиль <select id="profile"></select></label><div id="bars" role="img" aria-label="Медианное время операции"></div><div class="scroll"><table><thead><tr><th>Кандидат</th><th>p95, мс</th><th>операций/с</th><th>CPU, мс/оп.</th><th>Пик MiB</th><th>Проверки</th><th>Ошибки</th></tr></thead><tbody id="rows"></tbody></table></div><p class="note">Пик памяти измерен снаружи процесса и включает запуск, прогрев и завершение. Диапазон медиан между раундами приведён в полном отчёте. Отсутствующая проверка не означает успех. Все запросы оставались на локальном стенде.</p><script type="application/json" id="data">"""
        + data
        + """</script><script>
const data=JSON.parse(document.getElementById('data').textContent),group=document.getElementById('group'),profile=document.getElementById('profile');
function matches(row){const g=group.value;if(g==='api')return row.kind==='api';if(row.kind==='api')return false;if(g==='python-async')return ['python-aiohttp','python-httpx-async','python-curl-async'].includes(row.candidate);if(g==='python-sync')return ['python-requests','python-httpx-sync','python-curl-sync','python-urllib3','python-pycurl'].includes(row.candidate);return row.candidate.startsWith(g+'-')}
function draw(){const values=data.groups.filter(row=>matches(row)&&row.profile===profile.value).sort((a,b)=>Number(b.controls_qualified===true)-Number(a.controls_qualified===true)||(a.p50_ms??Infinity)-(b.p50_ms??Infinity)),maximum=Math.max(0.001,...values.map(row=>row.p50_ms));document.getElementById('bars').replaceChildren();document.getElementById('rows').replaceChildren();for(const row of values){const outer=document.createElement('div');outer.className='row';const name=document.createElement('span');name.className='name';name.textContent=(row.controls_qualified===false?'⚠ ':'')+row.candidate;const track=document.createElement('div'),bar=document.createElement('div');bar.className='bar';bar.style.width=(row.p50_ms===null?0:100*row.p50_ms/maximum)+'%';track.append(bar);const value=document.createElement('span');value.className='value';value.textContent=(row.p50_ms===null?'нет успехов':row.p50_ms.toFixed(3)+' мс');outer.append(name,track,value);document.getElementById('bars').append(outer);const tr=document.createElement('tr');for(const value of [row.candidate,(row.p95_ms===null?'нет успехов':row.p95_ms.toFixed(3)),row.throughput_rps.toFixed(0),row.cpu_ms_per_op.toFixed(3),row.rss_peak_mib.toFixed(1),row.capabilities_total===null?'API-тесты':(row.controls_qualified===true?'✓ ':'⚠ ')+row.capabilities_passed+'/'+row.capabilities_total,row.failed+'/'+row.samples]){const td=document.createElement('td');td.textContent=value;tr.append(td)}document.getElementById('rows').append(tr)}}
function update(){const old=profile.value;profile.replaceChildren();for(const name of [...new Set(data.groups.filter(matches).map(row=>row.profile))].sort()){const option=document.createElement('option');option.value=name;option.textContent=name;profile.append(option)}if([...profile.options].some(option=>option.value===old))profile.value=old;else if(group.value==='api'&&[...profile.options].some(option=>option.value==='api-warm-c1'))profile.value='api-warm-c1';else if([...profile.options].some(option=>option.value==='flow-http-proxy-c8'))profile.value='flow-http-proxy-c8';draw()}group.onchange=update;profile.onchange=draw;update();
</script></html>"""
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text())
    if raw["smoke"]:
        raise SystemExit("smoke results cannot be published as a comparative matrix")
    if raw.get("sources_unchanged") is not True:
        raise SystemExit("source integrity was not confirmed; preserve evidence and rerun")
    summary = summarize(raw)
    if summary["counts"].get("invalid_jobs", 0) or len(raw["jobs"]) != raw["planned_jobs"]:
        raise SystemExit(
            "incomplete or invalid matrix; preserve evidence and fix the harness first"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output / "raw.json.gz").write_bytes(
        gzip.compress(json.dumps(public_raw(raw), separators=(",", ":")).encode(), mtime=0)
    )
    with (args.output / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(summary["groups"][0])
            if summary["groups"]
            else ["candidate", "profile", "samples", "failed"],
        )
        writer.writeheader()
        writer.writerows(summary["groups"])
    (args.output / "REPORT.md").write_text(markdown(raw, summary, args.raw.resolve()))
    (args.output / "chart.html").write_text(chart(summary))
    print(json.dumps(summary["counts"], indent=2))


if __name__ == "__main__":
    main()
