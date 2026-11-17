# 📊 Python API after transport migration

This report measures the final PycURL sync and aiohttp async implementation.
All 18 jobs completed: three rounds of three profiles for both API modes,
2304 measured solves, zero failed samples and zero validation errors. Both the
runner and its original shell command exited zero; source/dependency hashes
remained unchanged during measurement.

See [REPORT.md](REPORT.md), [summary.json](summary.json), [CSV](summary.csv),
[raw observations](raw.json.gz), [comparison data](comparison.json) and the
[interactive before/after comparison](comparison.html). The baseline remains in
[its original directory](../speed-20261117/README.md); it is not overwritten.

`measured-sources.tar.gz` preserves all 114 unique source/dependency paths from
the raw environment, verified against every recorded SHA-256. It overlays the
base checkout named in `source-manifest.json`; it is not a standalone package.

Each performance cell has three rounds and 384 measured solves. Sync concurrency
uses a separate reused client in each bounded worker thread; async shares one
client. Setup and update preparation are excluded. Cold includes construction,
solve and close. Process RSS includes startup, warmup and cleanup; fixture CPU
fraction also averages the whole adapter lifetime, so its low value cannot
exclude saturation in the timed batch.

The measured reused-client advantage comes with increased sampled RSS and higher
cold API medians. Before and after are separate sequential runs, not an
interleaved A/B experiment. No public service was contacted. See the
[transport decision](../../../docs/transport-decisions.md) for qualification,
verification, tradeoffs and limits.
