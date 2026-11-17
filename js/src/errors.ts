export const errorCodes = [
  'InvalidInput', 'InvalidProxy', 'UnsupportedProxy', 'ProxyConnectError', 'ProxyAuthError',
  'TransportError', 'DeadlineExceeded', 'Cancelled', 'HttpStatusError', 'ProtocolError',
  'ConfigUnavailable', 'ConfigInvalid', 'ConfigExpired', 'ConfigRollback', 'ResourceLimit',
  'WorkerUnavailable', 'ClientClosed',
] as const;
export type ErrorCode = typeof errorCodes[number];
export class AnchorpassError extends Error {
  readonly code: ErrorCode;
  readonly stage: string;
  readonly retryable: boolean;
  constructor(code: ErrorCode, message: string, stage = 'input', retryable = false) {
    super(message);
    this.name = code;
    this.code = code;
    this.stage = stage;
    this.retryable = retryable;
  }
}
export function fail(code: ErrorCode, message: string, stage?: string): never {
  throw new AnchorpassError(code, message, stage);
}
