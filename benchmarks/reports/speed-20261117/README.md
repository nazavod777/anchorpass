# Transport selection evidence

This baseline compares 21 raw transport modes and eight package API modes.
All 1353 jobs completed, the runner and its original shell command exited zero,
source/dependency hashes stayed unchanged during measurement, and no harness
validation failed. See [REPORT.md](REPORT.md), [summary.json](summary.json),
[CSV](summary.csv), [chart](chart.html) and [raw observations](raw.json.gz).

The 151 failed observations are failed negative controls, not ordinary workload
failures. Every recorded performance group had zero failed samples. There were
36 explicitly unsupported jobs and no failed recovery observations. A failed,
missing or unsupported required control prevents qualification of that adapter.

`measured-sources.tar.gz` preserves all 113 unique source/dependency paths listed
in the raw environment hashes, with every file verified against its recorded
SHA-256. It is an overlay for the base checkout recorded in `source-manifest.json`,
not a standalone source distribution. It preserves the pre-migration Python
implementation; the shipped Python API is remeasured separately after migration.
The post-measurement Node correction rejects mixed Base64 padding only when an
explicit hostname override is supplied. No measured job used that branch.

Fixture CPU fraction averages the adapter's whole process lifetime, including
imports, setup, warmup and cleanup. A low average does not exclude fixture
saturation during timed samples. Client RSS also covers the whole process;
adapter CPU and latency cover the measured batch. Raw Rust flows use a small
fixture parser; package API flows use their production parsers. Small differences
between languages cannot be attributed solely to their HTTP libraries.

The preliminary aiohttp API adapter reduces reused-client latency but increases
sampled RSS. Its cold API median (construction + solve + close, after preparation)
is higher than the original curl API. These are explicit selection tradeoffs,
not evidence that every call is faster or that memory use falls. The final
transport decision and post-migration measurements are documented in
[transport decisions](../../../docs/transport-decisions.md).

All requests use a loopback TLS fixture. This evidence does not measure live
CAPTCHA scores, acceptance, Internet latency or all supported platforms.
