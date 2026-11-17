import { fail } from './errors.js';

/** JSON.parse accepts duplicate members. Signed data deliberately uses a stricter grammar. */
export function strictJson(bytes: Uint8Array): unknown {
  let source: string;
  try { source = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes); }
  catch { return fail('ConfigInvalid', 'Bundle is not valid UTF-8', 'bundle'); }
  let position = 0;
  let nodes = 0;
  const invalid = (): never => fail('ConfigInvalid', 'Invalid or ambiguous JSON', 'bundle');
  const whitespace = (): void => { while (/[\x20\t\r\n]/.test(source[position] ?? '\x01')) position++; };
  function string(): string {
    const start = position++;
    while (position < source.length) {
      const c = source[position++];
      if (c === '\\') { position++; continue; }
      if (c === '"') {
        try {
          const value = JSON.parse(source.slice(start, position)) as string;
          if (/[\uD800-\uDFFF]/u.test(value)) invalid();
          return value;
        } catch { return invalid(); }
      }
    }
    return invalid();
  }
  function value(depth: number): unknown {
    if (++nodes > 10000 || depth > 16) return invalid();
    whitespace();
    const c = source[position];
    if (c === '"') return string();
    if (c === '{') {
      position++;
      const object: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
      const keys = new Set<string>();
      whitespace();
      if (source[position] === '}') { position++; return object; }
      while (position < source.length) {
        whitespace();
        if (source[position] !== '"') return invalid();
        const key = string();
        if (keys.has(key)) return invalid();
        keys.add(key);
        whitespace();
        if (source[position++] !== ':') return invalid();
        object[key] = value(depth + 1);
        whitespace();
        const end = source[position++];
        if (end === '}') return object;
        if (end !== ',') return invalid();
      }
      return invalid();
    }
    if (c === '[') {
      position++;
      const array: unknown[] = [];
      whitespace();
      if (source[position] === ']') { position++; return array; }
      while (position < source.length) {
        array.push(value(depth + 1)); whitespace();
        const end = source[position++];
        if (end === ']') return array;
        if (end !== ',') return invalid();
      }
      return invalid();
    }
    const token = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(source.slice(position));
    if (!token) return invalid();
    position += token[0].length;
    const result: unknown = JSON.parse(token[0]);
    if (typeof result === 'number' && !Number.isFinite(result)) return invalid();
    return result;
  }
  const result = value(0);
  whitespace();
  if (position !== source.length) return invalid();
  return result;
}
export function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
