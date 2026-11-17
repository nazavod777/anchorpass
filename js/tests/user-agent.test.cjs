'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Client } = require('../dist/client.js');
const { SyncClient, workerStates } = require('../dist/sync.js');
const { solve, solveSync } = require('..');
const { fixture, anchor, signing } = require('./helpers.cjs');
const ca = fs.readFileSync(path.join(__dirname, 'certs/cert.pem'), 'utf8');

test('per-solve userAgent is isolated across concurrent calls, redirects and updates', async () => {
  const server = await fixture();
  const signed = signing();
  await server.command('reset', { bundle: signed.bundle({ revision: '999999999999' }) });
  const client = new Client({ bundleUrl: `${server.origin}/bundle`, trustedKeys: signed.keys }, { origin: server.origin });
  try {
    await Promise.all(['Agent/first', 'Agent/second'].map(userAgent => client.solve(anchor, { userAgent })));
    await client.solve(anchor);
    await client.refresh();
    const requests = (await server.command('state')).requests;
    for (const userAgent of ['Agent/first', 'Agent/second']) assert.equal(requests.filter(request => request.headers['user-agent'] === userAgent).length, 3);
    assert.ok(requests.filter(request => request.url === '/bundle').every(request => request.headers['user-agent'] === undefined));
    assert.ok(requests.filter(request => request.url !== '/bundle').slice(-3).every(request => request.headers['user-agent'] === undefined));
    await server.command('reset', { mode: 'redirect' });
    await client.solve(anchor, { userAgent: 'Agent/redirect' });
    assert.ok((await server.command('state')).requests.every(request => request.headers['user-agent'] === 'Agent/redirect'));
    await server.command('reset');
    await client.solve(anchor, { userAgent: 'x'.repeat(1024) });
    assert.ok((await server.command('state')).requests.every(request => request.headers['user-agent'] === 'x'.repeat(1024)));
  } finally { await client.closeAsync(); await server.close(); }
});

test('invalid userAgent fails before update, transport or synchronous worker startup', async () => {
  const server = await fixture();
  const options = { bundleUrl: `${server.origin}/bundle` };
  const client = new Client(options, { origin: server.origin });
  const sync = new SyncClient(options, { origin: server.origin });
  try {
    for (const userAgent of ['', ' ', '\t', 'a\r\nb', 'a\0b', '\x1f', '\x7f', 'é', 'x'.repeat(1025), null, 1, {}, ['agent']]) {
      await assert.rejects(client.solve(anchor, { userAgent }), { code: 'InvalidInput' });
      assert.throws(() => sync.solveSync(anchor, { userAgent }), { code: 'InvalidInput' });
      await assert.rejects(solve(anchor, { ...options, userAgent }), { code: 'InvalidInput' });
      assert.throws(() => solveSync(anchor, { ...options, userAgent }), { code: 'InvalidInput' });
    }
    assert.deepEqual(workerStates(), []);
    assert.equal((await server.command('state')).requests.length, 0);
  } finally { await client.closeAsync(); await sync.closeAsync(); await server.close(); }
});

test('sync client and both top-level helpers forward userAgent without retaining it', async () => {
  const server = await fixture();
  await server.command('reset', { mapGoogle: true });
  const options = { proxy: `http://user:p%40ss@127.0.0.1:${server.ports[2]}`, ca };
  const sync = new SyncClient(options);
  try {
    assert.equal(sync.solveSync(anchor, { userAgent: 'Agent/sync' }).token, 'fixture-token');
    assert.equal(sync.solveSync(anchor).token, 'fixture-token');
    assert.equal((await solve(anchor, { ...options, userAgent: 'Agent/helper-async' })).token, 'fixture-token');
    assert.equal(solveSync(anchor, { ...options, userAgent: 'Agent/helper-sync' }).token, 'fixture-token');
    const requests = (await server.command('state')).requests;
    for (const [index, userAgent] of ['Agent/sync', undefined, 'Agent/helper-async', 'Agent/helper-sync'].entries()) {
      assert.ok(requests.slice(index * 3, index * 3 + 3).every(request => request.headers['user-agent'] === userAgent));
    }
  } finally { await sync.closeAsync(); await server.close(); }
});
