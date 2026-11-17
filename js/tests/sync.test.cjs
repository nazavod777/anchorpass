'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { SyncClient, workerStates } = require('../dist/sync.js');
const { loadBgSync, loadBg, extractAction } = require('../dist/index.js');
const { fixture, anchor } = require('./helpers.cjs');

test('persistent sync worker reuses connections, forwards overrides and shuts down', async () => {
  const server = await fixture(); const client = new SyncClient({timeout:2},{origin:server.origin});
  try {
    for (let i=0;i<8;i++) assert.equal(client.solveSync(anchor,{action:'sync-action',bg:'sync-bg-'+i}).token,'fixture-token');
    assert.ok(client.loadBgSync().length > 0);
    const state = await server.command('state');
    assert.equal(state.requests.length,24); assert.ok(state.connections <= 2);
    for (let i=0;i<8;i++) { const body = new URLSearchParams(state.requests[i*3+2].body); assert.equal(body.get('bg'),'sync-bg-'+i); assert.equal(body.get('sa'),'sync-action'); }
    assert.deepEqual(workerStates(),['RUNNING']);
    client.close(); client.close(); await client.closeAsync();
    assert.deepEqual(workerStates(),[]); assert.throws(()=>client.solveSync(anchor),{code:'ClientClosed'});
  } finally { await client.closeAsync(); await server.close(); }
});
test('sync timeout, startup failure, worker crash and bounded worker accounting', async () => {
  for (const privateOptions of [{hangWorker:true},{crashWorker:true}]) {
    const client = new SyncClient({timeout:0.2},privateOptions); const started=performance.now();
    try { assert.throws(()=>client.solveSync(anchor),{code:'DeadlineExceeded'}); assert.ok(performance.now()-started < 1200); assert.throws(()=>client.solveSync(anchor),{code:'ClientClosed'}); } finally { await client.closeAsync(); }
  }
  const failed = new SyncClient({timeout:1},{failStartup:true});
  try { assert.throws(()=>failed.loadBgSync(),{code:'WorkerUnavailable'}); } finally { await failed.closeAsync(); }
  const clients = Array.from({length:5},()=>new SyncClient({timeout:2}));
  try { for (const client of clients.slice(0,4)) assert.ok(client.loadBgSync()); assert.throws(()=>clients[4].loadBgSync(),{code:'ResourceLimit'}); }
  finally { for(const client of clients) await client.closeAsync(); }
  assert.deepEqual(workerStates(),[]);
});
test('one-shot cleanup without event-loop turns; ESM and CommonJS imports', async () => {
  for(let i=0;i<8;i++) assert.ok(loadBgSync({offline:true}));
  assert.ok(await loadBg({offline:true}));
  assert.equal(extractAction(anchor),'url-action');
  assert.equal(extractAction(anchor.replace('&sa=url-action','')),null);
  assert.throws(()=>extractAction(anchor+'&sa=other'),{code:'InvalidInput'});
  const executable = spawnSync(process.execPath,['-e',"const {loadBgSync}=require('./dist/index.js');loadBgSync({offline:true});"],{cwd:require('node:path').join(__dirname,'..'),timeout:5000,encoding:'utf8'});
  assert.equal(executable.status,0,executable.stderr);
  const esm = await import('../dist/index.mjs'); assert.ok(esm.SyncClient); assert.equal(esm.extractAction(anchor),'url-action');
});

test('oversized serialized sync result is bounded and worker remains reusable',async()=>{
  const server=await fixture();const client=new SyncClient({timeout:2},{origin:server.origin});
  try {
    await server.command('reset',{mode:'unicode-token'});
    assert.throws(()=>client.solveSync(anchor),{code:'ResourceLimit'});
    await server.command('reset');assert.equal(client.solveSync(anchor).token,'fixture-token');
  }finally{await client.closeAsync();await server.close();}
});
