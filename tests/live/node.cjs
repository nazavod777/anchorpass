// Explicit live-check child. Tokens are returned only to the supervising verifier.
const { Client, SyncClient } = require('../../js/dist/index.js');
const fs = require('node:fs');

(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const client = input.mode === 'node-sync'
    ? new SyncClient({ proxy: input.proxy, timeout: input.timeout_ms / 1000 })
    : new Client({ proxy: input.proxy, timeout: input.timeout_ms / 1000 });
  try {
    const result = input.mode === 'node-sync'
      ? client.solveSync(input.anchor, { action: input.action })
      : await client.solve(input.anchor, { action: input.action });
    process.stdout.write(JSON.stringify({ token: result.token, bundle_revision: result.bundle_revision }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: error.code || error.name || 'Unclassified', stage: error.stage }));
    process.exitCode = 1;
  } finally {
    if (client.closeAsync) await client.closeAsync(); else await client.close();
  }
})().catch(() => { process.stdout.write('{"error":"LiveHelperFailed"}'); process.exitCode = 1; });
