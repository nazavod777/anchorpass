'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { Client } = require('../dist/client.js');
const { fixture, anchor, signing } = require('./helpers.cjs');

test('refresh persists an exact signed envelope and rejects rollback across clients', async () => {
  const server = await fixture(); const signer = signing();
  await fs.mkdir(path.join(os.tmpdir(),'anchorpass-node-impl'),{recursive:true});
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'anchorpass-node-impl/cache-'));
  const cachePath = path.join(scratch, 'bundle.json');
  const options = {bundleUrl:server.origin+'/bundle', trustedKeys:signer.keys, cachePath, refreshInterval:0};
  const client = new Client(options, {origin:server.origin});
  try {
    const second = signer.bundle(); await server.command('reset', {bundle:second});
    assert.equal((await client.solve(anchor)).bundle_revision, '2');
    assert.equal(await fs.readFile(cachePath,'utf8'),second);
    await server.command('reset', {bundle:signer.bundle({revision:'1'})});
    await assert.rejects(client.refresh(), {code:'ConfigRollback'});
    assert.equal((await client.solve(anchor)).bundle_revision, '2');
    const offline = new Client({...options,offline:true}, {origin:server.origin});
    try { assert.equal((await offline.solve(anchor)).bundle_revision,'2'); } finally { await offline.closeAsync(); }
    const pinned = new Client({...options,pin:'1',offline:true}, {origin:server.origin});
    try { await assert.rejects(pinned.prepare(), {code:'ConfigRollback'}); } finally { await pinned.closeAsync(); }
    assert.equal((await fs.readdir(scratch)).length,1);
  } finally { await client.closeAsync(); await server.close(); await fs.rm(scratch,{recursive:true,force:true}); }
});
test('strict expiry, signature failures, pin, corrupted cache and bounded lock', async () => {
  const server = await fixture(); const signer = signing();
  await fs.mkdir(path.join(os.tmpdir(),'anchorpass-node-impl'),{recursive:true});
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'anchorpass-node-impl/expiry-'));
  const cachePath = path.join(scratch, 'bundle.json');
  try {
    await fs.writeFile(cachePath, signer.bundle({issued_at:1,expires_at:2}));
    let client = new Client({cachePath,offline:true,trustedKeys:signer.keys}, {origin:server.origin});
    try { await assert.rejects(client.solve(anchor), {code:'ConfigExpired'}); } finally { await client.closeAsync(); }
    await fs.writeFile(cachePath,'{}');
    client = new Client({cachePath}, {origin:server.origin});
    try { await assert.rejects(client.prepare(), {code:'ConfigInvalid'}); } finally { await client.closeAsync(); }
    await fs.unlink(cachePath);
    await server.command('reset',{bundle:signer.bundle({revision:'3'})});
    client = new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,pin:'2'}, {origin:server.origin});
    try { await assert.rejects(client.prepare(), {code:'ConfigUnavailable'}); } finally { await client.closeAsync(); }
    await server.command('reset',{bundle:'{}'});
    client = new Client({bundleUrl:server.origin+'/bundle'}, {origin:server.origin});
    try { assert.equal((await client.solve(anchor)).bundle_revision,'1'); await assert.rejects(client.refresh(),{code:'ConfigInvalid'}); } finally { await client.closeAsync(); }
    await server.command('reset',{bundle:signer.bundle()}); await fs.writeFile(cachePath+'.lock','locked');
    client = new Client({bundleUrl:server.origin+'/bundle',cachePath,trustedKeys:signer.keys,updateTimeout:0.1}, {origin:server.origin});
    try { await assert.rejects(client.refresh(),{code:'DeadlineExceeded'}); } finally { await client.closeAsync(); }
    assert.equal((await server.command('state')).requests.filter(r=>r.url.startsWith('/recaptcha')).length,0);
  } finally { await server.close(); await fs.rm(scratch,{recursive:true,force:true}); }
});
test('update routing inherit, direct and explicit; no captcha cookies or fields', async () => {
  const server = await fixture();
  try {
    const proxy = `http://user:p%40ss@127.0.0.1:${server.ports[2]}`;
    for (const [updates, expected] of [[{},1], [{updatesProxy:null},0], [{updatesProxy:proxy},1]]) {
      await server.command('reset');
      const client = new Client({proxy,bundleUrl:server.origin+'/bundle',...updates},{origin:server.origin});
      try { await client.prepare(); } finally { await client.closeAsync(); }
      const state = await server.command('state'); assert.equal(state.tunnels.length,expected); assert.equal(state.requests.length,1);
      assert.equal(state.requests[0].url,'/bundle'); assert.equal(state.requests[0].headers.cookie,undefined);
      assert.equal(state.requests[0].headers['proxy-authorization'],undefined);
    }
    await server.command('reset');
    const client = new Client({proxy:'http://127.0.0.1:1',bundleUrl:server.origin+'/bundle'}, {origin:server.origin});
    try { await assert.rejects(client.refresh(),{code:'ProxyConnectError'}); } finally { await client.closeAsync(); }
    assert.equal((await server.command('state')).requests.length,0);
  } finally { await server.close(); }
});
test('concurrent refresh shares fetch; candidate conflict and persistent file integrity', async () => {
  const server = await fixture(); const signer = signing();
  await fs.mkdir(path.join(os.tmpdir(),'anchorpass-node-impl'),{recursive:true});
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(),'anchorpass-node-impl/concurrent-'));
  const cachePath = path.join(scratch,'bundle.json');
  const client = new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,cachePath},{origin:server.origin});
  try {
    await server.command('reset',{bundle:signer.bundle()});
    await Promise.all([client.refresh(),client.refresh(),client.refresh()]);
    assert.equal((await server.command('state')).requests.length,1);
    await server.command('reset',{bundle:signer.bundle({fields:{...signer.sample.fields,bg:'changed'}})});
    await assert.rejects(client.refresh(),{code:'ConfigInvalid'});
  } finally { await client.closeAsync(); await server.close(); await fs.rm(scratch,{recursive:true,force:true}); }
});

