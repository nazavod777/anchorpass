import net from 'node:net';
import { Headers, getSetCookies } from 'undici';
import { fail } from './errors.js';

const MAX_COOKIES = 64;
const MAX_COOKIE_BYTES = 16384;
const MAX_ATTRIBUTE_BYTES = 1024;
const MAX_COOKIE_AGE_SECONDS = 400 * 24 * 60 * 60;
interface StoredCookie {
  name: string; value: string; domain: string; hostOnly: boolean; path: string;
  secure: boolean; expires: number; created: number;
}
function domainMatches(host: string, domain: string): boolean {
  return host === domain || (!net.isIP(host) && domain.includes('.') && host.endsWith(`.${domain}`));
}
function defaultPath(path: string): string { return path.slice(0, path.lastIndexOf('/')) || '/'; }
function pathMatches(path: string, cookiePath: string): boolean {
  return path === cookiePath || (path.startsWith(cookiePath) && (cookiePath.endsWith('/') || path[cookiePath.length] === '/'));
}
export class OperationCookies {
  private readonly cookies = new Map<string, StoredCookie>();
  private receivedCount = 0;
  private receivedBytes = 0;
  receive(values: string | string[] | undefined, url: string, stage: string): void {
    const headers = values === undefined ? [] : typeof values === 'string' ? [values] : values;
    this.receivedCount += headers.length;
    this.receivedBytes += headers.reduce((total, value) => total + Buffer.byteLength(value), 0);
    if (this.receivedCount > MAX_COOKIES || this.receivedBytes > MAX_COOKIE_BYTES) fail('ResourceLimit', 'Response cookies exceed operation limits', stage);
    const target = new URL(url); const host = target.hostname; const now = Date.now();
    this.expire(now);
    for (const value of headers) {
      const parsed = getSetCookies(new Headers([['set-cookie', value]]))[0];
      if (!parsed || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(parsed.name) || /[\x00-\x20\x7f;,]/.test(parsed.value)) continue;
      const hostOnly = !parsed.domain; const domain = parsed.domain || host;
      if (!domainMatches(host, domain)) continue;
      let path = defaultPath(target.pathname);
      let maxAge: number | undefined;
      // Undici parses the cookie; the request supplies its default path and negative Max-Age.
      for (const attribute of value.split(';').slice(1)) {
        const separator = attribute.indexOf('=');
        const name = (separator < 0 ? attribute : attribute.slice(0, separator)).trim().toLowerCase();
        const content = (separator < 0 ? '' : attribute.slice(separator + 1)).trim();
        if (Buffer.byteLength(content) > MAX_ATTRIBUTE_BYTES) continue;
        if (name === 'path') path = content.startsWith('/') ? content : defaultPath(target.pathname);
        if (name === 'max-age' && /^-?[0-9]+$/.test(content)) maxAge = Math.min(Number(content), MAX_COOKIE_AGE_SECONDS);
      }
      const expires = maxAge !== undefined ? now + maxAge * 1000 : parsed.expires instanceof Date && Number.isFinite(parsed.expires.getTime()) ? parsed.expires.getTime() : Infinity;
      const key = JSON.stringify([parsed.name, domain, path]);
      if (expires <= now) { this.cookies.delete(key); continue; }
      const created = this.cookies.get(key)?.created ?? this.receivedCount - headers.length + headers.indexOf(value);
      this.cookies.set(key, { name: parsed.name, value: parsed.value, domain, hostOnly, path, secure: parsed.secure ?? false, expires, created });
    }
  }
  header(url: string, stage: string): string | undefined {
    const target = new URL(url); this.expire(Date.now());
    const matching = [...this.cookies.values()].filter(cookie =>
      (cookie.hostOnly ? target.hostname === cookie.domain : domainMatches(target.hostname, cookie.domain)) &&
      pathMatches(target.pathname, cookie.path) && (!cookie.secure || target.protocol === 'https:'));
    matching.sort((left, right) => right.path.length - left.path.length || left.created - right.created);
    const value = matching.map(cookie => `${cookie.name}=${cookie.value}`).join('; ');
    if (Buffer.byteLength(value) > MAX_COOKIE_BYTES) fail('ResourceLimit', 'Cookie header exceeds its byte limit', stage);
    return value || undefined;
  }
  private expire(now: number): void {
    for (const [key, cookie] of this.cookies) if (cookie.expires <= now) this.cookies.delete(key);
  }
}
