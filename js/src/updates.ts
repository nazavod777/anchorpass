import { createHash, createPublicKey, verify } from 'node:crypto';
import { constants, readFileSync } from 'node:fs';
import { mkdir, open, rename, unlink } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { AnchorpassError, fail } from './errors.js';
import { exactKeys, record, strictJson } from './json.js';
import { type Transport } from './transport.js';

const MAX_BUNDLE_BYTES = 65536;
const FAMILY = 'recaptcha-v3/1';
const PAYLOAD_KEYS = ['schema_version', 'protocol_family', 'revision', 'issued_at', 'expires_at', 'fields'] as const;
const FIELD_KEYS = ['reason', 'hl', 'size', 'chr', 'vh', 'bg'] as const;
export interface Bundle {
  revision: string; issued_at: number; expires_at: number; fields: Readonly<Record<string, string>>;
  digest: string; envelope: string;
}
export interface UpdateOptions { bundleUrl?: string; cachePath?: string | null; pin?: string; offline?: boolean; trustedKeys?: Record<string, string>; refreshInterval?: number; updateTimeout?: number }
function revisionValid(value: unknown): value is string { return typeof value === 'string' && /^[1-9][0-9]{0,19}$/.test(value); }
export function compareRevision(left: string, right: string): number { return left.length - right.length || (left < right ? -1 : left > right ? 1 : 0); }
function base64(value: unknown): Buffer {
  if (typeof value !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) return fail('ConfigInvalid', 'Bundle contains invalid base64', 'bundle');
  return Buffer.from(value, 'base64');
}
export function verifyBundle(envelope: string, keys: Record<string, string>, now = Date.now() / 1000): Bundle {
  if (Buffer.byteLength(envelope) > MAX_BUNDLE_BYTES) fail('ConfigInvalid', 'Bundle exceeds its byte limit', 'bundle');
  const outer = strictJson(Buffer.from(envelope));
  if (!record(outer) || !exactKeys(outer, ['key_id', 'payload', 'signature']) || typeof outer.key_id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(outer.key_id)) fail('ConfigInvalid', 'Bundle envelope has an unsupported schema', 'bundle');
  const keyText = Object.hasOwn(keys, outer.key_id) ? keys[outer.key_id] : undefined;
  if (!keyText) fail('ConfigInvalid', 'Bundle signing key is not trusted', 'bundle');
  const payload = base64(outer.payload); const signature = base64(outer.signature); const key = base64(keyText);
  if (key.length !== 32 || signature.length !== 64 || payload.length > MAX_BUNDLE_BYTES) fail('ConfigInvalid', 'Bundle key or signature has an invalid length', 'bundle');
  let valid = false;
  try {
    const publicKey = createPublicKey({ key: Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), key]), format: 'der', type: 'spki' });
    valid = verify(null, Buffer.concat([Buffer.from('anchorpass.bundle.v1\0'), Buffer.from(outer.key_id, 'ascii'), Buffer.from([0]), payload]), publicKey, signature);
  } catch { fail('ConfigInvalid', 'Bundle signature could not be verified', 'bundle'); }
  if (!valid) fail('ConfigInvalid', 'Bundle signature is invalid', 'bundle');
  const value = strictJson(payload);
  if (!record(value) || !exactKeys(value, PAYLOAD_KEYS) || value.schema_version !== 1 || value.protocol_family !== FAMILY || !revisionValid(value.revision)) fail('ConfigInvalid', 'Bundle payload is incompatible', 'bundle');
  if (typeof value.issued_at !== 'number' || typeof value.expires_at !== 'number' || !Number.isSafeInteger(value.issued_at) || !Number.isSafeInteger(value.expires_at) || value.issued_at < 0 || value.expires_at <= value.issued_at || value.issued_at > now + 300) fail('ConfigInvalid', 'Bundle timestamps are invalid', 'bundle');
  if (!record(value.fields) || !exactKeys(value.fields, FIELD_KEYS)) fail('ConfigInvalid', 'Bundle fields are incompatible', 'bundle');
  const fields: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const key of FIELD_KEYS) {
    const field = value.fields[key];
    if (typeof field !== 'string' || !field || Buffer.byteLength(field, 'utf8') > 16384 || /[\uD800-\uDFFF]/u.test(field)) fail('ConfigInvalid', 'Bundle field exceeds its type or size limit', 'bundle');
    fields[key] = field;
  }
  return Object.freeze({ revision: value.revision, issued_at: value.issued_at, expires_at: value.expires_at, fields: Object.freeze(fields), digest: createHash('sha256').update(payload).digest('hex'), envelope });
}
function accept(candidate: Bundle, current: Bundle | undefined): Bundle {
  if (!current) return candidate;
  const order = compareRevision(candidate.revision, current.revision);
  if (order < 0) fail('ConfigRollback', 'Bundle revision is below the accepted high water mark', 'bundle');
  if (order === 0 && candidate.digest !== current.digest) fail('ConfigInvalid', 'Bundle revision has conflicting payload bytes', 'bundle');
  return candidate;
}
export async function waitFor<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  signal.throwIfAborted();
  return await new Promise<T>((resolve, reject) => {
    const abort = (): void => { cleanup(); reject(signal.reason); };
    const cleanup = (): void => signal.removeEventListener('abort', abort);
    signal.addEventListener('abort', abort, { once: true });
    promise.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
  });
}
interface SharedDownload {
  promise: Promise<string>; controller: AbortController; users: number; settled: boolean;
}
const sharedDownloads = new Map<string, SharedDownload>();
const MAX_UPDATE_CHANNELS = 16;
const MAX_UPDATE_WAITERS = 16;
async function sharedDownload(source: Transport, url: string, timeout: number, signal: AbortSignal): Promise<string> {
  const key = createHash('sha256').update(`${url}\0${source.policyKey()}\0${timeout}`).digest('hex');
  let entry = sharedDownloads.get(key);
  if (!entry) {
    if (sharedDownloads.size >= MAX_UPDATE_CHANNELS) fail('ResourceLimit', 'Concurrent update channel limit reached', 'updates');
    const transport = source.independent();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new AnchorpassError('DeadlineExceeded', 'Bundle download deadline exceeded', 'updates')), timeout * 1000);
    entry = { controller, users: 0, settled: false, promise: Promise.resolve('') };
    const owned = entry;
    entry.promise = transport.read(url, 'GET', undefined, controller.signal, MAX_BUNDLE_BYTES, 'updates').finally(async () => {
      clearTimeout(timer); owned.settled = true;
      await transport.close();
      if (sharedDownloads.get(key) === owned) sharedDownloads.delete(key);
    });
    sharedDownloads.set(key, entry);
  }
  if (entry.users >= MAX_UPDATE_WAITERS) fail('ResourceLimit', 'Update channel waiter limit reached', 'updates');
  entry.users++;
  try { return await waitFor(entry.promise, signal); }
  finally {
    entry.users--;
    if (entry.users === 0 && !entry.settled) {
      entry.controller.abort(new AnchorpassError('Cancelled', 'Update download has no remaining clients', 'updates'));
      await entry.promise.catch(() => undefined);
    }
  }
}
export class Updates {
  private current?: Bundle;
  private persistenceError?: AnchorpassError;
  private closed = false;
  private refresh?: Promise<Bundle>;
  private refreshController?: AbortController;
  private checkedAt = -Infinity;
  private readonly keys: Record<string, string>;
  private readonly bundled: string;
  private readonly refreshInterval: number;
  private readonly updateTimeout: number;
  private readonly options: UpdateOptions;
  constructor(options: UpdateOptions, private readonly transport: Transport) {
    this.options = { ...options };
    if (options.offline !== undefined && typeof options.offline !== 'boolean') fail('InvalidInput', 'offline must be a boolean');
    this.keys = { ...JSON.parse(readFileSync(path.join(__dirname, '../data/keys.json'), 'utf8')) as Record<string, string>, ...options.trustedKeys };
    this.bundled = readFileSync(path.join(__dirname, '../data/bundle.json'), 'utf8');
    this.refreshInterval = options.refreshInterval ?? 3600;
    this.updateTimeout = options.updateTimeout ?? 5;
    if (!Number.isFinite(this.refreshInterval) || this.refreshInterval < 0 || !Number.isFinite(this.updateTimeout) || this.updateTimeout <= 0) fail('InvalidInput', 'Update limits are invalid');
    if (options.pin !== undefined && !revisionValid(options.pin)) fail('InvalidInput', 'Pinned revision is invalid');
    if (options.cachePath !== undefined && options.cachePath !== null && (typeof options.cachePath !== 'string' || !options.cachePath)) fail('InvalidInput', 'Cache path must be a nonempty filename');
    if (options.bundleUrl !== undefined) {
      if (typeof options.bundleUrl !== 'string' || /[\s\\#]/u.test(options.bundleUrl) || /%(?![a-fA-F0-9]{2})/.test(options.bundleUrl)) fail('InvalidInput', 'Bundle URL is invalid');
      let url: URL;
      try { url = new URL(options.bundleUrl); } catch { return fail('InvalidInput', 'Bundle URL is invalid'); }
      if (url.username || url.password || url.hash || (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))) fail('InvalidInput', 'Bundle URL requires HTTPS without credentials or fragment');
    }
  }
  private async readCache(): Promise<Bundle | undefined> {
    if (!this.options.cachePath) return undefined;
    try {
      const file = await open(this.options.cachePath, constants.O_RDONLY | constants.O_NONBLOCK);
      try {
        const metadata = await file.stat();
        if (!metadata.isFile() || metadata.size > MAX_BUNDLE_BYTES) fail('ConfigInvalid', 'Cached bundle is not a bounded regular file', 'bundle');
        const bytes = Buffer.alloc(MAX_BUNDLE_BYTES + 1); let length = 0;
        while (length < bytes.length) {
          const result = await file.read(bytes, length, bytes.length - length, null);
          if (result.bytesRead === 0) break;
          length += result.bytesRead;
        }
        if (length > MAX_BUNDLE_BYTES) fail('ConfigInvalid', 'Cached bundle exceeds its byte limit', 'bundle');
        let text: string;
        try { text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes.subarray(0, length)); }
        catch { return fail('ConfigInvalid', 'Cached bundle is not valid UTF-8', 'bundle'); }
        return verifyBundle(text, this.keys);
      } finally { await file.close(); }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
      if (error instanceof AnchorpassError) throw error;
      return fail('ConfigUnavailable', 'Signed bundle cache could not be read', 'bundle');
    }
  }
  private async persist(candidate: Bundle, signal: AbortSignal): Promise<Bundle> {
    if (!this.options.cachePath) return accept(candidate, this.current);
    const filename = this.options.cachePath;
    await mkdir(path.dirname(filename), { recursive: true });
    let lock;
    while (!lock) {
      signal.throwIfAborted();
      try { lock = await open(`${filename}.lock`, 'wx', 0o600); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') return fail('ConfigUnavailable', 'Bundle cache lock could not be acquired', 'bundle');
        await waitFor(new Promise<void>(resolve => setTimeout(resolve, 10)), signal);
      }
    }
    const temporary = `${filename}.${randomUUID()}.tmp`;
    try {
      const cached = await this.readCache();
      if (cached && (!this.current || compareRevision(cached.revision, this.current.revision) >= 0)) this.current = accept(cached, this.current);
      accept(candidate, this.current);
      signal.throwIfAborted();
      const file = await open(temporary, 'wx', 0o600);
      try { await file.writeFile(candidate.envelope); await file.sync(); } finally { await file.close(); }
      signal.throwIfAborted();
      await rename(temporary, filename);
      const directory = await open(path.dirname(filename), 'r');
      try { await directory.sync(); } finally { await directory.close(); }
      return candidate;
    } catch (error) {
      if (error instanceof AnchorpassError || signal.aborted) throw error;
      return fail('ConfigUnavailable', 'Signed bundle cache could not be persisted', 'bundle');
    } finally {
      await unlink(temporary).catch(error => { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; });
      await lock.close(); await unlink(`${filename}.lock`);
    }
  }
  private startRefresh(): Promise<Bundle> {
    if (this.closed) fail('ClientClosed', 'Bundle loader is closed', 'updates');
    if (this.refresh) return this.refresh;
    const controller = new AbortController(); this.refreshController = controller;
    const timer = setTimeout(() => controller.abort(new AnchorpassError('DeadlineExceeded', 'Bundle update deadline exceeded', 'updates')), this.updateTimeout * 1000);
    this.refresh = (async () => {
      const source = await sharedDownload(this.transport, this.options.bundleUrl!, this.updateTimeout, controller.signal);
      const candidate = verifyBundle(source, this.keys);
      this.current = accept(candidate, this.current);
      this.persistenceError = new AnchorpassError('ConfigUnavailable', 'Authenticated bundle has not been persisted', 'bundle');
      const persisted = await this.persist(candidate, controller.signal);
      this.current = accept(persisted, this.current);
      this.persistenceError = undefined;
      if (candidate.expires_at <= Date.now() / 1000) fail('ConfigExpired', 'Downloaded bundle has expired', 'bundle');
      if (this.options.pin !== undefined && candidate.revision !== this.options.pin) fail('ConfigUnavailable', 'Update did not provide the pinned revision', 'bundle');
      return this.current;
    })().finally(() => { clearTimeout(timer); this.refresh = undefined; this.refreshController = undefined; this.checkedAt = performance.now(); });
    return this.refresh;
  }
  async get(signal: AbortSignal, requiredSeconds: number, force = false): Promise<Bundle> {
    signal.throwIfAborted();
    if (this.closed) fail('ClientClosed', 'Bundle loader is closed', 'bundle');
    if (!this.current) this.current = verifyBundle(this.bundled, this.keys);
    const cached = await this.readCache();
    signal.throwIfAborted();
    if (this.closed) fail('ClientClosed', 'Bundle loader is closed', 'bundle');
    if (cached) {
      const order = compareRevision(cached.revision, this.current.revision);
      if (order >= 0) this.current = accept(cached, this.current);
      // A lower cache never reduces an in-memory high water mark.
    }
    const usable = (): boolean => Boolean(this.current && this.current.expires_at > Date.now() / 1000 + requiredSeconds && (this.options.pin === undefined || this.options.pin === this.current.revision));
    if (this.options.pin !== undefined && compareRevision(this.options.pin, this.current.revision) < 0) fail('ConfigRollback', 'Pinned revision is below the accepted high water mark', 'bundle');
    const refreshDue = force || Boolean(this.persistenceError) || (!this.options.pin && performance.now() - this.checkedAt >= this.refreshInterval * 1000) || !usable();
    if (!this.options.offline && this.options.bundleUrl && refreshDue) {
      try { await waitFor(this.startRefresh(), signal); }
      catch (error) { if (signal.aborted || this.persistenceError || !usable() || force) throw error; }
    }
    signal.throwIfAborted();
    if (this.persistenceError) throw this.persistenceError;
    if (!usable()) {
      if (this.current.expires_at <= Date.now() / 1000 + requiredSeconds) fail('ConfigExpired', 'Bundle does not cover the operation deadline', 'bundle');
      fail('ConfigUnavailable', 'Pinned signed bundle is not available', 'bundle');
    }
    return this.current;
  }
  async close(): Promise<void> {
    this.closed = true;
    this.refreshController?.abort(new AnchorpassError('ClientClosed', 'Client is closed', 'updates'));
    await this.refresh?.catch(() => undefined);
  }
}