test('different clients share an update download; closing one waiter preserves the other',async()=>{
  const server=await fixture();const signer=signing();
  const options={bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys};
  const first=new Client(options,{origin:server.origin});const second=new Client(options,{origin:server.origin});
  try{
    await server.command('reset',{mode:'bundle-slow',bundle:signer.bundle()});
    const arrived=require('node:events').once(server.child,'message');
    const cancelled=first.solve(anchor);const success=second.prepare();
    const expectedRejection=assert.rejects(cancelled,{code:'ClientClosed'});
    await arrived;await first.closeAsync();await expectedRejection;await success;
    const state=await server.command('state');assert.equal(state.requests.length,1);
    assert.equal((await second.solve(anchor)).bundle_revision,'2');
  }finally{await first.closeAsync();await second.closeAsync();await server.close();}
});
test('cache FIFO is rejected without blocking worker-pool shutdown',async()=>{
  if(process.platform!=='linux')return;
  const {spawnSync}=require('node:child_process');
  await fs.mkdir(path.join(os.tmpdir(),'anchorpass-node-impl'),{recursive:true});
  const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'anchorpass-node-impl/fifo-'));
  const cachePath=path.join(scratch,'cache');
  try {
    const created=spawnSync('mkfifo',[cachePath],{encoding:'utf8',timeout:5000});assert.equal(created.status,0,created.stderr);
    const source=`const {Client}=require(${JSON.stringify(path.resolve(__dirname,'../dist/client.js'))});(async()=>{const client=new Client({cachePath:${JSON.stringify(cachePath)},timeout:.1});try{await client.prepare();process.exitCode=2}catch(error){if(error.code!=='ConfigInvalid')throw error}finally{await client.closeAsync()}})().catch(error=>{console.error(error);process.exitCode=1});`;
    const child=spawnSync(process.execPath,['-e',source],{timeout:2000,encoding:'utf8'});
    assert.equal(child.status,0,child.stderr);assert.equal(child.signal,null);
  }finally{await fs.rm(scratch,{recursive:true,force:true});}
});
