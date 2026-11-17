import { decode } from 'html-entities';
import { isIP } from 'node:net';
import { domainToASCII } from 'node:url';
import { fail } from './errors.js';

export interface ProxyRoute {
  scheme: 'http' | 'https' | 'socks5' | 'socks5h';
  hostname: string; port: number; username: string; password: string;
}
export function parseProxy(input?: string | null): ProxyRoute | null {
  if (input === undefined || input === null) return null;
  if (typeof input !== 'string' || input.length > 4096 || /[\s\x00-\x1f\x7f\\?#]/u.test(input) || /%(?![a-fA-F0-9]{2})/.test(input)) {
    return fail('InvalidProxy', 'Proxy must be a strict absolute URL');
  }
  const match = /^(http|https|socks5|socks5h):\/\/(?:([^@]*)@)?(\[[0-9a-fA-F:.]+\]|[^:/@]+):([0-9]+)\/?$/.exec(input);
  if (!match) return fail('InvalidProxy', 'Proxy requires a supported scheme, host and explicit port');
  const port = Number(match[4]);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65535) return fail('InvalidProxy', 'Proxy port is outside its allowed range');
  let url: URL;
  try { url = new URL(input); } catch { return fail('InvalidProxy', 'Proxy URL is invalid'); }
  const auth = match[2] ?? '';
  const separator = auth.indexOf(':');
  let username: string, password: string;
  try {
    username = decodeURIComponent(separator < 0 ? auth : auth.slice(0, separator));
    password = decodeURIComponent(separator < 0 ? '' : auth.slice(separator + 1));
  } catch { return fail('InvalidProxy', 'Proxy credentials have invalid encoding'); }
  if (/[^\x20-\x7e]/.test(username + password) || username.length > 255 || password.length > 255 || username.includes(':')) {
    return fail('InvalidProxy', 'Proxy credentials must be bounded printable ASCII');
  }
  return { scheme: match[1] as ProxyRoute['scheme'], hostname: url.hostname.replace(/^\[|\]$/g, ''), port, username, password };
}
export interface Anchor {
  path: string; variant: 'api2' | 'enterprise'; query: URLSearchParams; siteKey: string; action?: string;
}
export function parseAnchor(input: string, action?: string, validateAction = true, hostname?: string): Anchor {
  if (typeof input !== 'string' || input.length > 8192 || /[\uD800-\uDFFF]/u.test(input) || !/^https:\/\/(?:www\.)?google\.com(?::443)?\/recaptcha\/(?:api2|enterprise)\/anchor\?/i.test(input) || /[\s\x00-\x1f\x7f\\#]/u.test(input) || /%(?![a-fA-F0-9]{2})/.test(input)) {
    return fail('InvalidInput', 'Anchor URL is invalid');
  }
  let url: URL;
  try { url = new URL(input); decodeURIComponent(url.search.replace(/\+/g, ' ')); }
  catch { return fail('InvalidInput', 'Anchor URL or encoding is invalid'); }
  const match = /^\/recaptcha\/(api2|enterprise)\/anchor$/.exec(url.pathname);
  if (url.protocol !== 'https:' || !['www.google.com', 'google.com'].includes(url.hostname) || (url.port && url.port !== '443') || url.username || url.password || !match) {
    return fail('InvalidInput', 'Anchor must use the supported Google HTTPS endpoint');
  }
  const seen = new Set<string>();
  for (const [key] of url.searchParams) {
    if (seen.has(key)) return fail('InvalidInput', 'Anchor query contains duplicate parameters');
    seen.add(key);
  }
  const siteKey = url.searchParams.get('k');
  if (!siteKey || !url.searchParams.get('co')) return fail('InvalidInput', 'Anchor requires nonempty k and co parameters');
  const selected = action === undefined ? url.searchParams.get('sa') ?? undefined : action;
  if (validateAction && selected !== undefined && (typeof selected !== 'string' || !selected || Buffer.byteLength(selected, 'utf8') > 256 || /[\uD800-\uDFFF]/u.test(selected))) {
    return fail('InvalidInput', 'Action is invalid');
  }
  if (selected !== undefined) url.searchParams.set('sa', selected);
  else url.searchParams.delete('sa');
  if (hostname !== undefined) url.searchParams.set('co', hostnameOrigin(url.searchParams.get('co')!, hostname));
  return { path: url.pathname, variant: match[1] as Anchor['variant'], query: url.searchParams, siteKey, action: selected };
}
function hostnameOrigin(encoded: string, hostname: unknown): string {
  if (typeof hostname !== 'string' || !hostname || /[^\x21-\x7e]|%/.test(hostname)) return fail('InvalidInput', 'hostname must be a valid ASCII domain or IP address');
  const unbracketed = hostname.startsWith('[') && hostname.endsWith(']') ? hostname.slice(1, -1) : hostname;
  let normalized: string;
  if (isIP(unbracketed) === 6) normalized = new URL(`http://[${unbracketed}]`).hostname;
  else if (isIP(hostname) === 4) normalized = hostname;
  else {
    const name = hostname.endsWith('.') ? hostname.slice(0, -1) : hostname;
    if (name.length > 253 || !name || !name.split('.').every(label => /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label)) || !domainToASCII(hostname)) return fail('InvalidInput', 'hostname must be a valid ASCII domain or IP address');
    normalized = hostname.toLowerCase();
  }
  const base64 = encoded.replace(/-/g, '+').replace(/_/g, '/').replace(/\./g, '=');
  if (!/^[A-Za-z0-9+/_-]+(?:={1,2}|\.{1,2})?$/.test(encoded) || base64.length % 4 === 1 || (/[=.]$/.test(encoded) && base64.length % 4 !== 0)) return fail('InvalidInput', 'Anchor co must encode an HTTP or HTTPS origin when hostname is supplied');
  const bytes = Buffer.from(base64, 'base64');
  if (bytes.toString('base64').replace(/=+$/, '') !== base64.replace(/=+$/, '')) return fail('InvalidInput', 'Anchor co has invalid Base64 encoding');
  let origin: string;
  try { origin = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
  catch { return fail('InvalidInput', 'Anchor co must encode a UTF-8 origin'); }
  const match = /^(https?):\/\/(\[[0-9a-fA-F:.]+\]|[^\s:/?#@\\]+)(?::([0-9]+))?\/?$/i.exec(origin);
  if (!match) return fail('InvalidInput', 'Anchor co must encode an HTTP or HTTPS origin');
  let parsed: URL;
  try { parsed = new URL(origin); }
  catch { return fail('InvalidInput', 'Anchor co contains an invalid origin'); }
  const port = match[3] === undefined ? (parsed.protocol === 'https:' ? 443 : 80) : Number(match[3]);
  if (!parsed.hostname || !Number.isSafeInteger(port) || port < 1 || port > 65535) return fail('InvalidInput', 'Anchor co contains an invalid origin port');
  return Buffer.from(`${parsed.protocol}//${normalized}:${port}`, 'utf8').toString('base64').replace(/=/g, '.');
}
export function parseVersion(source: string): string {
  const value = /\/recaptcha\/releases\/([A-Za-z0-9_-]{1,128})\//.exec(source)?.[1];
  return value ?? fail('ProtocolError', 'Version was absent from the API script', 'version');
}
function tagEnd(source: string, start: number): number {
  let quote = '';
  for (let index = start; index < source.length; index++) {
    const char = source[index]!;
    if (quote) { if (char === quote) quote = ''; }
    else if (char === '"' || char === "'") quote = char;
    else if (char === '>') return index;
  }
  return -1;
}
export function parseAnchorToken(source: string): string {
  let position = 0;
  const tagName = /<(\/?)([A-Za-z][^\t\n\f\r />]*)/y;
  while (position < source.length) {
    const start = source.indexOf('<', position);
    if (start < 0) break;
    if (source.startsWith('<!--', start)) {
      const ending = /--!?>/g; ending.lastIndex = start + 4;
      const comment = ending.exec(source);
      if (!comment) break;
      position = ending.lastIndex; continue;
    }
    tagName.lastIndex = start;
    const tag = tagName.exec(source);
    if (!tag) {
      const declaration = source[start + 1] === '!' || source[start + 1] === '?';
      const end = declaration ? tagEnd(source, start + 2) : start;
      if (end < 0) break;
      position = end + 1; continue;
    }
    const end = tagEnd(source, tagName.lastIndex);
    if (end < 0) break;
    position = end + 1;
    if (tag[1]) continue;
    const name = tag[2]!.toLowerCase();
    if (name === 'plaintext') break;
    if (['script', 'style', 'textarea', 'title', 'xmp', 'iframe', 'noembed', 'noframes'].includes(name)) {
      const closing = new RegExp(`</${name}(?=[\\t\\n\\f\\r />])`, 'gi'); closing.lastIndex = position;
      const match = closing.exec(source);
      if (!match) break;
      position = match.index; continue;
    }
    if (name !== 'input') continue;
    const attrs = new Map<string, string>();
    const attributes = source.slice(tagName.lastIndex, end);
    for (const match of attributes.matchAll(/([^\t\n\f\r "'<>/=]+)(?:[\t\n\f\r ]*=[\t\n\f\r ]*(?:"([^"]*)"|'([^']*)'|([^\t\n\f\r >]+)))?/g)) {
      const attribute = match[1]!.toLowerCase();
      if (!attrs.has(attribute)) attrs.set(attribute, decode(match[2] ?? match[3] ?? match[4] ?? '', { level: 'html5', scope: 'attribute' }));
    }
    const token = attrs.get('value');
    if ((attrs.get('id') === 'recaptcha-token' || attrs.get('name') === 'recaptcha-token') && token && token.length <= 32768) return token;
  }
  return fail('ProtocolError', 'Anchor token was absent or too large', 'anchor');
}
export function parseReload(source: string): string {
  const clean = source.startsWith(")]}'") ? source.slice(4).trimStart() : source;
  let value: unknown;
  try { value = JSON.parse(clean); } catch { return fail('ProtocolError', 'Reload response was not JSON', 'reload'); }
  if (!Array.isArray(value) || value[0] !== 'rresp' || typeof value[1] !== 'string' || !value[1] || value[1].length > 32768) {
    return fail('ProtocolError', 'Reload response did not contain a bounded token', 'reload');
  }
  return value[1];
}

export function validateBg(bg: unknown): asserts bg is string | undefined {
  if (bg !== undefined && (typeof bg !== 'string' || !bg || Buffer.byteLength(bg, 'utf8') > 16384 || /[\uD800-\uDFFF]/u.test(bg))) fail('InvalidInput', 'bg must be a nonempty string of at most 16384 UTF-8 bytes');
}
export function validateUserAgent(userAgent: unknown): asserts userAgent is string | undefined {
  if (userAgent !== undefined && (typeof userAgent !== 'string' || !userAgent.trim() || userAgent.length > 1024 || /[^\x20-\x7e]/.test(userAgent))) fail('InvalidInput', 'userAgent must be a nonempty printable ASCII string of at most 1024 bytes');
}
export function extractAction(anchorUrl: string): string | null { return parseAnchor(anchorUrl, undefined, false).action ?? null; }
