'use strict';

// Benchmark adapter only. HTTP stacks share Anchorpass's proxy/TLS connector.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const https = require('node:https');
const { Readable } = require('node:stream');
const zlib = require('node:zlib');
const { Agent, request } = require('undici');
const axios = require('axios');
const { Transport } = require('../dist/transport.js');
const { parseProxy, parseVersion, parseAnchorToken, parseReload } = require('../dist/core.js');
const { OperationCookies } = require('../dist/cookies.js');
const { Client, SyncClient } = require('..');

const TRANSPORTS = ['node-fetch', 'node-undici', 'node-axios', 'node-https', 'node-axios-http', 'node-got'];
const CANDIDATES = [...TRANSPORTS, 'node-api-async', 'node-api-sync'];
let got;
class ObservedError extends Error {
  constructor(category, message) { super(message); this.name = 'ObservedError'; this.category = category; }
}
function resources() {
  let fds = null, threads = null;
  try { fds = fs.readdirSync('/proc/self/fd').length; } catch { /* Non-Linux. */ }
  try { threads = Number(/^Threads:\s+(\d+)/m.exec(fs.readFileSync('/proc/self/status', 'utf8'))[1]); } catch { /* Non-Linux. */ }
  return { fds, threads };
}
function classify(error, signal) {
  if (signal?.aborted) return 'deadline';
  for (let current = error, depth = 0; current && depth < 8; current = current.cause, depth++) {
    if (current.category) return current.category;
    if (current.code === 'DeadlineExceeded') return 'deadline';
    if (['ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID', 'CERT_UNTRUSTED', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN'].includes(current.code)) return 'tls';
    if (['ProxyAuthError', 'ProxyConnectError'].includes(current.code)) {
      if (current.cause) continue;
      return 'proxy';
    }
    if (current.code === 'HttpStatusError') return 'http_status';
    if (current.code === 'ResourceLimit' && /response|byte limit/i.test(current.message)) return 'response_limit';
    if (current.code === 'TransportError' && current.cause) continue;
    if (['UND_ERR_SOCKET', 'ECONNRESET', 'ECONNREFUSED', 'EPIPE', 'ENOTFOUND', 'TransportError'].includes(current.code)) return 'transport';
  }
  return 'unclassified';
}
function detail(error, fixture) {
  let value = `${error.name || 'Error'}: ${error.message || String(error)}`;
  for (const proxy of Object.values(fixture.proxies || {})) {
    value = value.split(proxy).join('[proxy]');
    const parsed = new URL(proxy);
    for (const credential of [parsed.password, decodeURIComponent(parsed.password)]) {
      if (credential) value = value.split(credential).join('[credential]');
    }
  }
  return value.replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/g, '$1[credentials]@').slice(0, 512);
}
async function consume(source, limit, expected) {
  let bytes = 0;
  const chunks = expected === undefined ? [] : null;
  try {
    for await (const chunk of source) {
      const data = Buffer.from(chunk);
      bytes += data.length;
      if (bytes > limit) throw new ObservedError('response_limit', 'Decoded response exceeds byte limit');
      if (chunks) chunks.push(data);
      else if (data.some(byte => byte !== 120)) throw new ObservedError('validation', 'Response contains a non-x byte');
    }
    if (chunks) return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, bytes));
    if (bytes !== expected) throw new ObservedError('validation', `Unexpected body length ${bytes}`);
    return bytes;
  } finally { source.destroy(); }
}
function transportClient(job, route) {
  const { profile: p, fixture: f, candidate } = job;
  const proxy = route === 'direct' ? null : f.proxies[route === 'bad_target' ? 'https' : route];
  const backend = new Transport({ proxy: parseProxy(proxy), connections: p.concurrency, connectTimeout: p.timeout_ms, ca: fs.readFileSync(f.ca_file, 'utf8'), proxyCa: fs.readFileSync(f.proxy_ca_file, 'utf8') });
  const native = ['node-https', 'node-axios-http', 'node-got'].includes(candidate);
  const agent = native ? new https.Agent({ keepAlive: true, maxSockets: p.concurrency, maxTotalSockets: p.concurrency, maxFreeSockets: p.concurrency }) : new Agent({ connections: p.concurrency, pipelining: 1, maxHeaderSize: 16384, headersTimeout: p.timeout_ms, bodyTimeout: p.timeout_ms,
    // A pooled dispatcher's reconnect callback may inherit an earlier request's
    // async context. Do not bind its connection to that request's AbortSignal.
    // The connector has its own bounded handshake timeout; dispatch abort still
    // cancels the current request and close destroys all pending handshakes.
    connect: (target, callback) => { backend.connect(target).then(socket => callback(null, socket), error => callback(error, null)); } });
  if (native) {
    // node:https.Agent's documented createConnection hook accepts a ready TLS
    // socket, preserving the exact CONNECT/SOCKS and independent trust policy.
    agent.createConnection = (options, callback) => {
      backend.connect({ hostname: options.hostname || options.host, port: String(options.port || 443), protocol: 'https:' }).then(socket => callback(null, socket), error => callback(error));
    };
  }
  async function read(requestPath, controller, expected, method = 'GET', body, cookies) {
    const url = new URL(requestPath, route === 'bad_target' ? f.bad_target_url : f.https_url).href;
    const cookie = cookies?.header(url, 'flow');
    const headers = { 'accept-encoding': 'gzip, deflate, br', ...(cookie === undefined ? {} : { cookie }), ...(body === undefined ? {} : { 'content-type': 'application/x-www-form-urlencoded' }) };
    let raw, decoder;
    try {
      let status, responseHeaders;
      if (candidate === 'node-undici') {
        const response = await request(url, { dispatcher: agent, method, body, signal: controller.signal, maxRedirections: 0, headers });
        raw = response.body; status = response.statusCode; responseHeaders = response.headers;
      } else if (candidate === 'node-fetch' || candidate === 'node-axios') {
        const response = candidate === 'node-fetch'
          ? await fetch(url, { dispatcher: agent, method, body, headers, signal: controller.signal, redirect: 'manual' })
          : await axios.request({ url, method, data: body, headers, adapter: 'fetch', proxy: false, signal: controller.signal, maxRedirects: 0, responseType: 'stream', validateStatus: () => true, fetchOptions: { dispatcher: agent, redirect: 'manual' } });
        status = response.status;
        responseHeaders = candidate === 'node-fetch' ? { 'set-cookie': response.headers.getSetCookie() } : response.headers;
        raw = Readable.fromWeb(candidate === 'node-fetch' ? response.body : response.data);
      } else if (candidate === 'node-https') {
        const response = await new Promise((resolve, reject) => {
          const outgoing = https.request(url, { agent, method, headers, signal: controller.signal, maxHeaderSize: 16384 }, resolve);
          outgoing.once('error', reject);
          outgoing.end(body);
        });
        raw = response; status = response.statusCode; responseHeaders = response.headers;
      } else if (candidate === 'node-axios-http') {
        const response = await axios.request({ url, method, data: body, headers, adapter: 'http', httpsAgent: agent, proxy: false, signal: controller.signal, maxRedirects: 0, responseType: 'stream', decompress: false, validateStatus: () => true, maxHeaderSize: 16384 });
        raw = response.data; status = response.status; responseHeaders = response.headers;
      } else {
        raw = got.stream(url, { agent: { https: agent }, method, body, headers, signal: controller.signal, retry: { limit: 0 }, followRedirect: false, throwHttpErrors: false, decompress: false, http2: false, maxHeaderSize: 16384 });
        const response = await new Promise((resolve, reject) => {
          const cleanup = () => { raw.off('response', received); raw.off('error', failed); };
          const received = value => { cleanup(); resolve(value); };
          const failed = error => { cleanup(); reject(error); };
          raw.once('response', received); raw.once('error', failed);
        });
        status = response.statusCode; responseHeaders = response.headers;
      }
      raw.on('error', () => undefined);
      cookies?.receive(responseHeaders['set-cookie'], url, 'flow');
      if (status < 200 || status >= 300) throw new ObservedError('http_status', `HTTP response status ${status}`);
      const encoding = candidate === 'node-fetch' || candidate === 'node-axios' ? undefined : responseHeaders['content-encoding'];
      if (encoding === 'gzip') decoder = zlib.createGunzip();
      else if (encoding === 'deflate') decoder = zlib.createInflate();
      else if (encoding === 'br') decoder = zlib.createBrotliDecompress();
      else if (encoding && encoding !== 'identity') throw new ObservedError('validation', 'Unknown content encoding');
      if (decoder) raw.on('error', error => decoder.destroy(error));
      return await consume(decoder ? raw.pipe(decoder) : raw, p.max_response_bytes, expected);
    } finally { decoder?.destroy(); raw?.destroy(); }
  }
  return {
    async run(requestPath, expected) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(new ObservedError('deadline', 'Total request deadline exceeded')), p.timeout_ms);
      try {
        if (p.kind !== 'flow') return await read(requestPath, controller, expected);
        const cookies = new OperationCookies();
        const flowPath = (pathname, values = {}) => {
          const query = new URLSearchParams(values);
          if (p.flow_delay_ms) query.set('delay_ms', String(p.flow_delay_ms));
          return `${pathname}${query.size ? `?${query}` : ''}`;
        };
        const version = parseVersion(await read(flowPath('/recaptcha/api.js'), controller, undefined, 'GET', undefined, cookies));
        const token = parseAnchorToken(await read(flowPath('/recaptcha/api2/anchor', { k: 'test-key', co: 'dGVzdA', v: version, sa: 'matrix-action' }), controller, undefined, 'GET', undefined, cookies));
        const form = new URLSearchParams({ ...f.flow_fields, v: version, c: token, k: 'test-key', co: 'dGVzdA', sa: 'matrix-action' });
        const solved = parseReload(await read(flowPath('/recaptcha/api2/reload', { k: 'test-key' }), controller, undefined, 'POST', form.toString(), cookies));
        if (solved !== 'fixture-token') throw new ObservedError('validation', 'Unexpected flow token');
        return Buffer.byteLength(solved);
      } catch (error) {
        if (controller.signal.aborted) throw controller.signal.reason;
        throw error;
      } finally { clearTimeout(timer); }
    },
    async close() { await agent.destroy(); await backend.close(); },
  };
}
async function apiFactory(job) {
  const { fixture: f, profile: p } = job;
  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'anchorpass-node-matrix-'));
  const options = { proxy: f.proxies.http, updatesProxy: null, ca: fs.readFileSync(f.ca_file, 'utf8'), proxyCa: fs.readFileSync(f.proxy_ca_file, 'utf8'), timeout: p.timeout_ms / 1000, connectTimeout: p.timeout_ms / 1000, updateTimeout: p.timeout_ms / 1000, maxInFlight: p.concurrency, maxResponseBytes: p.max_response_bytes, trustedKeys: f.trusted_keys, bundleUrl: f.bundle_url, cachePath: path.join(scratch, 'bundle.json') };
  const prepare = new Client(options);
  const refreshStarted = process.hrtime.bigint();
  let refreshWall;
  try { await prepare.refresh(); }
  catch (error) { fs.rmSync(scratch, { recursive: true, force: true }); throw error; }
  finally { await prepare.closeAsync(); }
  refreshWall = Number(process.hrtime.bigint() - refreshStarted);
  return {
    refresh_wall_ns: refreshWall,
    async create(warm) {
      const synchronous = job.candidate === 'node-api-sync';
      const client = new (synchronous ? SyncClient : Client)({ ...options, offline: true });
      if (warm) {
        try { if (synchronous) client.loadBgSync(); else await client.prepare(); }
        catch (error) { await client.closeAsync(); throw error; }
      }
      return { async run() {
        const result = synchronous ? client.solveSync(f.anchor_url) : await client.solve(f.anchor_url);
        if (result.token !== 'fixture-token') throw new ObservedError('validation', 'Unexpected API token');
        return Buffer.byteLength(result.token);
      }, close: () => client.closeAsync() };
    },
    cleanup() { fs.rmSync(scratch, { recursive: true, force: true }); },
  };
}
async function pool(count, concurrency, operation) {
  const samples = new Array(count);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(count, concurrency) }, async () => {
    while (next < count) { const index = next++; samples[index] = await operation(); }
  }));
  return samples;
}
function validate(job) {
  if (job.schema_version !== 1 || !CANDIDATES.includes(job.candidate)) throw new Error('Invalid job schema or candidate');
  if (!['transport', 'api', 'flow'].includes(job.profile.kind)) throw new Error('Invalid profile kind');
  if (!['warm', 'cold'].includes(job.profile.connection_mode)) throw new Error('Invalid connection mode');
  if (!['direct', 'http', 'https', 'socks5', 'socks5h', 'bad_auth', 'bad_tls', 'bad_target'].includes(job.profile.route)) throw new Error('Invalid route');
  for (const key of ['concurrency', 'requests', 'timeout_ms', 'max_response_bytes']) {
    if (!Number.isSafeInteger(job.profile[key]) || job.profile[key] <= 0) throw new Error(`Invalid profile ${key}`);
  }
  for (const key of ['warmup', 'recovery_requests']) {
    if (!Number.isSafeInteger(job.profile[key]) || job.profile[key] < 0) throw new Error(`Invalid profile ${key}`);
  }
  if (job.profile.concurrency > 1024) throw new Error('Concurrency exceeds adapter limit');
  if (job.profile.kind === 'flow' && (!job.fixture.flow_fields || typeof job.fixture.flow_fields !== 'object' || Array.isArray(job.fixture.flow_fields))) throw new Error('Flow fixture fields are required');
  if (job.profile.flow_delay_ms !== undefined && (!Number.isSafeInteger(job.profile.flow_delay_ms) || job.profile.flow_delay_ms < 0 || job.profile.flow_delay_ms > 60000)) throw new Error('Invalid flow delay');
}
async function main(job) {
  validate(job);
  const { profile: p, candidate } = job;
  if (candidate === 'node-got') got = (await import('got')).default;
  const api = candidate.startsWith('node-api-');
  const result = { schema_version: 1, run_id: job.run_id, candidate, status: 'ok', runtime: process.version, versions: { undici: require('undici/package.json').version, axios: require('axios/package.json').version, got: JSON.parse(fs.readFileSync(path.join(path.dirname(require.resolve('got')), '../../package.json'), 'utf8')).version, anchorpass: require('../package.json').version, bundled_undici: process.versions.undici }, mode: candidate === 'node-api-sync' ? 'blocking' : 'native-async', notes: [], samples: [], recovery: [], warmup_completed: 0 };
  let reason;
  if (api !== (p.kind === 'api')) reason = 'Candidate kind does not match profile kind';
  else if (candidate === 'node-api-sync' && p.concurrency !== 1) reason = 'SyncClient is blocking; this adapter supports concurrency 1 only, without adding an outer worker implementation';
  else if (api && p.route !== 'http') reason = 'API adapter is configured for the shared HTTP CONNECT API workload only';
  if (reason) return { ...result, status: 'unsupported', reason };
  if (!api) result.notes.push('Shared connector: internal Anchorpass Transport.connect for CONNECT/SOCKS and separate target/proxy trust roots; this compares HTTP stacks, not independent proxy libraries.');
  if (['node-https', 'node-axios-http', 'node-got'].includes(candidate)) result.notes.push('Uses Node native HTTP/1.1 stack and bounded node:https.Agent through its documented createConnection hook; no Undici dispatcher handles requests.');
  else if (!api) result.notes.push('Uses installed Undici Agent for HTTP/1.1 dispatch and connection pooling.');
  if (candidate === 'node-fetch' || candidate === 'node-axios') result.notes.push('Uses Node global fetch, backed by Node bundled Undici; node-fetch is a candidate label, not the npm node-fetch package.');
  if (candidate === 'node-axios') result.notes.push('Axios fetch adapter; fetchOptions.dispatcher selects the common installed Undici Agent.');
  if (candidate === 'node-axios-http') result.notes.push('Axios native http adapter; explicit httpsAgent, proxy:false, maxRedirects:0 and manual bounded decompression.');
  if (candidate === 'node-got') result.notes.push('Got stream API; retry.limit:0, followRedirect:false, throwHttpErrors:false, http2:false and manual bounded decompression.');
  if (p.kind === 'flow') result.notes.push('Three sequential version/anchor/reload requests share one total deadline, validate the fixture token and use a fresh bounded per-operation cookie jar; client pools are reused only in warm mode.');
  if (api) result.notes.push('Fresh fixture signed bundle refreshed into a private cache before timing; offline clients validate/read that cache. Public constructors only; fixture HTTP CONNECT maps www.google.com:443 to loopback.');
  const before = resources();
  let factory, client;
  try {
    factory = api ? await apiFactory(job) : { create: async () => transportClient(job, p.route), cleanup() {} };
    if (api) result.setup = { refresh_and_close_wall_ns: factory.refresh_wall_ns };
    if (p.connection_mode === 'warm') client = await factory.create(true);
    async function sample(recovery = false) {
      const started = process.hrtime.bigint();
      let owned, bytes = 0, error = null, message;
      try {
        owned = client || await factory.create(false);
        bytes = await owned.run(recovery ? '/bytes?size=1024' : p.path, recovery ? 1024 : p.expected_bytes);
      } catch (failure) { error = classify(failure); message = detail(failure, job.fixture); }
      finally { if (!client && owned) await owned.close(); }
      const elapsed = Number(process.hrtime.bigint() - started);
      const expected = recovery ? null : p.expected_error;
      const ok = error === (expected || null) && !(error === 'deadline' && elapsed > (p.timeout_ms + 100) * 1e6);
      return { elapsed_ns: elapsed, ok, error, bytes, ...(message ? { detail: message } : {}) };
    }
    const warmup = await pool(p.warmup, p.concurrency, () => sample());
    if (warmup.some(value => !value.ok)) throw new Error(`Warmup failed: ${JSON.stringify(warmup.find(value => !value.ok))}`);
    result.warmup_completed = warmup.length;
    const cpu = process.cpuUsage(), started = process.hrtime.bigint();
    result.samples = await pool(p.requests, p.concurrency, () => sample());
    result.wall_ns = Number(process.hrtime.bigint() - started);
    const used = process.cpuUsage(cpu);
    result.cpu_ns = (used.user + used.system) * 1000;
    if (p.recovery_requests) {
      // Invalid proxy credentials and trust faults need a normal-route client.
      if (['bad_auth', 'bad_tls', 'bad_target'].includes(p.route)) {
        if (client) await client.close();
        client = transportClient(job, p.route === 'bad_target' ? 'direct' : 'http');
      }
      result.recovery = await pool(p.recovery_requests, p.concurrency, () => sample(true));
    }
  } finally { if (client) await client.close(); factory?.cleanup(); }
  const after = resources();
  result.resources = { fds_before: before.fds, fds_after: after.fds, threads_before: before.threads, threads_after: after.threads };
  return result;
}
if (require.main === module) {
  (async () => {
    const args = process.argv.slice(2);
    if (args.length !== 4 || args[0] !== '--job' || args[2] !== '--output') throw new Error('Usage: node matrix.cjs --job PATH --output PATH');
    const result = await main(JSON.parse(fs.readFileSync(args[1], 'utf8')));
    fs.writeFileSync(args[3], `${JSON.stringify(result, null, 2)}\n`);
  })().catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 1; });
}
module.exports = { main };
