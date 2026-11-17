'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { OperationCookies } = require('../dist/cookies.js');
const { Client } = require('../dist/client.js');
const { SyncClient } = require('../dist/sync.js');
const { fixture, anchor } = require('./helpers.cjs');
const cookieUrl = 'https://www.google.com/recaptcha/api2/anchor';
const reloadUrl = 'https://www.google.com/recaptcha/api2/reload';

test('cookie domain, path, Secure, expiry and default-path rules',()=>{
  const cookies = new OperationCookies();
  cookies.receive([
    'host=allowed; Path=/recaptcha/', 'domain=allowed; Domain=.google.com; Path=/recaptcha/',
    'default=allowed', 'fallback=allowed; Path=invalid', 'outside=hidden; Path=/other/',
    'boundary=hidden; Path=/recaptcha/api', 'unrelated=hidden; Domain=unrelated.invalid; Path=/',
    'tls=allowed; Secure; Path=/recaptcha/', 'expired=hidden; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/',
    'zero=hidden; Max-Age=0; Expires=Thu, 01 Jan 2099 00:00:00 GMT; Path=/',
    'negative=hidden; Max-Age=-1; Path=/',
    'fresh=allowed; Max-Age=60; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/',
  ],cookieUrl,'anchor');
  assert.deepEqual(new Set(cookies.header(reloadUrl,'reload').split('; ')),new Set([
    'host=allowed','domain=allowed','default=allowed','fallback=allowed','tls=allowed','fresh=allowed',
  ]));
  assert.doesNotMatch(cookies.header(reloadUrl.replace('https:','http:'),'reload'),/tls=/);
  assert.equal(cookies.header('https://sub.www.google.com/recaptcha/api2/reload','reload'),'domain=allowed');
  assert.equal(cookies.header('https://unrelated.invalid/recaptcha/api2/reload','reload'),undefined);
  assert.doesNotMatch(cookies.header('https://www.google.com/recaptcha/api.js','version'),/default=|fallback=/);
});
test('cookie replacement, deletion and elapsed expiry',t=>{
  let now = Date.now(); t.mock.method(Date,'now',()=>now);
  const cookies = new OperationCookies();
  cookies.receive(['id=wide; Path=/','id=narrow; Path=/recaptcha/'],cookieUrl,'anchor');
  assert.equal(cookies.header(reloadUrl,'reload'),'id=narrow; id=wide');
  cookies.receive(['id=removed; Path=/recaptcha/; Max-Age=0'],cookieUrl,'anchor');
  assert.equal(cookies.header(reloadUrl,'reload'),'id=wide');
  cookies.receive(['id=short; Path=/; Max-Age=1'],cookieUrl,'anchor');
  assert.equal(cookies.header(reloadUrl,'reload'),'id=short');
  now += 2000; assert.equal(cookies.header(reloadUrl,'reload'),undefined);
});
test('processed cookie count and bytes are bounded cumulatively across responses',()=>{
  let cookies = new OperationCookies();
  cookies.receive(Array(64).fill('one=value'),cookieUrl,'version');
  assert.throws(()=>cookies.receive(['one=replaced'],cookieUrl,'anchor'),{code:'ResourceLimit'});
  cookies = new OperationCookies();
  cookies.receive(['a='+'x'.repeat(16384 - Buffer.byteLength('a='))],cookieUrl,'version');
  assert.throws(()=>cookies.receive(['b=value'],cookieUrl,'anchor'),{code:'ResourceLimit'});
  cookies = new OperationCookies();
  cookies.receive(Array.from({length:4},(_,index)=>`c${index}=`+'x'.repeat(4096-Buffer.byteLength(`c${index}=`))),cookieUrl,'version');
  assert.throws(()=>cookies.header(reloadUrl,'reload'),{code:'ResourceLimit'});
});
test('cookie-required concurrent solves and later operations never share cookies with updates',async()=>{
  const server = await fixture();
  const client = new Client({bundleUrl:server.origin+'/bundle'},{origin:server.origin});
  try {
    await server.command('reset',{mode:'cookie-flow'});
    const actions = Array.from({length:4},(_,index)=>`operation-${index}`);
    const results = await Promise.all(actions.map(action=>client.solve(anchor,{action})));
    assert.ok(results.every(result=>result.token==='fixture-token'));
    assert.equal((await client.solve(anchor,{action:'next'})).token,'fixture-token');
    await client.refresh();
    const state = await server.command('state');
    for (const request of state.requests.filter(request=>request.method==='GET')) assert.equal(request.headers.cookie,undefined);
    assert.ok(state.connections < state.requests.length);
  } finally { await client.closeAsync(); await server.close(); }
});
test('sync solve preserves its cookies and clears them before the next solve',async()=>{
  const server = await fixture();
  const client = new SyncClient({},{origin:server.origin});
  try {
    await server.command('reset',{mode:'cookie-flow'});
    assert.equal(client.solveSync(anchor,{action:'first'}).token,'fixture-token');
    assert.equal(client.solveSync(anchor,{action:'second'}).token,'fixture-token');
    const state = await server.command('state');
    assert.equal(state.requests[0].headers.cookie,undefined);
    assert.equal(state.requests[3].headers.cookie,undefined);
    assert.equal(state.requests[2].headers.cookie,'operation=first');
    assert.equal(state.requests[5].headers.cookie,'operation=second');
  } finally { await client.closeAsync(); await server.close(); }
});
test('allowed GET redirects preserve cookies; count and byte bounds stop the solve',async()=>{
  const server = await fixture();
  try {
    for (const mode of ['cookie-redirect','cookie-limit','cookie-bytes']) {
      await server.command('reset',{mode});
      const client = new Client({},{origin:server.origin});
      try {
        if (mode==='cookie-redirect') assert.equal((await client.solve(anchor)).token,'fixture-token');
        else await assert.rejects(client.solve(anchor),{code:'ResourceLimit'});
      } finally { await client.closeAsync(); }
      if (mode!=='cookie-redirect') assert.equal((await server.command('state')).requests.length,2);
    }
  } finally { await server.close(); }
});
