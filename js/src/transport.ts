import { AsyncLocalStorage } from 'node:async_hooks';
import { Agent, request, type buildConnector } from 'undici';
import { createHash } from 'node:crypto';
import net, { type Socket } from 'node:net';
import tls from 'node:tls';
import dns from 'node:dns/promises';
import { createBrotliDecompress, createGunzip, createInflate } from 'node:zlib';
import { AnchorpassError, fail } from './errors.js';
import { type ProxyRoute } from './core.js';
import { type OperationCookies } from './cookies.js';

export interface TransportOptions { proxy: ProxyRoute | null; connections: number; connectTimeout: number; ca?: string; proxyCa?: string }
class SocketReader {
  private buffered = Buffer.alloc(0);
  private wait?: () => void;
  private failure?: Error;
  private readonly onData = (chunk: Buffer): void => {
    if (this.buffered.length + chunk.length > 65536) this.failure = new Error('Proxy handshake exceeds limit');
    else this.buffered = Buffer.concat([this.buffered, chunk]);
    this.wait?.();
  };
  private readonly onError = (error: Error): void => { this.failure = error; this.wait?.(); };
  private readonly onClose = (): void => { this.onError(new Error('Connection closed during handshake')); };
  constructor(private readonly socket: Socket) {
    socket.on('data', this.onData); socket.on('error', this.onError); socket.on('close', this.onClose);
  }
  async take(count: number): Promise<Buffer> {
    while (this.buffered.length < count) {
      if (this.failure) throw this.failure;
      await new Promise<void>(resolve => { this.wait = resolve; });
      this.wait = undefined;
    }
    const result = this.buffered.subarray(0, count); this.buffered = this.buffered.subarray(count); return result;
  }
  async headers(): Promise<string> {
    while (!this.buffered.includes('\r\n\r\n')) {
      if (this.failure) throw this.failure;
      await new Promise<void>(resolve => { this.wait = resolve; }); this.wait = undefined;
    }
    const length = this.buffered.indexOf('\r\n\r\n') + 4;
    return (await this.take(length)).toString('latin1');
  }
  release(): void {
    this.socket.pause();
    this.socket.off('data', this.onData); this.socket.off('error', this.onError); this.socket.off('close', this.onClose);
    if (this.buffered.length) this.socket.unshift(this.buffered);
  }
}
function connected(socket: Socket, secure: boolean): Promise<void> {
  return new Promise((resolve, reject) => {
    const event = secure ? 'secureConnect' : 'connect';
    const ready = (): void => { cleanup(); resolve(); };
    const error = (failure: Error): void => { cleanup(); reject(failure); };
    const close = (): void => error(new Error('Connection closed before ready'));
    const cleanup = (): void => { socket.off(event, ready); socket.off('error', error); socket.off('close', close); };
    socket.once(event, ready); socket.once('error', error); socket.once('close', close);
  });
}
async function socksHandshake(socket: Socket, route: ProxyRoute, hostname: string, port: number): Promise<void> {
  const reader = new SocketReader(socket);
  try {
    const authenticated = route.username !== '' || route.password !== '';
    socket.write(Buffer.from(authenticated ? [5, 1, 2] : [5, 1, 0]));
    const greeting = await reader.take(2);
    if (greeting[0] !== 5 || greeting[1] !== (authenticated ? 2 : 0)) fail('ProxyAuthError', 'SOCKS proxy rejected authentication', 'connect');
    if (authenticated) {
      const user = Buffer.from(route.username); const password = Buffer.from(route.password);
      socket.write(Buffer.concat([Buffer.from([1, user.length]), user, Buffer.from([password.length]), password]));
      const auth = await reader.take(2);
      if (auth[0] !== 1 || auth[1] !== 0) fail('ProxyAuthError', 'SOCKS proxy rejected authentication', 'connect');
    }
    let destination = hostname;
    if (route.scheme === 'socks5' && !net.isIP(hostname)) destination = (await dns.lookup(hostname)).address;
    const family = net.isIP(destination);
    let address: Buffer;
    if (family === 4) address = Buffer.from([1, ...destination.split('.').map(Number)]);
    else if (family === 6) {
      // IPv6 is represented as eight network-order words, including compressed forms.
      const expanded = destination.split('::');
      const left = (expanded[0] ?? '').split(':').filter(Boolean);
      const right = (expanded[1] ?? '').split(':').filter(Boolean);
      const words = expanded.length === 2 ? [...left, ...Array<string>(8 - left.length - right.length).fill('0'), ...right] : left;
      address = Buffer.alloc(17); address[0] = 4;
      words.forEach((word, index) => address.writeUInt16BE(Number.parseInt(word, 16), 1 + index * 2));
    } else {
      const name = Buffer.from(destination);
      if (name.length > 255) fail('ProxyConnectError', 'SOCKS destination hostname is too long', 'connect');
      address = Buffer.concat([Buffer.from([3, name.length]), name]);
    }
    const portBytes = Buffer.alloc(2); portBytes.writeUInt16BE(port);
    socket.write(Buffer.concat([Buffer.from([5, 1, 0]), address, portBytes]));
    const response = await reader.take(4);
    if (response[0] !== 5 || response[1] !== 0 || response[2] !== 0) fail('ProxyConnectError', 'SOCKS proxy refused the connection', 'connect');
    const type = response[3];
    if (type === 1) await reader.take(6);
    else if (type === 4) await reader.take(18);
    else if (type === 3) { const length = (await reader.take(1))[0] ?? 0; await reader.take(length + 2); }
    else fail('ProxyConnectError', 'SOCKS proxy returned an invalid address', 'connect');
  } finally { reader.release(); }
}
export class Transport {
  private readonly pending = new Set<Socket>();
  private readonly context = new AsyncLocalStorage<AbortSignal>();
  private readonly agent: Agent;
  private closed = false;
  constructor(private readonly options: TransportOptions) {
    const connector: buildConnector.connector = (target, callback) => {
      void this.connect(target, this.context.getStore()).then(socket => callback(null, socket), error => callback(error as Error, null));
    };
    this.agent = new Agent({ connections: options.connections, pipelining: 1, connect: connector, maxHeaderSize: 16384, headersTimeout: 5000, bodyTimeout: 5000 });
  }
  policyKey(): string { return createHash('sha256').update(JSON.stringify(this.options)).digest('hex'); }
  independent(): Transport {
    if (this.closed) fail('ClientClosed', 'Transport is closed', 'updates');
    return new Transport({ ...this.options });
  }
  private async connect(target: buildConnector.Options, signal?: AbortSignal): Promise<Socket> {
    signal?.throwIfAborted();
    const route = this.options.proxy;
    let phase: 'proxy' | 'target' = route ? 'proxy' : 'target';
    const host = target.hostname.replace(/^\[|\]$/g, '');
    const port = Number(target.port || (target.protocol === 'https:' ? 443 : 80));
    const proxySecure = route?.scheme === 'https';
    let socket: Socket = proxySecure
      ? tls.connect({ host: route.hostname, port: route.port, servername: net.isIP(route.hostname) ? undefined : route.hostname, ca: this.options.proxyCa, rejectUnauthorized: true, ALPNProtocols: ['http/1.1'] })
      : net.connect({ host: route?.hostname ?? host, port: route?.port ?? port });
    this.pending.add(socket);
    const abort = (): void => { socket.destroy(new Error('Request cancelled during connection')); };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    const timer = setTimeout(() => socket.destroy(new Error('Connect deadline exceeded')), this.options.connectTimeout);
    try {
      await connected(socket, proxySecure);
      if (route && (route.scheme === 'http' || route.scheme === 'https')) {
        const reader = new SocketReader(socket);
        try {
          const authority = `${net.isIP(host) === 6 ? `[${host}]` : host}:${port}`;
          const authorization = route.username || route.password ? `Proxy-Authorization: Basic ${Buffer.from(`${route.username}:${route.password}`).toString('base64')}\r\n` : '';
          socket.write(`CONNECT ${authority} HTTP/1.1\r\nHost: ${authority}\r\n${authorization}\r\n`);
          const response = await reader.headers();
          const status = Number(/^HTTP\/1\.[01] ([0-9]{3})\b/.exec(response)?.[1]);
          if (status === 407) fail('ProxyAuthError', 'HTTP proxy rejected authentication', 'connect');
          if (status !== 200) fail('ProxyConnectError', 'HTTP proxy refused the tunnel', 'connect');
        } finally { reader.release(); }
      } else if (route) await socksHandshake(socket, route, host, port);
      phase = 'target';
      if (target.protocol === 'https:') {
        this.pending.delete(socket);
        socket = tls.connect({ socket, host, servername: net.isIP(host) ? undefined : host, ca: this.options.ca, rejectUnauthorized: true, ALPNProtocols: ['http/1.1'] });
        this.pending.add(socket);
        await connected(socket, true);
      }
      socket.setTimeout(0); socket.setNoDelay(true); socket.setKeepAlive(true, 1000);
      return socket;
    } catch (error) {
      socket.destroy();
      if (error instanceof AnchorpassError) throw error;
      const failure = new AnchorpassError(phase === 'proxy' ? 'ProxyConnectError' : 'TransportError', phase === 'proxy' ? 'Proxy connection failed' : 'Target connection failed', 'connect');
      // Retain only a stable error code, never peer names or proxy credentials.
      if (error instanceof Error && 'code' in error && typeof error.code === 'string') Object.defineProperty(failure, 'cause', { value: { code: error.code } });
      throw failure;
    }
    finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); this.pending.delete(socket); }
  }
  async read(url: string, method: 'GET' | 'POST', body: string | undefined, signal: AbortSignal, limit: number, stage: string, cookies?: OperationCookies, userAgent?: string, redirects = 0): Promise<string> {
    if (this.closed) fail('ClientClosed', 'Transport is closed', stage);
    try {
      const cookieHeader = cookies?.header(url, stage);
      const response = await this.context.run(signal, () => request(url, {
        dispatcher: this.agent, method, body, signal, maxRedirections: 0,
        headers: { 'accept-encoding': 'identity', ...(userAgent === undefined ? {} : { 'user-agent': userAgent }), ...(cookieHeader === undefined ? {} : { cookie: cookieHeader }), ...(body === undefined ? {} : { 'content-type': 'application/x-www-form-urlencoded' }) },
      }));
      response.body.on('error', () => undefined);
      try { cookies?.receive(response.headers['set-cookie'], url, stage); }
      catch (error) { response.body.destroy(); throw error; }
      if ([301, 302, 303, 307, 308].includes(response.statusCode) && method === 'GET' && stage !== 'updates') {
        const location = response.headers.location;
        response.body.destroy();
        if (typeof location !== 'string' || redirects >= 2) fail('HttpStatusError', 'Redirect limit exceeded', stage);
        const target = new URL(location, url);
        if (target.origin !== new URL(url).origin || !/^\/recaptcha\//.test(target.pathname) || target.username || target.password || target.hash) fail('HttpStatusError', 'Redirect left the permitted origin', stage);
        return await this.read(target.href, method, body, signal, limit, stage, cookies, userAgent, redirects + 1);
      }
      if (response.statusCode < 200 || response.statusCode >= 300) { response.body.destroy(); fail('HttpStatusError', `HTTP response status ${response.statusCode}`, stage); }
      const encoding = response.headers['content-encoding'];
      const decoder = encoding === 'gzip' ? createGunzip() : encoding === 'br' ? createBrotliDecompress() : encoding === 'deflate' ? createInflate() : null;
      if (encoding && encoding !== 'identity' && !decoder) { response.body.destroy(); fail('ProtocolError', 'Unsupported content encoding', stage); }
      let compressed = 0;
      response.body.on('data', (chunk: Buffer) => { compressed += chunk.length; if (compressed > limit) response.body.destroy(new AnchorpassError('ResourceLimit', 'Encoded response exceeds its limit', stage)); });
      const source = decoder ? response.body.pipe(decoder) : response.body;
      if (decoder) response.body.on('error', error => decoder.destroy(error));
      const chunks: Buffer[] = []; let length = 0;
      try {
        for await (const chunk of source) {
          const bytes = Buffer.from(chunk as Uint8Array); length += bytes.length;
          if (length > limit) fail('ResourceLimit', 'Response exceeds its byte limit', stage);
          chunks.push(bytes);
        }
        try { return new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks, length)); }
        catch { return fail(stage === 'updates' ? 'ConfigInvalid' : 'ProtocolError', 'Response is not valid UTF-8', stage); }
      } finally { decoder?.destroy(); response.body.destroy(); }
    } catch (error) {
      if (signal.aborted) throw signal.reason;
      if (error instanceof AnchorpassError) throw error;
      // Underlying socket errors can contain a hostname or credentials; expose only stable context.
      const cause = error instanceof Error && 'cause' in error ? error.cause : undefined;
      if (cause instanceof AnchorpassError) throw cause;
      throw new AnchorpassError('TransportError', 'HTTP transport failed', stage);
    }
  }
  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    for (const socket of this.pending) socket.destroy();
    await this.agent.destroy();
  }
}
