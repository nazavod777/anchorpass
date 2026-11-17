'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { Client } = require('../dist/client.js');
const { fixture, signing } = require('./helpers.cjs');

for (const persistent of [false,true]) test(`expired remote revision establishes a floor (${persistent?'persistent':'memory'})`,async()=>{
  const server=await fixture();const signer=signing();
  const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'anchorpass-expired-floor-'));
  const cachePath=persistent?path.join(scratch,'cache.json'):undefined;
  const options={bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,cachePath};
  const client=new Client(options,{origin:server.origin});
  try {
    await server.command('reset',{bundle:signer.bundle({revision:'2'})}); await client.refresh();
    const expired=signer.bundle({revision:'4',issued_at:1,expires_at:2});
    await server.command('reset',{bundle:expired});
    await assert.rejects(client.refresh(),{code:'ConfigExpired'});
    if(persistent)assert.equal(await fs.readFile(cachePath,'utf8'),expired);
    await server.command('reset',{bundle:signer.bundle({revision:'3'})});
    await assert.rejects(client.refresh(),{code:'ConfigRollback'});
    if(persistent) {
      const restarted=new Client({...options,offline:true});
      try {await assert.rejects(restarted.prepare(),{code:'ConfigExpired'});}
      finally {await restarted.closeAsync();}
    }
  }finally {await client.closeAsync();await server.close();await fs.rm(scratch,{recursive:true,force:true});}
});
test('pin does not disable a verified floor',async()=>{
  const server=await fixture();const signer=signing();
  const client=new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,pin:'2'});
  try {
    await server.command('reset',{bundle:signer.bundle({revision:'2'})}); await client.refresh();
    await server.command('reset',{bundle:signer.bundle({revision:'3'})});
    await assert.rejects(client.refresh(),{code:'ConfigUnavailable'});
    await assert.rejects(client.prepare(),{code:'ConfigRollback'});
  }finally {await client.closeAsync();await server.close();}
});
test('invalid signatures cannot raise the revision floor',async()=>{
  const server=await fixture();const signer=signing();
  const client=new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys});
  try {
    await server.command('reset',{bundle:signer.bundle({revision:'2'})}); await client.refresh();
    const invalid=JSON.parse(signer.bundle({revision:'9'}));invalid.signature='AA==';
    await server.command('reset',{bundle:JSON.stringify(invalid)});
    await assert.rejects(client.refresh(),{code:'ConfigInvalid'});
    await server.command('reset',{bundle:signer.bundle({revision:'3'})}); await client.refresh();
  }finally {await client.closeAsync();await server.close();}
});

for (const expired of [false, true]) test(`cache failure preserves authenticated floor and blocks solves (expired=${expired})`, async () => {
  const { anchor } = require('./helpers.cjs');
  const server = await fixture(); const signer = signing();
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'anchorpass-cache-failure-'));
  const cachePath = path.join(scratch, 'cache.json');
  await fs.writeFile(cachePath, signer.bundle({revision:'2'}));
  await fs.mkdir(cachePath + '.lock');
  const candidate = signer.bundle(expired ? {revision:'3',issued_at:1,expires_at:2} : {revision:'3'});
  const client = new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,cachePath,updateTimeout:0.05},{origin:server.origin});
  try {
    await server.command('reset', {bundle:candidate});
    await assert.rejects(client.solve(anchor), {code:'DeadlineExceeded'});
    // Restore the normal deadline after exercising the deliberate cache-lock timeout.
    client.updates.updateTimeout = 5;
    assert.equal((await server.command('state')).requests.every(request => request.url === '/bundle'), true);
    await fs.rmdir(cachePath+'.lock');
    await server.command('reset', {bundle:signer.bundle({revision:'2'})});
    await assert.rejects(client.refresh(), {code:'ConfigRollback'});
    await assert.rejects(client.prepare(), {code:'ConfigRollback'});
    await server.command('reset', {bundle:candidate});
    if (expired) await assert.rejects(client.refresh(), {code:'ConfigExpired'});
    else await client.refresh();
    assert.equal(await fs.readFile(cachePath,'utf8'), candidate);
    await server.command('reset', {bundle:signer.bundle({revision:'4'})});
    await client.refresh();
    assert.equal((await client.solve(anchor)).bundle_revision, '4');
  } finally { await client.closeAsync(); await server.close(); await fs.rm(scratch,{recursive:true,force:true}); }
});

test('invalid signature cannot raise the floor when cache writes are blocked', async () => {
  const server = await fixture(); const signer = signing();
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'anchorpass-cache-invalid-'));
  const cachePath = path.join(scratch, 'cache.json');
  await fs.writeFile(cachePath, signer.bundle({revision:'2'}));
  await fs.mkdir(cachePath + '.lock');
  const invalid = JSON.parse(signer.bundle({revision:'9'})); invalid.signature = 'AA==';
  const client = new Client({bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,cachePath});
  try { await server.command('reset',{bundle:JSON.stringify(invalid)}); await client.prepare(); }
  finally { await client.closeAsync(); await server.close(); await fs.rm(scratch,{recursive:true,force:true}); }
});

test('a completed refresh cannot undo a higher floor observed from another cache writer', async () => {
  const { anchor } = require('./helpers.cjs');
  const server = await fixture(); const signer = signing();
  const scratch = await fs.mkdtemp(path.join(os.tmpdir(), 'anchorpass-cache-race-'));
  const cachePath = path.join(scratch, 'bundle.json');
  await fs.writeFile(cachePath, signer.bundle({revision:'2'}));
  const options = {bundleUrl:server.origin+'/bundle',trustedKeys:signer.keys,cachePath};
  const first = new Client(options,{origin:server.origin});
  const second = new Client(options,{origin:server.origin});
  const unlink = fs.unlink;
  let enter, resume;
  const entered = new Promise(resolve => { enter = resolve; });
  const released = new Promise(resolve => { resume = resolve; });
  let paused = false;
  fs.unlink = async filename => {
    await unlink(filename);
    if (filename === cachePath+'.lock' && !paused) {
      paused = true; enter(); await released;
    }
  };
  let refresh, solve;
  try {
    await server.command('reset',{bundle:signer.bundle({revision:'3'})});
    refresh = assert.rejects(first.refresh(),{code:'ConfigRollback'});
    await Promise.race([entered,refresh.then(() => assert.fail('Refresh completed before the cache release hook'))]);
    await server.command('reset',{bundle:signer.bundle({revision:'4',issued_at:1,expires_at:2})});
    await assert.rejects(second.refresh(),{code:'ConfigExpired'});
    solve = assert.rejects(first.solve(anchor),{code:'ConfigRollback'});
    const deadline = performance.now() + 2000;
    while (first.updates.current.revision !== '4' && performance.now() < deadline) {
      await new Promise(resolve => setImmediate(resolve));
    }
    assert.equal(first.updates.current.revision,'4');
    resume();
    await Promise.all([refresh,solve]);
    assert.equal(first.updates.current.revision,'4');
    assert.equal((await server.command('state')).requests.every(request => request.url === '/bundle'),true);
  } finally {
    resume(); fs.unlink = unlink;
    await Promise.allSettled([refresh,solve]);
    await first.closeAsync(); await second.closeAsync(); await server.close();
    await fs.rm(scratch,{recursive:true,force:true});
  }
});
