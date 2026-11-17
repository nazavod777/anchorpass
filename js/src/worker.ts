import { parentPort, workerData } from 'node:worker_threads';
import { Client, type ClientOptions, type PrivateOptions } from './client.js';
import { AnchorpassError } from './errors.js';

const MAX_RESULT_BYTES = 65536;
const data = workerData as { options: ClientOptions; privateOptions: PrivateOptions; handshake: SharedArrayBuffer; lifecycle: SharedArrayBuffer };
process.on('exit', () => { const lifecycle = new Int32Array(data.lifecycle); Atomics.store(lifecycle, 0, 1); Atomics.notify(lifecycle, 0); });
function publish(shared: SharedArrayBuffer, status: number, result: unknown): void {
  const control = new Int32Array(shared, 0, 2);
  let bytes = Buffer.from(JSON.stringify(result));
  if (bytes.length > MAX_RESULT_BYTES) {
    status = 2; bytes = Buffer.from(JSON.stringify({ code: 'ResourceLimit', message: 'Worker result exceeds its byte limit', stage: 'worker' }));
  }
  // Cancellation owns terminal state 3. Late completion cannot replace it.
  if (Atomics.load(control, 0) !== 0) return;
  new Uint8Array(shared, 8, bytes.length).set(bytes); Atomics.store(control, 1, bytes.length);
  Atomics.compareExchange(control, 0, 0, status); Atomics.notify(control, 0);
}
function errorData(error: unknown): object {
  return error instanceof AnchorpassError ? { code: error.code, message: error.message, stage: error.stage } : { code: 'WorkerUnavailable', message: 'Worker operation failed', stage: 'worker' };
}
let client: Client;
try {
  if (data.privateOptions.failStartup) throw new Error('Synthetic startup failure');
  client = new Client(data.options, data.privateOptions);
  publish(data.handshake, 1, null);
} catch (error) { publish(data.handshake, 2, errorData(error)); parentPort?.close(); }
let running = false;
parentPort?.on('message', (message: { kind: 'solve' | 'loadBg' | 'close'; anchorUrl: string; action?: string; bg?: string; hostname?: string; userAgent?: string; shared: SharedArrayBuffer; timeout: number }) => {
  if (message.kind === 'close') { void client.closeAsync().finally(() => parentPort?.close()); return; }
  if (running) { publish(message.shared, 2, { code: 'ResourceLimit', message: 'Worker already has an operation', stage: 'worker' }); return; }
  if (data.privateOptions.crashWorker) process.exit(17);
  if (data.privateOptions.hangWorker) return;
  running = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), message.timeout);
  void (message.kind === 'loadBg' ? client.loadBg() : client.solve(message.anchorUrl, { action: message.action, bg: message.bg, hostname: message.hostname, userAgent: message.userAgent, signal: controller.signal }))
    .then(result => publish(message.shared, 1, result), error => publish(message.shared, 2, controller.signal.aborted ? { code: 'DeadlineExceeded', message: 'Sync operation deadline exceeded', stage: 'worker' } : errorData(error)))
    .finally(() => { clearTimeout(timer); running = false; });
});
