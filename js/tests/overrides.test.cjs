'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { Client } = require('../dist/client.js');
const { SyncClient } = require('../dist/sync.js');
const { fixture, anchor } = require('./helpers.cjs');
test('bg override is validated before network and isolated between concurrent operations', async()=>{
  const server=await fixture(); const client=new Client({}, {origin:server.origin});
  try {
    for(const bg of ['',null,1,'x'.repeat(16385),'\ud800']) await assert.rejects(client.solve(anchor,{bg}),{code:'InvalidInput'});
    const sync=new SyncClient({timeout:1},{origin:server.origin});
    try { assert.throws(()=>sync.solveSync(anchor,{bg:''}),{code:'InvalidInput'}); } finally { await sync.closeAsync(); }
    assert.equal((await server.command('state')).requests.length,0);
    const original=await client.loadBg();
    await Promise.all([client.solve(anchor,{bg:'first'}),client.solve(anchor,{bg:'second'})]);
    assert.equal(await client.loadBg(),original);
    const state=await server.command('state');
    assert.deepEqual(state.requests.filter(request=>request.method==='POST').map(request=>new URLSearchParams(request.body).get('bg')).sort(),['first','second']);
    await server.command('reset');await client.solve(anchor,{bg:'\0',action:'\0'});
    const controls=(await server.command('state')).requests.find(request=>request.method==='POST');
    assert.equal(new URLSearchParams(controls.body).get('bg'),'\0');assert.equal(new URLSearchParams(controls.body).get('sa'),'\0');assert.match(controls.body,/bg=%00/);
  } finally { await client.closeAsync(); await server.close(); }
});
