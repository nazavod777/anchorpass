'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseAnchor, validateUserAgent } = require('../dist/core.js');
const { Client } = require('../dist/client.js');
const { SyncClient, workerStates } = require('../dist/sync.js');
const { solve, solveSync } = require('..');
const { fixture, anchor } = require('./helpers.cjs');
const vectors = JSON.parse(fs.readFileSync(path.join(__dirname, '../../spec/vectors/solve-options.json'), 'utf8'));
const ca = fs.readFileSync(path.join(__dirname, 'certs/cert.pem'), 'utf8');

function anchorWithCo(co) {
  const url = new URL(anchor);
  url.searchParams.set('co', co);
  return url.href;
}
function observedOrigins(requests) {
  return requests.filter(request => request.method === 'POST' || request.url.includes('/anchor?')).map(request => {
    const fields = request.method === 'POST' ? new URLSearchParams(request.body) : new URL(request.url, 'https://fixture').searchParams;
    return [fields.get('sa'), fields.get('co')];
  });
}
test('shared solve-option vectors preserve scheme, port, encoding and default co', () => {
  for (const vector of vectors.hostname) {
    const url = anchorWithCo(vector.co);
    if (vector.valid) assert.equal(parseAnchor(url, undefined, true, vector.hostname).query.get('co'), vector.expected_co);
    else assert.throws(() => parseAnchor(url, undefined, true, vector.hostname), { code: 'InvalidInput' });
    assert.equal(parseAnchor(url).query.get('co'), vector.co);
  }
  for (const vector of vectors.user_agent) {
    if (vector.valid) validateUserAgent(vector.value);
    else assert.throws(() => validateUserAgent(vector.value), { code: 'InvalidInput' });
  }
  for (const hostname of [null, 1, {}, [], 'a'.repeat(64), `${'a'.repeat(63)}.${'a'.repeat(63)}.${'a'.repeat(63)}.${'a'.repeat(62)}.a`]) assert.throws(() => parseAnchor(anchorWithCo(vectors.hostname[0].co), undefined, true, hostname), { code: 'InvalidInput' });
});
test('invalid hostname or co fails before update, transport or synchronous worker startup', async () => {
  const server = await fixture();
  const options = { bundleUrl: `${server.origin}/bundle` };
  const client = new Client(options, { origin: server.origin });
  const sync = new SyncClient(options, { origin: server.origin });
  try {
    const invalid = [...vectors.hostname.filter(vector => !vector.valid), ...['fe80::1%eth0', '[fe80::1%eth0]'].map(hostname => ({ co: vectors.hostname[0].co, hostname }))];
    for (const vector of invalid) {
      const url = anchorWithCo(vector.co), hostname = vector.hostname;
      await assert.rejects(client.solve(url, { hostname }), { code: 'InvalidInput' });
      assert.throws(() => sync.solveSync(url, { hostname }), { code: 'InvalidInput' });
      await assert.rejects(solve(url, { ...options, hostname }), { code: 'InvalidInput' });
      assert.throws(() => solveSync(url, { ...options, hostname }), { code: 'InvalidInput' });
    }
    assert.deepEqual(workerStates(), []);
    assert.equal((await server.command('state')).requests.length, 0);
  } finally { await client.closeAsync(); await sync.closeAsync(); await server.close(); }
});
test('hostname stays per solve and matches anchor and reload for async, sync and helpers', async () => {
  const server = await fixture();
  await server.command('reset', { mapGoogle: true });
  const options = { proxy: `http://user:p%40ss@127.0.0.1:${server.ports[2]}`, ca };
  const client = new Client(options), sync = new SyncClient(options);
  const vector = vectors.hostname[0], url = anchorWithCo(vector.co);
  const secondCo = parseAnchor(url, undefined, true, 'second.example').query.get('co');
  try {
    await Promise.all([client.solve(url, { action: 'first', hostname: vector.hostname }), client.solve(url, { action: 'second', hostname: 'second.example' })]);
    await client.solve(url, { action: 'default' });
    sync.solveSync(url, { action: 'sync', hostname: vector.hostname });
    sync.solveSync(url, { action: 'sync-default' });
    await solve(url, { ...options, action: 'helper-async', hostname: vector.hostname });
    solveSync(url, { ...options, action: 'helper-sync', hostname: vector.hostname });
    const observed = observedOrigins((await server.command('state')).requests);
    for (const [action, co] of [['first', vector.expected_co], ['second', secondCo], ['default', vector.co], ['sync', vector.expected_co], ['sync-default', vector.co], ['helper-async', vector.expected_co], ['helper-sync', vector.expected_co]]) {
      assert.deepEqual(observed.filter(value => value[0] === action), [[action, co], [action, co]]);
    }
    assert.equal(new URL(url).searchParams.get('co'), vector.co);
  } finally { await client.closeAsync(); await sync.closeAsync(); await server.close(); }
});
