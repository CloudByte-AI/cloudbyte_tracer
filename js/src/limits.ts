import type { TracerOptions, WireSpan } from './types.js';
export const MAX_FIELD_BYTES = 65536,
  MAX_ATTRIBUTES_BYTES = 32768;
export const text = (v: unknown): string => {
  try {
    return String(v);
  } catch {
    return '<unprintable>';
  }
};
export const clampText = (v: unknown, max = 200) => Array.from(text(v)).slice(0, max).join('');
const safeJSON = (v: unknown): string =>
  JSON.stringify(v, (_k, value) => (typeof value === 'bigint' ? String(value) : value));
export function fit(value: unknown): [unknown, boolean] {
  let serialized: string;
  try {
    serialized = safeJSON(value);
    if (serialized === undefined) serialized = text(value);
  } catch {
    serialized = text(value);
  }
  if (Buffer.byteLength(serialized) <= MAX_FIELD_BYTES) {
    try {
      return [JSON.parse(serialized), false];
    } catch {
      return [serialized, false];
    }
  }
  // Bounded prefix above 64 KB lets the unchanged server set its truncation flag.
  let bytes = Buffer.from(serialized.slice(0, 140000));
  if (bytes.length > 70000) {
    let end = 70000;
    while (end && (bytes[end] & 0xc0) === 0x80) end--;
    bytes = bytes.subarray(0, end);
  }
  return [bytes.toString('utf8'), true];
}
export function encode(span: WireSpan, options: TracerOptions): string | undefined {
  try {
    const copy: WireSpan = { ...span };
    const redact = (value: unknown, field: string) => {
      try {
        return options.redact ? options.redact(value, field, span) : value;
      } catch {
        return undefined;
      }
    };
    for (const field of ['input', 'output'] as const) {
      const value = options.captureContent === false ? undefined : redact(copy[field], field);
      delete copy[field];
      if (value !== undefined && value !== null) {
        const [fitted, truncated] = fit(value);
        copy[field] = fitted;
        if (truncated) copy[field === 'input' ? 'input_truncated' : 'output_truncated'] = true;
      }
    }
    const attrs: Record<string, string | number | boolean> = Object.create(null);
    for (const [k, v] of Object.entries({
      ...copy.attributes,
      ...(options.serviceName ? { 'service.name': options.serviceName } : {}),
    })) {
      const value = typeof v === 'string' ? redact(v, 'attributes.' + k) : v;
      if (
        typeof value === 'string' ||
        typeof value === 'boolean' ||
        (typeof value === 'number' && Number.isFinite(value))
      )
        attrs[k] = value;
    }
    for (const key of Object.keys(attrs).sort(
      (a, b) =>
        Buffer.byteLength(JSON.stringify({ [b]: attrs[b] })) -
        Buffer.byteLength(JSON.stringify({ [a]: attrs[a] })),
    )) {
      if (Buffer.byteLength(JSON.stringify(attrs)) <= MAX_ATTRIBUTES_BYTES) break;
      delete attrs[key];
    }
    if (Object.keys(attrs).length) copy.attributes = attrs;
    else delete copy.attributes;
    if (copy.error)
      copy.error = { type: text(copy.error.type), message: clampText(copy.error.message, 4096) };
    const usage: Record<string, number> = {};
    for (const [k, v] of Object.entries(copy.usage ?? {}))
      if (
        ['input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'].includes(k) &&
        Number.isInteger(v) &&
        v >= 0
      )
        usage[k] = Math.min(v, 2147483647);
    if (Object.keys(usage).length) copy.usage = usage;
    else delete copy.usage;
    copy.name = clampText(copy.name) || 'span';
    if (copy.model !== undefined) copy.model = clampText(copy.model);
    return JSON.stringify(copy);
  } catch {
    return undefined;
  }
}
