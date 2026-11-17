import { inspect } from 'node:util';
import { AnchorpassError, fail } from './errors.js';
import { parseAnchor, parseAnchorToken, parseProxy, parseReload, parseVersion, validateBg, validateUserAgent } from './core.js';
import { Transport } from './transport.js';
import { OperationCookies } from './cookies.js';
import { Updates, waitFor, type UpdateOptions } from './updates.js';

export interface ClientOptions extends UpdateOptions {
  proxy?: string | null; updatesProxy?: string | null; timeout?: number; maxInFlight?: number;
  maxResponseBytes?: number; connectTimeout?: number; ca?: string; proxyCa?: string;
}
export interface SolveOptions { action?: string; bg?: string; hostname?: string; userAgent?: string; signal?: AbortSignal }
export interface SolveResult { token: string; bundle_revision: string; protocol_family: 'recaptcha-v3/1'; duration_ms: number }
export interface PrivateOptions { origin?: string; crashWorker?: boolean; hangWorker?: boolean; failStartup?: boolean }
function positive(value: number, name: string, maximum: number): number {
  if (!Number.isFinite(value) || value <= 0 || value > maximum) return fail('InvalidInput', `${name} is outside its allowed range`);
  return value;
}
export class Client {
  private readonly captcha: Transport;
  private readonly updateTransport: Transport;
  private readonly updates: Updates;
  private readonly timeout: number;
  private readonly maxInFlight: number;
  private readonly maxResponseBytes: number;
  private readonly origin: string;
  private readonly active = new Set<AbortController>();
  private closed = false;
  private closing?: Promise<void>;
  constructor(options: ClientOptions = {}, privateOptions: PrivateOptions = {}) {
    this.timeout = positive(options.timeout ?? 30, 'timeout', 86400);
    this.maxInFlight = positive(options.maxInFlight ?? 16, 'maxInFlight', 1024);
    this.maxResponseBytes = positive(options.maxResponseBytes ?? 1048576, 'maxResponseBytes', 16777216);
    if (!Number.isInteger(this.maxInFlight) || !Number.isInteger(this.maxResponseBytes)) fail('InvalidInput', 'Resource limits must be integers');
    const connectTimeout = positive(options.connectTimeout ?? 5, 'connectTimeout', 300) * 1000;
    const proxy = parseProxy(options.proxy);
    const updatesProxy = parseProxy(Object.hasOwn(options, 'updatesProxy') ? options.updatesProxy : options.proxy);
    this.origin = privateOptions.origin ?? 'https://www.google.com';
    this.captcha = new Transport({ proxy, connections: this.maxInFlight, connectTimeout, ca: options.ca, proxyCa: options.proxyCa });
    this.updateTransport = new Transport({ proxy: updatesProxy, connections: 1, connectTimeout, ca: options.ca, proxyCa: options.proxyCa });
    try { this.updates = new Updates(options, this.updateTransport); }
    catch (error) { void this.captcha.close(); void this.updateTransport.close(); throw error; }
  }
  [inspect.custom](): string { return `Client { closed: ${this.closed}, active: ${this.active.size} }`; }
  private async operation<T>(signal: AbortSignal | undefined, run: (signal: AbortSignal) => Promise<T>, started = performance.now()): Promise<T> {
    if (this.closed) fail('ClientClosed', 'Client is closed');
    if (this.active.size >= this.maxInFlight) fail('ResourceLimit', 'Client concurrency limit reached');
    const controller = new AbortController();
    const abort = (): void => controller.abort(new AnchorpassError('Cancelled', 'Operation was cancelled', 'solve'));
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, { once: true });
    const deadline = started + this.timeout * 1000;
    const timer = setTimeout(() => controller.abort(new AnchorpassError('DeadlineExceeded', 'Solve deadline exceeded', 'solve')), Math.max(0, deadline - performance.now()));
    this.active.add(controller);
    try {
      controller.signal.throwIfAborted();
      const result = await waitFor(run(controller.signal), controller.signal);
      if (performance.now() >= deadline) fail('DeadlineExceeded', 'Solve deadline exceeded', 'solve');
      return result;
    }
    finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); this.active.delete(controller); }
  }
  async solve(anchorUrl: string, options: SolveOptions = {}): Promise<SolveResult> {
    const started = performance.now();
    const { action, bg, hostname, userAgent } = options;
    const anchor = parseAnchor(anchorUrl, action, true, hostname);
    validateBg(bg);
    validateUserAgent(userAgent);
    return await this.operation(options.signal, async signal => {
      const bundle = await this.updates.get(signal, this.timeout);
      const cookies = new OperationCookies();
      const script = anchor.variant === 'enterprise' ? '/recaptcha/enterprise.js' : '/recaptcha/api.js';
      const version = parseVersion(await this.captcha.read(`${this.origin}${script}`, 'GET', undefined, signal, this.maxResponseBytes, 'version', cookies, userAgent));
      anchor.query.set('v', version);
      const anchorToken = parseAnchorToken(await this.captcha.read(`${this.origin}${anchor.path}?${anchor.query}`, 'GET', undefined, signal, this.maxResponseBytes, 'anchor', cookies, userAgent));
      const form = new URLSearchParams({ ...bundle.fields, ...(bg === undefined ? {} : { bg }), v: version, k: anchor.siteKey, c: anchorToken, co: anchor.query.get('co')! });
      if (anchor.action !== undefined) form.set('sa', anchor.action);
      const token = parseReload(await this.captcha.read(`${this.origin}/recaptcha/${anchor.variant}/reload?k=${encodeURIComponent(anchor.siteKey)}`, 'POST', form.toString(), signal, this.maxResponseBytes, 'reload', cookies, userAgent));
      return { token, bundle_revision: bundle.revision, protocol_family: 'recaptcha-v3/1', duration_ms: performance.now() - started };
    }, started);
  }
  async loadBg(): Promise<string> { return await this.operation(undefined, async signal => (await this.updates.get(signal, this.timeout)).fields.bg!); }
  async prepare(): Promise<void> { await this.operation(undefined, async signal => { await this.updates.get(signal, this.timeout); }); }
  async refresh(): Promise<void> { await this.operation(undefined, async signal => { await this.updates.get(signal, this.timeout, true); }); }
  close(): void {
    if (this.closed) return;
    this.closed = true;
    const updatesClosed = this.updates.close();
    for (const controller of this.active) controller.abort(new AnchorpassError('ClientClosed', 'Client closed during operation', 'solve'));
    this.closing = Promise.all([this.captcha.close(), this.updateTransport.close(), updatesClosed]).then(() => undefined);
    this.closing.catch(() => undefined);
  }
  async closeAsync(): Promise<void> { this.close(); await this.closing; }
}
export async function solve(anchorUrl: string, options: ClientOptions & SolveOptions = {}): Promise<SolveResult> {
  validateBg(options.bg); validateUserAgent(options.userAgent); parseAnchor(anchorUrl, options.action, true, options.hostname);
  const client = new Client(options);
  try { return await client.solve(anchorUrl, options); } finally { await client.closeAsync(); }
}

export async function loadBg(options: ClientOptions = {}): Promise<string> {
  const client = new Client(options);
  try { return await client.loadBg(); } finally { await client.closeAsync(); }
}
