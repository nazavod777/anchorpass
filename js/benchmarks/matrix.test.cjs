'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const https = require('node:https');
const zlib = require('node:zlib');
const tls = require('node:tls');
const os = require('node:os');
const { main } = require('./matrix.cjs');
const { fixture, signing, anchor } = require('../tests/helpers.cjs');
const certificate = path.resolve(__dirname, '../tests/certs/cert.pem');

function job(candidate, metadata, patch = {}) {
  return { schema_version: 1, run_id: 'adapter-regression-smoke', candidate, fixture: metadata,
    profile: { kind: 'transport', route: 'direct', connection_mode: 'warm', concurrency: 2, requests: 2, warmup: 0, recovery_requests: 1, timeout_ms: 150, max_response_bytes: 65536, path: '/bytes?size=1024', expected_bytes: 1024, expected_error: null, ...patch } };
}
function passed(result) {
  assert.equal(result.status, 'ok');
  assert.ok(result.samples.every(sample => sample.ok), JSON.stringify(result.samples));
  assert.ok(result.recovery.every(sample => sample.ok), JSON.stringify(result.recovery));
}
test('transport frontends validate decoded bodies, deadlines, errors and recovery', async () => {
  const server = https.createServer({ cert: fs.readFileSync(certificate), key: fs.readFileSync(path.resolve(__dirname, '../tests/certs/key.pem')) }, (request, response) => {
    const url = new URL(request.url, 'https://localhost');
    if (url.pathname === '/reset') { request.socket.destroy(); return; }
    if (url.pathname === '/status') { response.writeHead(429); response.end(); return; }
    if (url.pathname === '/drip') {
      response.write('x');
      const timer = setInterval(() => response.write('x'), 20);
      response.once('close', () => clearInterval(timer));
      return;
    }
    if (url.pathname === '/gzip') { response.setHeader('content-encoding', 'gzip'); response.end(zlib.gzipSync(Buffer.alloc(262144, 120))); return; }
    if (url.pathname === '/wrong') { response.end('wrong'); return; }
    response.end(Buffer.alloc(Number(url.searchParams.get('size')), 120));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const metadata = { https_url: `https://127.0.0.1:${server.address().port}`, ca_file: certificate, proxy_ca_file: certificate, proxies: {} };
  try {
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'anchorpass-node-trust-'));
    const unrelatedRoot = path.join(scratch, 'unrelated-ca.pem');
    fs.writeFileSync(unrelatedRoot, tls.rootCertificates[0]);
    for (const candidate of ['node-fetch', 'node-undici', 'node-axios', 'node-https', 'node-axios-http', 'node-got']) {
      for (const patch of [{}, { connection_mode: 'cold' }, { path: '/gzip', expected_error: 'response_limit' }, { path: '/drip', expected_error: 'deadline' }, { path: '/status', expected_error: 'http_status' }, { path: '/reset', expected_error: 'transport' }]) passed(await main(job(candidate, metadata, patch)));
      const wrong = await main(job(candidate, metadata, { path: '/wrong', expected_error: 'transport' }));
      assert.ok(wrong.samples.every(sample => !sample.ok && sample.error === 'validation'));
      passed(await main(job(candidate, { ...metadata, ca_file: unrelatedRoot }, { expected_error: 'tls', recovery_requests: 0 })));
    }
    fs.unlinkSync(unrelatedRoot); fs.rmdirSync(scratch);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
test('real API clients prepare a fresh signed bundle and preserve warm/cold behavior', async () => {
  const server = await fixture();
  try {
    const signed = signing();
    await server.command('reset', { bundle: signed.bundle({ revision: '999999999999' }), mapGoogle: true });
    const metadata = { https_url: `https://127.0.0.1:${server.ports[1]}`, ca_file: certificate, proxy_ca_file: certificate, anchor_url: anchor, bundle_url: `https://127.0.0.1:${server.ports[1]}/bundle`, trusted_keys: signed.keys, proxies: { http: `http://user:p%40ss@127.0.0.1:${server.ports[2]}` } };
    for (const candidate of ['node-api-async', 'node-api-sync']) {
      for (const connection_mode of ['warm', 'cold']) passed(await main(job(candidate, metadata, { kind: 'api', route: 'http', connection_mode, concurrency: 1, requests: 2, warmup: 1, recovery_requests: 0, timeout_ms: 2000 })));
    }
    const unsupported = await main(job('node-api-sync', metadata, { kind: 'api', route: 'http', concurrency: 8 }));
    assert.equal(unsupported.status, 'unsupported');
  } finally { await server.close(); }
});
test('all HTTP stacks preserve the three-step flow over direct and proxy routes', async () => {
  const server = await fixture();
  const signed = signing();
  const metadata = { https_url: `https://127.0.0.1:${server.ports[1]}`, ca_file: certificate, proxy_ca_file: certificate, flow_fields: signed.sample.fields,
    proxies: { http: `http://user:p%40ss@127.0.0.1:${server.ports[2]}`, https: `https://user:p%40ss@127.0.0.1:${server.ports[3]}`, socks5: `socks5://user:p%40ss@127.0.0.1:${server.ports[4]}`, socks5h: `socks5h://user:p%40ss@127.0.0.1:${server.ports[4]}` } };
  try {
    for (const candidate of ['node-fetch', 'node-undici', 'node-axios', 'node-https', 'node-axios-http', 'node-got']) {
      for (const route of ['direct', 'http', 'https', 'socks5', 'socks5h']) {
        await server.command('reset');
        passed(await main(job(candidate, metadata, { kind: 'flow', route, timeout_ms: 2000, requests: 2, warmup: 1, recovery_requests: 0 })));
        const requests = (await server.command('state')).requests;
        assert.equal(requests.length, 9);
        for (const request of requests) {
          if (request.url === '/recaptcha/api.js') assert.equal(request.headers.cookie, undefined);
          else assert.equal(request.headers.cookie, 'operation=must-not-persist');
          if (request.method === 'POST') assert.equal(new URLSearchParams(request.body).get('bg'), signed.sample.fields.bg);
        }
      }
      passed(await main(job(candidate, metadata, { kind: 'flow', connection_mode: 'cold', timeout_ms: 2000, recovery_requests: 0 })));
    }
  } finally { await server.close(); }
});
