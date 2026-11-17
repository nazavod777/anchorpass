'use strict';
const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs/promises');const os=require('node:os');const path=require('node:path');
const {fixture}=require('./helpers.cjs');
test('fixture startup rejects early exit and readiness timeout and reaps owned child',async()=>{
  const base=path.join(os.tmpdir(),'anchorpass-node-impl');await fs.mkdir(base,{recursive:true});
  const scratch=await fs.mkdtemp(path.join(base,'fixture-'));
  try {
    const early=path.join(scratch,'exit.cjs');await fs.writeFile(early,'process.exit(7);');
    await assert.rejects(fixture({serverFile:early,readyTimeout:1000}),/Fixture exited before readiness.*code=7/);
    const hung=path.join(scratch,'hang.cjs');await fs.writeFile(hung,'setInterval(()=>{},1000);');
    const started=performance.now();await assert.rejects(fixture({serverFile:hung,readyTimeout:50}),/Fixture startup deadline exceeded/);assert.ok(performance.now()-started<2000);
    const server=await fixture();await server.close();await server.close();
  }finally{await fs.rm(scratch,{recursive:true,force:true});}
});
