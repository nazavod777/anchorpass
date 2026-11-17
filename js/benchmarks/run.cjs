'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { performance } = require('node:perf_hooks');
const http = require('node:http');
const { Agent, request } = require('undici');
const axios = require('axios');
const { Client } = require('../dist/client.js');
const { SyncClient } = require('../dist/sync.js');
const { fixture, anchor } = require('../tests/helpers.cjs');
const SAMPLES = 90;
const REPETITIONS = 3;
const WARMUP = 15;
function quantile(sorted,p) { return sorted[Math.min(sorted.length-1,Math.ceil(p*sorted.length)-1)]; }
function summarize(latencies,elapsed,errors) {
  const sorted = [...latencies].sort((a,b)=>a-b);
  return { samples:sorted.length, p50_ms:quantile(sorted,.5), p95_ms:quantile(sorted,.95), p99_ms:quantile(sorted,.99), elapsed_ms:elapsed, operations_per_second:latencies.length/(elapsed/1000), errors };
}
function optionalRead(file) { try {return fs.readFileSync(file,'utf8').trim();} catch {return null;} }
function cgroupLimits() {
  const scope=optionalRead('/proc/self/cgroup')?.split('\n').find(line=>line.startsWith('0::'))?.slice(3) || '';
  const observed=[];let current=path.join('/sys/fs/cgroup',scope);
  while(current.startsWith('/sys/fs/cgroup')) { observed.push({path:current,cpu_max:optionalRead(path.join(current,'cpu.max')),memory_max:optionalRead(path.join(current,'memory.max'))});if(current==='/sys/fs/cgroup')break;current=path.dirname(current); }
  return observed;
}
function runtimeResource() {
  const read = file => { try {return fs.readFileSync(file,'utf8').trim();}catch{return null;} };
  return { rss_bytes:process.memoryUsage().rss, descriptors:fs.readdirSync('/proc/self/fd').length, threads:read('/proc/self/status')?.match(/^Threads:\s+(\d+)$/m)?.[1] };
}
async function profile(run, samples, concurrency) {
  const latencies=[];const errors=[];let next=0;
  const resourcesBefore=runtimeResource();const cpuStart=process.cpuUsage();const started=performance.now();
  await Promise.all(Array.from({length:concurrency},async()=>{
    while(next++ < samples) {
      const start=performance.now();
      try { await run();latencies.push(performance.now()-start); }
      catch(error) { errors.push(error.code || error.name); }
    }
  }));
  return { ...summarize(latencies,performance.now()-started,errors), cpu_microseconds:process.cpuUsage(cpuStart), resources_before:resourcesBefore, resources_after:runtimeResource(), latency_ms:latencies };
}
function candidate(name,concurrency,url) {
  if(name==='axios') {
    const agent=new http.Agent({keepAlive:true,maxSockets:concurrency,maxFreeSockets:concurrency});
    return {run:async()=>{ const response=await axios.get(url,{proxy:false,httpAgent:agent,timeout:5000,maxContentLength:1048576,maxRedirects:0,responseType:'text'}); if(response.status!==200) throw new Error('Unexpected status'); },close:async()=>agent.destroy()};
  }
  const agent=new Agent({connections:concurrency,pipelining:1,headersTimeout:5000,bodyTimeout:5000});
  return {run:async()=>{
    const signal=AbortSignal.timeout(5000);
    if(name==='fetch') { const response=await fetch(url,{dispatcher:agent,signal,redirect:'error'});if(response.status!==200) throw new Error('Unexpected status');await response.text(); }
    else {const response=await request(url,{dispatcher:agent,signal,maxRedirections:0});if(response.statusCode!==200) throw new Error('Unexpected status');await response.body.text();}
  },close:()=>agent.close()};
}
(async()=>{
  const server=await fixture();const runs=[];const names=['fetch','undici.request','axios'];
  try {
    for(let repetition=0;repetition<REPETITIONS;repetition++) {
      for(const concurrency of [1,8,32]) {
        for(let index=0;index<names.length;index++) {
          const name=names[(index+repetition)%names.length];
          await server.command('reset');const transport=candidate(name,concurrency,server.origin+'/recaptcha/api.js');
          try {
            for(let warmup=0;warmup<WARMUP;warmup++) await transport.run();
            await server.command('reset');const measured=await profile(transport.run,SAMPLES,concurrency);
            runs.push({kind:'http-warm',candidate:name,concurrency,repetition,...measured,origin_connections:(await server.command('state')).connections});
          }finally{await transport.close();}
        }
      }
    }
    for(const name of names) {
      runs.push({kind:'http-cold',candidate:name,concurrency:1,...await profile(async()=>{const transport=candidate(name,1,server.origin+'/recaptcha/api.js');try{await transport.run();}finally{await transport.close();}},9,1)});
    }
    for(const concurrency of [1,8,32]) {
      const client=new Client({maxInFlight:concurrency},{origin:server.origin});
      try {await client.solve(anchor);runs.push({kind:'full-api-async',candidate:'undici.request+adapter',concurrency,...await profile(()=>client.solve(anchor),30,concurrency)});}finally{await client.closeAsync();}
    }
    const sync=new SyncClient({timeout:5},{origin:server.origin});
    try{sync.solveSync(anchor);runs.push({kind:'full-api-sync',candidate:'persistent-worker',concurrency:1,...await profile(()=>sync.solveSync(anchor),30,1)});}finally{await sync.closeAsync();}
    const data={generated_at:new Date().toISOString(),runtime:process.version,versions:process.versions,dependencies:{undici:require('undici/package.json').version,axios:require('axios/package.json').version,html_entities:require('html-entities/package.json').version},os:{platform:os.platform(),release:os.release(),arch:os.arch(),cpu:os.cpus()[0]?.model},limits:{cgroup_ancestors:cgroupLimits(),open_files:fs.readFileSync('/proc/self/limits','utf8').split('\n').find(line=>line.startsWith('Max open files'))},method:{samples:SAMPLES,repetitions:REPETITIONS,warmup:WARMUP,payload:`${Buffer.byteLength('https://www.gstatic.com/recaptcha/releases/test-version/recaptcha__en.js')}-byte API script over local HTTP`,timeouts_ms:5000,retries:0,redirects:0,concurrency:[1,8,32],notes:'Bounded child fixture; direct transport pilot, not external compatibility or full proxy performance comparison. RSS observations are samples, not measured peak.'},runs};
    fs.writeFileSync(path.join(__dirname,'raw.json'),JSON.stringify(data,null,2)+'\n');
    const aggregate=[];
    for(const name of names)for(const concurrency of [1,8,32]) {
      const selected=runs.filter(run=>run.kind==='http-warm'&&run.candidate===name&&run.concurrency===concurrency);
      const latencies=selected.flatMap(run=>run.latency_ms);aggregate.push({candidate:name,concurrency,...summarize(latencies,selected.reduce((sum,run)=>sum+run.elapsed_ms,0),selected.flatMap(run=>run.errors))});
    }
    console.log(JSON.stringify({runtime:data.runtime,dependencies:data.dependencies,aggregate,full_api:runs.filter(run=>run.kind.startsWith('full-api')).map(({latency_ms,...run})=>run)},null,2));
  }finally{await server.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
