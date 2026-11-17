'use strict';
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { fixture } = require('../js/tests/helpers.cjs');

(async () => {
  const server = await fixture();
  try {
    await server.command('reset', { mapGoogle: true });
    const checkout = path.resolve(__dirname, '..');
    const consumer = path.join(checkout, 'tests/integration/python_consumer.py');
    const ca = path.join(checkout, 'js/tests/certs/cert.pem');
    const proxy = `http://user:p%40ss@127.0.0.1:${server.ports[2]}`;
    const checked = spawnSync(process.argv[2], ['-I', consumer, proxy, ca, checkout], {
      cwd: process.argv[3], encoding: 'utf8', timeout: 30000,
      maxBuffer: 1024 * 1024,
    });
    process.stdout.write(checked.stdout || '');
    process.stderr.write(checked.stderr || '');
    assert.equal(checked.status, 0, checked.error?.message);
    const state = await server.command('state');
    const forms = state.requests.filter(request => request.method === 'POST')
      .map(request => new URLSearchParams(request.body));
    assert.equal(forms.length, 6);
    assert.equal(forms[0].get('bg'), 'packed-bg');
    assert.equal(forms[0].get('sa'), 'packed-action');
    assert.notEqual(forms[1].get('bg'), 'packed-bg');
    assert.equal(forms[1].get('sa'), 'url-action');
    assert.equal(forms[3].get('bg'), 'packed-async');
    console.log('PASS: installed Python requests used authenticated CONNECT and verified target TLS');
  } finally {
    await server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
