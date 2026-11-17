'use strict';
// Run after npm pack. The installed package must not consult monorepo data.
const { spawnSync }=require('node:child_process');
const fs=require('node:fs/promises');const path=require('node:path');const os=require('node:os');
const assert=require('node:assert/strict');
const {fixture,anchor}=require('../tests/helpers.cjs');
const packageMetadata=require('../package.json');
(async()=>{
  const scratch=await fs.mkdtemp(path.join(os.tmpdir(),'anchorpass-node-package-'));
  try {
    const packageFile=path.resolve(process.argv[2] || path.join(__dirname,`../${packageMetadata.name}-${packageMetadata.version}.tgz`));
    const install=spawnSync('npm',['install','--ignore-scripts','--no-audit','--no-fund',packageFile],{cwd:scratch,encoding:'utf8',timeout:60000});
    assert.equal(install.status,0,install.stderr);
    const installed=path.join(scratch,'node_modules/anchorpass');
    const installedMetadata=JSON.parse(await fs.readFile(path.join(installed,'package.json'),'utf8'));
    assert.equal(installedMetadata.name,packageMetadata.name);
    assert.equal(installedMetadata.version,packageMetadata.version);
    for(const name of ['data/bundle.json','data/keys.json','dist/worker.js','dist/index.mjs','LICENSE','NOTICE'])assert.ok((await fs.stat(path.join(installed,name))).isFile());
    const api=require(require('node:module').createRequire(path.join(scratch,'consumer.cjs')).resolve('anchorpass'));assert.ok(await api.loadBg({offline:true}));assert.ok(api.loadBgSync({offline:true}));
    const server=await fixture();
    try{
      await server.command('reset',{mapGoogle:true});
      const options={proxy:`http://user:p%40ss@127.0.0.1:${server.ports[2]}`,ca:await fs.readFile(path.join(__dirname,'../tests/certs/cert.pem'),'utf8'),timeout:3};
      assert.equal((await api.solve(anchor,{...options,bg:'packed-bg',action:'packed-action'})).token,'fixture-token');
      const client=new api.Client(options);
      try{assert.equal((await client.solve(anchor)).token,'fixture-token');assert.equal((await client.solve(anchor)).token,'fixture-token');}finally{await client.closeAsync();}
      assert.equal(api.solveSync(anchor,{...options,bg:'packed-sync'}).token,'fixture-token');
      const sync=new api.SyncClient(options);
      try{assert.equal(sync.solveSync(anchor).token,'fixture-token');assert.equal(sync.solveSync(anchor).token,'fixture-token');}finally{await sync.closeAsync();}
      const state=await server.command('state');assert.equal(state.requests.length,18);
      assert.equal(new URLSearchParams(state.requests[2].body).get('bg'),'packed-bg');
      const esm=spawnSync(process.execPath,['--input-type=module','-e',"import {loadBgSync,extractAction} from 'anchorpass';if(!loadBgSync({offline:true}))process.exit(2);if(extractAction('"+anchor+"')!=='url-action')process.exit(3);"],{cwd:scratch,encoding:'utf8',timeout:5000});
      assert.equal(esm.status,0,esm.stderr);
      const typeSource="import { Client, SyncClient, solve, solveSync, loadBg, loadBgSync, extractAction, type SolveResult } from 'anchorpass';\nconst client=new Client({ proxy:null, updatesProxy:null, timeout:1 });\nconst sync=new SyncClient();\nconst operation:Promise<SolveResult>=client.solve('url',{bg:'x',action:'login'});\nconst immediate:SolveResult=sync.solveSync('url',{bg:'x'});\nvoid [operation,immediate,solve,solveSync,loadBg,loadBgSync,extractAction];\n// @ts-expect-error private origin injection must not be part of public declarations\nnew Client({}, {origin:'http://localhost'});\n";
      await fs.writeFile(path.join(scratch,'consumer.cts'),typeSource);await fs.writeFile(path.join(scratch,'consumer.mts'),typeSource);
      const typed=spawnSync(process.execPath,[path.join(__dirname,'../node_modules/typescript/bin/tsc'),'--strict','--noEmit','--target','ES2022','--module','Node16','--moduleResolution','Node16','--typeRoots',path.join(__dirname,'../node_modules/@types'),'consumer.cts','consumer.mts'],{cwd:scratch,encoding:'utf8',timeout:30000});
      assert.equal(typed.status,0,typed.stdout+typed.stderr);
      console.log('Installed tarball smoke passed: standalone data, CJS/ESM, async/sync one-shot and reused public clients through loopback CONNECT/TLS');
    }finally{await server.close();}
  }finally{await fs.rm(scratch,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
