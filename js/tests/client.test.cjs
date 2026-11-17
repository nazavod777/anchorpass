'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Client } = require('../dist/client.js');
const { fixture, anchor } = require('./helpers.cjs');
const ca = fs.readFileSync(path.join(__dirname, 'certs/cert.pem'), 'utf8');

test('async protocol, action, cookies, connection reuse and close', async () => {
  const server = await fixture();
  const client = new Client({}, { origin: server.origin });
  try {
    assert.equal((await client.solve(anchor, {action:'explicit'})).token, 'fixture-token');
    assert.equal((await client.solve(anchor)).token, 'fixture-token');
    const state = await server.command('state');
    assert.equal(state.requests.length, 6); assert.ok(state.connections >= 1 && state.connections <= 2);
    assert.equal(new URL(state.requests[1].url, server.origin).searchParams.get('sa'), 'explicit');
    assert.equal(new URLSearchParams(state.requests[2].body).get('sa'), 'explicit');
    assert.equal(new URLSearchParams(state.requests[5].body).get('sa'), 'url-action');
    for (const [index, request] of state.requests.entries()) {
      assert.equal(request.headers.cookie, index % 3 === 0 ? undefined : 'operation=must-not-persist');
      assert.equal(request.headers['proxy-authorization'], undefined);
    }
    await client.closeAsync(); await client.closeAsync();
    await assert.rejects(client.solve(anchor), {code:'ClientClosed'});
  } finally { await client.closeAsync(); await server.close(); }
});
test('HTTP and HTTPS CONNECT, target TLS and exact credentials', async () => {
  const server = await fixture();
  try {
    for (const [scheme, port] of [['http',server.ports[2]], ['https',server.ports[3]]]) {
      await server.command('reset');
      const client = new Client({ proxy:`${scheme}://user:p%40ss@127.0.0.1:${port}`, proxyCa:ca, ca }, { origin:`https://127.0.0.1:${server.ports[1]}` });
      try { assert.equal((await client.solve(anchor)).token, 'fixture-token'); } finally { await client.closeAsync(); }
      const state = await server.command('state');
      assert.equal(state.requests.length, 3); assert.ok(state.tunnels.length >= 1 && state.tunnels.length <= 2);
      assert.equal(state.requests[0].headers['proxy-authorization'], undefined);
    }
    const client = new Client({ proxy:`http://user:wrong@127.0.0.1:${server.ports[2]}` }, { origin:server.origin });
    await server.command('reset');
    try { await assert.rejects(client.solve(anchor), {code:'ProxyAuthError'}); } finally { await client.closeAsync(); }
    assert.equal((await server.command('state')).requests.length, 0);
  } finally { await server.close(); }
});
test('SOCKS5 DNS policy and authentication', async t => {
  const lookup = t.mock.method(require('node:dns/promises'), 'lookup', async hostname => {
    if (hostname === 'node.invalid') return { address: '127.0.0.1', family: 4 };
    throw Object.assign(new Error('Fixture hostname not found'), { code: 'ENOTFOUND' });
  });
  const server = await fixture();
  try {
    for (const [scheme, host, kind] of [['socks5','node.invalid',1], ['socks5h','node.invalid',3]]) {
      lookup.mock.resetCalls();
      await server.command('reset');
      const client = new Client({ proxy:`${scheme}://user:p%40ss@127.0.0.1:${server.ports[4]}` }, { origin:`http://${host}:${server.ports[0]}` });
      try { assert.equal((await client.solve(anchor)).token, 'fixture-token'); } finally { await client.closeAsync(); }
      const state = await server.command('state');
      assert.ok(state.socks.length >= 1 && state.socks.length <= 2); assert.equal(state.socks[0].kind, kind);
      assert.equal(lookup.mock.callCount() > 0, scheme === 'socks5');
      for (const call of lookup.mock.calls) assert.equal(call.arguments[0], host);
      assert.match(state.requests[0].headers.host, new RegExp('^' + host.replace('.', '\\.')));
    }
    lookup.mock.resetCalls();
    await server.command('reset');
    const client = new Client({ proxy:`socks5://127.0.0.1:${server.ports[4]}` }, { origin:`http://missing.invalid:${server.ports[0]}` });
    try { await assert.rejects(client.solve(anchor), {code:'ProxyConnectError'}); } finally { await client.closeAsync(); }
    assert.ok(lookup.mock.callCount() > 0);
    for (const call of lookup.mock.calls) assert.equal(call.arguments[0], 'missing.invalid');
    const state = await server.command('state'); assert.equal(state.requests.length,0); assert.equal(state.socks.length,0);
  } finally { await server.close(); }
});
test('bad proxy has no direct fallback; environment ignored', async () => {
  const server = await fixture();
  const old = process.env.NO_PROXY; process.env.NO_PROXY = '*';
  try {
    const broken = new Client({ proxy:'http://127.0.0.1:1', timeout:1 }, { origin:server.origin });
    try { await assert.rejects(broken.solve(anchor), {code:'ProxyConnectError'}); } finally { await broken.closeAsync(); }
    assert.equal((await server.command('state')).requests.length,0);
    const direct = new Client({}, { origin:server.origin });
    process.env.HTTP_PROXY = 'http://127.0.0.1:1'; process.env.HTTPS_PROXY = 'http://127.0.0.1:1';
    try { assert.equal((await direct.solve(anchor)).token, 'fixture-token'); } finally { await direct.closeAsync(); delete process.env.HTTP_PROXY; delete process.env.HTTPS_PROXY; }
  } finally { if(old === undefined) delete process.env.NO_PROXY; else process.env.NO_PROXY=old; await server.close(); }
});
test('deadline, cancellation, load bound, response bound and redirects', async () => {
  const server = await fixture();
  try {
    for (const mode of ['hang','slow']) {
      await server.command('reset', {mode});
      const client = new Client({timeout:0.15}, {origin:server.origin}); const start = performance.now();
      try { await assert.rejects(client.solve(anchor), {code:'DeadlineExceeded'}); assert.ok(performance.now()-start < 1000); } finally { await client.closeAsync(); }
    }
    for (const mode of ['huge','compressed']) {
      await server.command('reset', {mode}); const client = new Client({}, {origin:server.origin});
      try { await assert.rejects(client.solve(anchor), {code:'ResourceLimit'}); } finally { await client.closeAsync(); }
    }
    await server.command('reset', {mode:'hang'});
    const client = new Client({maxInFlight:1}, {origin:server.origin});
    const controller = new AbortController(); const first = client.solve(anchor, {signal:controller.signal});
    await assert.rejects(client.solve(anchor), {code:'ResourceLimit'}); controller.abort();
    await assert.rejects(first, {code:'Cancelled'}); await client.closeAsync();
    await server.command('reset', {mode:'redirect'});
    const redirected = new Client({}, {origin:server.origin});
    try { assert.equal((await redirected.solve(anchor)).token, 'fixture-token'); } finally { await redirected.closeAsync(); }
    await server.command('reset', {mode:'redirect-out'});
    const rejected = new Client({}, {origin:server.origin});
    try { await assert.rejects(rejected.solve(anchor), {code:'HttpStatusError'}); } finally { await rejected.closeAsync(); }
    assert.equal((await server.command('state')).requests.length,1);
  } finally { await server.close(); }
});
test('cancelling one CONNECT handshake preserves another operation', async () => {
  const http = require('node:http'); const net = require('node:net'); const {once}=require('node:events');
  const server=await fixture(); const waiting=[]; const sockets=new Set(); let released=false;
  const tunnel=({down,head})=>{
    if (down.destroyed) return;
    const up=net.connect({host:'127.0.0.1',port:server.ports[0]});
    up.on('connect',()=>{down.write('HTTP/1.1 200 OK\r\n\r\n');if(head.length)up.write(head);down.pipe(up);up.pipe(down);});
    down.on('close',()=>up.destroy());down.on('error',()=>up.destroy());up.on('error',()=>down.destroy());
  };
  const proxy=http.createServer();
  proxy.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
  proxy.on('connect',(_,down,head)=>{if(released)tunnel({down,head});else {waiting.push({down,head});if(waiting.length===2)proxy.emit('paired');}});
  await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
  const client=new Client({proxy:`http://127.0.0.1:${proxy.address().port}`,timeout:2},{origin:server.origin});
  try {
    const ready=once(proxy,'paired',{signal:AbortSignal.timeout(2000)});const abort=new AbortController();
    const first=client.solve(anchor,{signal:abort.signal});const second=client.solve(anchor);
    const cancelled=assert.rejects(first,{code:'Cancelled'});
    await ready;abort.abort();released=true;for(const item of waiting)tunnel(item);
    await cancelled;assert.equal((await second).token,'fixture-token');
  } finally {await client.closeAsync();for(const socket of sockets)socket.destroy();await new Promise(resolve=>proxy.close(resolve));await server.close();}
});
test('IPv6 proxy route, SOCKS bad password and distinct TLS trust checks', async()=>{
  const server=await fixture();
  try {
    const ipv6=new Client({proxy:`http://user:p%40ss@[::1]:${server.ports[5]}`},{origin:server.origin});
    try{assert.equal((await ipv6.solve(anchor)).token,'fixture-token');}finally{await ipv6.closeAsync();}
    for(const options of [
      {proxy:`socks5h://user:wrong@127.0.0.1:${server.ports[4]}`},
      {proxy:`http://user:p%40ss@127.0.0.1:${server.ports[2]}`},
      {proxy:`https://user:p%40ss@127.0.0.1:${server.ports[3]}`,ca},
    ]) {
      await server.command('reset');const client=new Client(options,{origin:`https://127.0.0.1:${server.ports[1]}`});
      try {await assert.rejects(client.solve(anchor),error=>{
        assert.equal(error.code,options.proxy.startsWith('socks')?'ProxyAuthError':options.proxy.startsWith('http:')?'TransportError':'ProxyConnectError');
        if(!options.proxy.startsWith('socks')){
          assert.deepEqual(Object.keys(error.cause),['code']);assert.equal(typeof error.cause.code,'string');
          assert.doesNotMatch(require('node:util').inspect(error),/p@ss|127\.0\.0\.1|localhost/);
        }
        return true;
      });}finally{await client.closeAsync();}
      assert.equal((await server.command('state')).requests.length,0);
    }
    const inspected=new Client({proxy:'http://user:secret@localhost:80'});
    try{assert.doesNotMatch(require('node:util').inspect(inspected),/secret|user|localhost/);}finally{await inspected.closeAsync();}
  }finally{await server.close();}
});

test('decoded anchor HTML attribute is form-encoded exactly once for reload',async()=>{
  const server=await fixture();const client=new Client({},{origin:server.origin});
  try {
    await server.command('reset',{mode:'entities'});assert.equal((await client.solve(anchor)).token,'fixture-token');
    const reload=(await server.command('state')).requests.find(request=>request.method==='POST');
    assert.equal(new URLSearchParams(reload.body).get('c'),'anchor&token++');assert.match(reload.body,/c=anchor%26token%2B%2B/);
  }finally{await client.closeAsync();await server.close();}
});
test('reload uses the actual unquoted input token after comment and script decoys',async()=>{
  const server=await fixture();const client=new Client({},{origin:server.origin});
  try {
    await server.command('reset',{mode:'html-tokenizer'});
    assert.equal((await client.solve(anchor)).token,'fixture-token');
    const reload=(await server.command('state')).requests.find(request=>request.method==='POST');
    assert.equal(new URLSearchParams(reload.body).get('c'),'actual-token');
  }finally{await client.closeAsync();await server.close();}
});
