'use strict';
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const credentials = { key: fs.readFileSync(path.join(__dirname, 'certs/key.pem')), cert: fs.readFileSync(path.join(__dirname, 'certs/cert.pem')) };
const state = { mapGoogle:false, requests: [], connections: 0, tunnels: [], socks: [], mode: 'normal', bundle: fs.readFileSync(path.join(__dirname, '../data/bundle.json'), 'utf8') };
const sockets = new Set();
function track(server) {
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  return server;
}
const handler = (request, response) => {
  const chunks = []; let size = 0;
  request.on('data', chunk => { size += chunk.length; if (size > 1048576) request.destroy(); else chunks.push(chunk); });
  request.on('end', () => {
    state.requests.push({ method: request.method, url: request.url, headers: request.headers, body: Buffer.concat(chunks).toString() });
    const url = new URL(request.url, 'http://fixture');
    if (url.pathname === '/bundle') { response.setHeader('set-cookie','update=private; Path=/'); if (state.mode === 'bundle-slow') { process.send({event:'bundle-request'}); const timer=setTimeout(()=>response.end(state.bundle),200);response.on('close',()=>clearTimeout(timer)); } else response.end(state.bundle); return; }
    if (state.mode === 'hang') return;
    if (state.mode === 'huge') { response.end('x'.repeat(1048577)); return; }
    if (state.mode === 'compressed') { response.setHeader('content-encoding', 'gzip'); response.end(require('node:zlib').gzipSync('x'.repeat(1048577))); return; }
    if (state.mode === 'redirect-out') { response.writeHead(302, { location: 'http://untrusted.invalid/recaptcha/api.js' }); response.end(); return; }
    if (state.mode === 'redirect' && url.pathname.endsWith('api.js')) { response.writeHead(302, { location: '/recaptcha/api-redirect.js' }); response.end(); return; }
    if (state.mode === 'cookie-redirect' && url.pathname.endsWith('api.js')) { response.writeHead(302, { location: '/recaptcha/api-redirect.js', 'set-cookie':'redirect=required; Path=/recaptcha/' }); response.end(); return; }
    if (state.mode === 'cookie-redirect' && request.headers.cookie !== 'redirect=required') { response.writeHead(403); response.end(); return; }
    if (state.mode === 'cookie-flow') {
      if (url.pathname.endsWith('/anchor')) response.setHeader('set-cookie', `operation=${url.searchParams.get('sa')}; Path=/recaptcha/`);
      const expected = url.pathname.endsWith('/reload') ? `operation=${new URLSearchParams(Buffer.concat(chunks).toString()).get('sa')}` : undefined;
      if (request.headers.cookie !== expected) { response.writeHead(403); response.end(); return; }
    } else if (state.mode === 'cookie-limit') {
      response.setHeader('set-cookie', Array.from({length:url.pathname.endsWith('.js')?32:33}, (_,index)=>`cookie${index}=value; Path=/recaptcha/`));
    } else if (state.mode === 'cookie-bytes') {
      response.setHeader('set-cookie', Array.from({length:3}, (_,index)=>`cookie${index}=${'x'.repeat(3000)}; Path=/recaptcha/`));
    } else if (state.mode !== 'cookie-redirect') response.setHeader('set-cookie', 'operation=must-not-persist; Path=/recaptcha/');
    if (state.mode === 'html-tokenizer' && url.pathname.endsWith('/reload') && new URLSearchParams(Buffer.concat(chunks).toString()).get('c') !== 'actual-token') { response.writeHead(403); response.end(); return; }
    if (url.pathname.endsWith('.js')) {
      const script = 'https://www.gstatic.com/recaptcha/releases/test-version/recaptcha__en.js';
      if (state.mode === 'slow') {
        let offset = 0; const timer = setInterval(() => { if (offset === script.length) { clearInterval(timer); response.end(); } else response.write(script[offset++]); }, 20);
        response.on('close', () => clearInterval(timer));
      } else response.end(script);
    } else if (url.pathname.endsWith('/anchor')) response.end(state.mode==='html-tokenizer'?'<!-- <input id="recaptcha-token" value="comment-decoy"> --><script>const sample=\'<input id="recaptcha-token" value="script-decoy">\';</script><input value=actual-token id=recaptcha-token>':state.mode==='entities'?'<input value="anchor&amp;token&#x2B;&#43;" id="recaptcha-token" type="hidden">':'<input value="anchor-token" id="recaptcha-token" type="hidden">');
    else if (url.pathname.endsWith('/reload')) response.end(")]}'\n"+JSON.stringify(['rresp',state.mode==='unicode-token'?'界'.repeat(32768):'fixture-token']));
    else { response.writeHead(404); response.end(); }
  });
};
const origin = track(http.createServer(handler));
const secureOrigin = track(https.createServer(credentials, handler));
origin.on('connection', () => state.connections++); secureOrigin.on('connection', () => state.connections++);
function proxy(server) {
  track(server);
  server.on('connect', (request, downstream, head) => {
    state.tunnels.push({ target: request.url, auth: request.headers['proxy-authorization'] });
    if (request.headers['proxy-authorization'] !== 'Basic ' + Buffer.from('user:p@ss').toString('base64')) { downstream.end('HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n'); return; }
    const target = new URL('http://' + request.url);
    let targetHost=target.hostname.replace(/^\[|\]$/g,'');let targetPort=Number(target.port);
    if(state.mapGoogle && ['www.google.com','google.com'].includes(targetHost) && targetPort===443){targetHost='127.0.0.1';targetPort=secureOrigin.address().port;}
    else if(targetHost==='node.invalid')targetHost='127.0.0.1';
    else if(!['127.0.0.1','::1','localhost'].includes(targetHost)){downstream.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');return;}
    const upstream = net.connect({ host:targetHost, port:targetPort });
    upstream.on('connect', () => { downstream.write('HTTP/1.1 200 Connection Established\r\n\r\n'); if (head.length) upstream.write(head); downstream.pipe(upstream); upstream.pipe(downstream); });
    upstream.on('error', () => downstream.destroy()); downstream.on('error', () => upstream.destroy()); downstream.on('close', () => upstream.destroy());
  });
  return server;
}
const httpProxy = proxy(http.createServer((_, response) => { response.writeHead(405); response.end(); }));
const tlsProxy = proxy(https.createServer(credentials, (_, response) => { response.writeHead(405); response.end(); }));
const socks = track(net.createServer(socket => {
  let buffer = Buffer.alloc(0); let phase = 0;
  const consume = () => {
    if (phase === 0) {
      if (buffer.length < 2 || buffer.length < 2 + buffer[1]) return;
      const methods = buffer.subarray(2, 2 + buffer[1]); const auth = methods.includes(2);
      buffer = buffer.subarray(2 + buffer[1]); socket.write(Buffer.from([5, auth ? 2 : 0])); phase = auth ? 1 : 2;
    }
    if (phase === 1) {
      if (buffer.length < 2 || buffer.length < 3 + buffer[1]) return;
      const userLength = buffer[1]; const passLength = buffer[2 + userLength];
      if (buffer.length < 3 + userLength + passLength) return;
      const user = buffer.subarray(2, 2 + userLength).toString(); const pass = buffer.subarray(3 + userLength, 3 + userLength + passLength).toString();
      buffer = buffer.subarray(3 + userLength + passLength);
      const valid = user === 'user' && pass === 'p@ss'; socket.write(Buffer.from([1, valid ? 0 : 1])); if (!valid) { socket.end(); return; } phase = 2;
    }
    if (phase === 2) {
      if (buffer.length < 5) return;
      const kind = buffer[3]; const length = kind === 1 ? 4 : kind === 4 ? 16 : buffer[4]; const offset = kind === 3 ? 5 : 4;
      if (buffer.length < offset + length + 2) return;
      const raw = buffer.subarray(offset, offset + length); const host = kind === 1 ? [...raw].join('.') : kind === 4 ? Array.from({length:8}, (_,i) => raw.readUInt16BE(i*2).toString(16)).join(':') : raw.toString();
      const port = buffer.readUInt16BE(offset + length); buffer = buffer.subarray(offset + length + 2);
      state.socks.push({ kind, host, port }); phase = 3; socket.off('data', data);
      if(!['node.invalid','localhost','127.0.0.1','0:0:0:0:0:0:0:1'].includes(host)){socket.destroy();return;}
      const upstream = net.connect({ host: host === 'node.invalid' ? '127.0.0.1' : host, port });
      upstream.on('connect', () => { socket.write(Buffer.from([5,0,0,1,127,0,0,1,0,0])); if (buffer.length) upstream.write(buffer); socket.pipe(upstream); upstream.pipe(socket); });
      upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy()); socket.on('close', () => upstream.destroy());
    }
  };
  const data = chunk => { if (buffer.length + chunk.length > 65536) { socket.destroy(); return; } buffer = Buffer.concat([buffer, chunk]); consume(); };
  socket.on('data', data); socket.on('error', () => {}); socket.setTimeout(5000, () => socket.destroy());
}));
const ipv6Proxy=proxy(http.createServer((_,response)=>{response.writeHead(405);response.end();}));
const servers = [origin, secureOrigin, httpProxy, tlsProxy, socks, ipv6Proxy];
Promise.all(servers.map(server => new Promise(resolve => server.listen(0, server===ipv6Proxy?'::1':'127.0.0.1', resolve)))).then(() => process.send({ ready: true, ports: servers.map(server => server.address().port) }));
process.on('message', message => {
  if (message.command === 'state') process.send({ id: message.id, state });
  if (message.command === 'reset') { state.requests = []; state.connections = 0; state.tunnels = []; state.socks = []; state.mode = message.mode || 'normal'; state.mapGoogle=message.mapGoogle || false; if (message.bundle !== undefined) state.bundle = message.bundle; process.send({ id: message.id }); }
  if (message.command === 'stop') { for (const socket of sockets) socket.destroy(); for (const server of servers) server.close(); process.disconnect(); }
});
