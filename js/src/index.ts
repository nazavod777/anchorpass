import { Client as InternalClient, type ClientOptions } from './client.js';
import { SyncClient as InternalSyncClient } from './sync.js';
export class Client extends InternalClient {
  constructor(options: ClientOptions = {}) { super(options); }
}
export class SyncClient extends InternalSyncClient {
  constructor(options: ClientOptions = {}) { super(options); }
}
export { solve, loadBg, type ClientOptions, type SolveOptions, type SolveResult } from './client.js';
export { solveSync, loadBgSync } from './sync.js';
export { AnchorpassError, type ErrorCode } from './errors.js';
export { extractAction } from './core.js';
