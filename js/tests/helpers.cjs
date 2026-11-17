'use strict';
const { fork } = require('node:child_process');
const path = require('node:path');
const { generateKeyPairSync, sign } = require('node:crypto');
const START_TIMEOUT_MS = 5000;
const CLOSE_TIMEOUT_MS = 1000;
const KILL_TIMEOUT_MS = 500;
let identifier = 0;
function bounded(promise, milliseconds, message) {
  let timer;
  return Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(message)),milliseconds);})]).finally(()=>clearTimeout(timer));
}
async function fixture(options = {}) {
  const child = fork(options.serverFile ?? path.join(__dirname, 'server.cjs'), [], { stdio: ['ignore', 'inherit', 'inherit', 'ipc'] });
  let didExit=false;
  const exited=new Promise(resolve=>child.once('exit',(code,signal)=>{didExit=true;resolve({code,signal});}));
  // Keep a listener after readiness; a child error must never become an unhandled event.
  let childError;
  child.on('error',error=>{childError=error;});
  async function terminate() {
    if(didExit||!child.pid)return;
    child.kill('SIGTERM');
    try {await bounded(exited,KILL_TIMEOUT_MS,'Fixture SIGTERM deadline exceeded');}
    catch {child.kill('SIGKILL');await bounded(exited,KILL_TIMEOUT_MS,'Fixture SIGKILL deadline exceeded');}
  }
  let ports;
  try {
    ports=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{cleanup();reject(new Error('Fixture startup deadline exceeded'));},options.readyTimeout ?? START_TIMEOUT_MS);
      const cleanup=()=>{clearTimeout(timer);child.off('message',message);child.off('exit',exit);child.off('error',error);};
      const message=data=>{
        if(!data?.ready)return;
        if(!Array.isArray(data.ports)||data.ports.length<5||data.ports.some(port=>!Number.isInteger(port)||port<1||port>65535)){cleanup();reject(new Error('Fixture sent invalid readiness metadata'));return;}
        cleanup();resolve(data.ports);
      };
      const exit=(code,signal)=>{cleanup();reject(new Error(`Fixture exited before readiness (code=${code}, signal=${signal})`));};
      const error=failure=>{cleanup();reject(new Error('Fixture could not start',{cause:failure}));};
      child.on('message',message);child.once('exit',exit);child.once('error',error);
    });
  }catch(error){await terminate();throw error;}
  function command(command, extra = {}) {
    const id = ++identifier;
    return new Promise((resolve, reject) => {
      if(didExit||childError){reject(new Error('Fixture is unavailable'));return;}
      const cleanup=()=>{clearTimeout(timer);child.off('message',receive);child.off('exit',exit);child.off('error',error);};
      const timer = setTimeout(() => { cleanup(); reject(new Error('Fixture command timeout')); }, START_TIMEOUT_MS);
      const receive = message => { if (message.id === id) { cleanup(); resolve(message.state); } };
      const exit=()=>{cleanup();reject(new Error('Fixture exited before answering'));};
      const error=()=>{cleanup();reject(new Error('Fixture command failed'));};
      child.on('message',receive);child.once('exit',exit);child.once('error',error);
      child.send({id,command,...extra},failure=>{if(failure)error();});
    });
  }
  let closing;
  function close() {
    if(closing)return closing;
    closing=(async()=>{
      if(child.connected&&!didExit)child.send({command:'stop'},()=>{});
      try {
        const result=await bounded(exited,CLOSE_TIMEOUT_MS,'Fixture close deadline exceeded');
        if(result.code!==0||result.signal!==null)throw new Error(`Fixture exited unsuccessfully (code=${result.code}, signal=${result.signal})`);
      }catch(error){await terminate();throw error;}
    })();
    return closing;
  }
  return { child, ports, origin: `http://127.0.0.1:${ports[0]}`, command, close };
}
const anchor = 'https://www.google.com/recaptcha/api2/anchor?k=test-key&co=dGVzdA&sa=url-action';
function signing() {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  const keys = { fixture: publicKey.export({format:'der', type:'spki'}).subarray(-32).toString('base64') };
  const sample = JSON.parse(Buffer.from(require('../data/bundle.json').payload, 'base64'));
  function bundle(patch = {}, raw) {
    const now = Math.floor(Date.now() / 1000);
    const payload = Buffer.from(raw ?? JSON.stringify({ ...sample, issued_at: now - 10, expires_at: now + 3600, revision: '2', ...patch }));
    const signature = sign(null, Buffer.concat([Buffer.from('anchorpass.bundle.v1\0fixture\0'), payload]), privateKey);
    return JSON.stringify({ key_id: 'fixture', payload: payload.toString('base64'), signature: signature.toString('base64') });
  }
  return { keys, bundle, sample };
}
module.exports = { fixture, anchor, signing };
