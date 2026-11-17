const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

for (const mode of ['node-async', 'node-sync']) {
  test(`live helper invokes the public ${mode} method without network`, () => {
    const result = spawnSync(process.execPath, [path.join(__dirname, 'node.cjs')], {
      input: JSON.stringify({ mode, anchor: 'invalid', action: 'test', timeout_ms: 1000 }),
      encoding: 'utf8', timeout: 3000, maxBuffer: 65536,
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 1);
    assert.equal(JSON.parse(result.stdout).error, 'InvalidInput');
  });
}
