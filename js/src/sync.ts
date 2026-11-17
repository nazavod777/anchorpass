import { Worker } from 'node:worker_threads';
import path from 'node:path';
import { inspect } from 'node:util';
import { AnchorpassError, errorCodes, fail, type ErrorCode } from './errors.js';
import { parseAnchor, parseProxy, validateBg, validateUserAgent } from './core.js';
import { type ClientOptions, type PrivateOptions, type SolveOptions, type SolveResult } from './client.js';

const MAX_RESULT_BYTES = 65536;
const WORKER_LIMIT = 4;
type SyncSolveOptions = Omit<SolveOptions, 'signal'>;
interface Entry { worker: Worker; state: 'STARTING' | 'RUNNING' | 'TERMINATING'; termination?: Promise<number>; lifecycle: Int32Array; exited: Promise<number> }
const registry = new Set<Entry>();
function wait(control: Int32Array, deadline: number): number {
  while (Atomics.load(control, 0) === 0) {
    const remaining = deadline - performance.now();
    if (remaining <= 0) { Atomics.compareExchange(control, 0, 0, 3); return Atomics.load(control, 0); }
    Atomics.wait(control, 0, 0, remaining);
  }
  return Atomics.load(control, 0);
}
function readResult(control: Int32Array, shared: SharedArrayBuffer): unknown {
  const length = Atomics.load(control, 1);
  if (length < 0 || length > MAX_RESULT_BYTES) return fail('WorkerUnavailable', 'Worker returned an invalid result size', 'worker');
  try { return JSON.parse(Buffer.from(shared, 8, length).toString('utf8')) as unknown; }
  catch { return fail('WorkerUnavailable', 'Worker returned invalid result bytes', 'worker'); }
}
function throwRemote(value: unknown): never {
  if (value && typeof value === 'object' && 'code' in value && 'message' in value) {
    const error = value as { code: ErrorCode; message: string; stage?: string };
    if (errorCodes.includes(error.code) && typeof error.message === 'string') throw new AnchorpassError(error.code, error.message, error.stage ?? 'worker');
  }
  return fail('WorkerUnavailable', 'Worker failed without a valid error', 'worker');
}
/** One persistent worker per client; calls are sequential in the owning isolate. */
export class SyncClient {
  private entry?: Entry;
  private closed = false;
  private busy = false;
  private closing?: Promise<void>;
  private readonly timeout: number;
  private inFlight?: Int32Array;
  private readonly options: ClientOptions;
  constructor(options: ClientOptions = {}, private readonly privateOptions: PrivateOptions = {}) {
    this.options = { ...options, ...(options.trustedKeys ? { trustedKeys: { ...options.trustedKeys } } : {}) };
    parseProxy(options.proxy);
    parseProxy(Object.hasOwn(options, 'updatesProxy') ? options.updatesProxy : options.proxy);
    this.timeout = options.timeout ?? 30;
    if (!Number.isFinite(this.timeout) || this.timeout <= 0 || this.timeout > 86400) fail('InvalidInput', 'timeout is outside its allowed range');
  }
  [inspect.custom](): string { return `SyncClient { closed: ${this.closed}, state: ${this.entry?.state ?? 'NEW'} }`; }
  private start(deadline: number): Entry {
    if (this.entry) return this.entry;
    for (const entry of registry) if (Atomics.load(entry.lifecycle, 0) === 1) registry.delete(entry);
    if (registry.size >= WORKER_LIMIT) fail('ResourceLimit', 'Worker limit reached, including terminating workers', 'worker');
    const handshake = new SharedArrayBuffer(MAX_RESULT_BYTES + 8);
    const control = new Int32Array(handshake, 0, 2);
    const lifecycle = new Int32Array(new SharedArrayBuffer(4));
    let worker: Worker;
    try { worker = new Worker(path.join(__dirname, 'worker.js'), { workerData: { options: this.options, privateOptions: this.privateOptions, handshake, lifecycle: lifecycle.buffer }, resourceLimits: { maxOldGenerationSizeMb: 64, maxYoungGenerationSizeMb: 16, stackSizeMb: 4 } }); }
    catch { return fail('WorkerUnavailable', 'Worker could not be started', 'worker'); }
    const exited = new Promise<number>(resolve => worker.once('exit', resolve));
    const entry: Entry = { worker, state: 'STARTING', lifecycle, exited }; this.entry = entry; registry.add(entry);
    worker.unref();
    worker.on('error', () => { this.closed = true; this.terminate(entry); });
    worker.on('exit', () => { registry.delete(entry); if (entry.state !== 'TERMINATING') this.closed = true; });
    const status = wait(control, deadline);
    if (status !== 1) {
      this.closed = true; this.terminate(entry);
      if (status === 2) throwRemote(readResult(control, handshake));
      return fail('DeadlineExceeded', 'Worker did not become ready before the deadline', 'worker');
    }
    entry.state = 'RUNNING'; return entry;
  }
  private terminate(entry: Entry): Promise<void> {
    if (!entry.termination) {
      entry.state = 'TERMINATING'; entry.termination = entry.worker.terminate();
      entry.termination.then(() => registry.delete(entry), () => registry.delete(entry));
    }
    this.closing = entry.termination.then(() => undefined);
    this.closing.catch(() => undefined); return this.closing;
  }
  private invoke(kind: 'solve' | 'loadBg', anchorUrl?: string, options: SyncSolveOptions = {}): unknown {
    const deadline = performance.now() + this.timeout * 1000;
    if (this.closed) fail('ClientClosed', 'Sync client is closed');
    if (this.busy) fail('ResourceLimit', 'Sync client already has an operation');
    if (anchorUrl !== undefined) parseAnchor(anchorUrl, options.action, true, options.hostname);
    validateBg(options.bg);
    validateUserAgent(options.userAgent);
    this.busy = true;
    try {
      const entry = this.start(deadline);
      const shared = new SharedArrayBuffer(MAX_RESULT_BYTES + 8);
      const control = new Int32Array(shared, 0, 2); this.inFlight = control;
      try { entry.worker.postMessage({ kind, anchorUrl, action: options.action, bg: options.bg, hostname: options.hostname, userAgent: options.userAgent, shared, timeout: Math.max(0, deadline - performance.now()) }); }
      catch { this.closed = true; this.terminate(entry); return fail('WorkerUnavailable', 'Worker could not accept the operation', 'worker'); }
      const state = wait(control, deadline);
      if (state === 3) {
        this.closed = true; this.terminate(entry);
        return fail('DeadlineExceeded', 'Sync operation deadline exceeded', 'worker');
      }
      const result = readResult(control, shared);
      if (state === 2) throwRemote(result);
      if (state !== 1) return fail('WorkerUnavailable', 'Worker returned an invalid state', 'worker');
      return result;
    } finally { this.busy = false; this.inFlight = undefined; }
  }
  solveSync(anchorUrl: string, options: SyncSolveOptions = {}): SolveResult { return this.invoke('solve', anchorUrl, options) as SolveResult; }
  loadBgSync(): string { return this.invoke('loadBg') as string; }
  close(): void {
    if (this.closed && !this.entry) return;
    this.closed = true;
    if (this.inFlight) { Atomics.compareExchange(this.inFlight, 0, 0, 3); Atomics.notify(this.inFlight, 0); }
    if (this.entry) {
      const entry = this.entry;
      if (entry.state === 'TERMINATING') return;
      entry.state = 'TERMINATING';
      entry.termination = entry.exited;
      this.closing = entry.termination.then(() => { registry.delete(entry); });
      try { entry.worker.postMessage({ kind: 'close' }); } catch { this.terminate(entry); return; }
      const deadline = performance.now() + 1000;
      while (Atomics.load(entry.lifecycle, 0) === 0) {
        const remaining = deadline - performance.now();
        if (remaining <= 0) { void entry.worker.terminate(); break; }
        Atomics.wait(entry.lifecycle, 0, 0, remaining);
      }
      if (Atomics.load(entry.lifecycle, 0) === 1) registry.delete(entry);
    }
  }
  async closeAsync(): Promise<void> {
    this.entry?.worker.ref();
    try { this.close(); await this.closing; } finally { this.entry?.worker.unref(); }
  }
}
export function solveSync(anchorUrl: string, options: ClientOptions & SyncSolveOptions = {}): SolveResult {
  const client = new SyncClient(options);
  try { return client.solveSync(anchorUrl, { action: options.action, bg: options.bg, hostname: options.hostname, userAgent: options.userAgent }); } finally { client.close(); }
}
/** Internal diagnostics, deliberately absent from the package export map. */
export function workerStates(): string[] { return [...registry].map(entry => entry.state); }

export function loadBgSync(options: ClientOptions = {}): string {
  const client = new SyncClient(options);
  try { return client.loadBgSync(); } finally { client.close(); }
}
