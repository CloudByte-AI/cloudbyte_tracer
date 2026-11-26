import type { WireSpan, Kind } from '../types.js';
const kinds: Record<string, Kind> = {
  LLM: 'llm',
  TOOL: 'tool',
  RETRIEVER: 'retrieval',
  RERANKER: 'retrieval',
  AGENT: 'agent',
};
const usageMap: Record<string, string> = {
  prompt: 'input_tokens',
  completion: 'output_tokens',
  'prompt_details.cache_read': 'cache_read_tokens',
  'prompt_details.cache_write': 'cache_write_tokens',
};
const timestamp = (t: [number, number]) => new Date(t[0] * 1000 + t[1] / 1e6).toISOString();
function content(attrs: Record<string, any>, direction: string): unknown {
  const items = new Map<number, any>();
  const pattern = new RegExp(
    '^llm\\.' +
      direction +
      '_messages\\.(\\d+)\\.message\\.(role|content|contents\\.(\\d+)\\.message_content\\.text)$',
  );
  for (const [key, value] of Object.entries(attrs)) {
    const m = pattern.exec(key);
    if (!m) continue;
    const index = Number(m[1]),
      item = items.get(index) ?? {};
    items.set(index, item);
    if (m[2] === 'role' || m[2] === 'content') item[m[2]] = value;
    else (item.parts ??= {})[m[3]] = value;
  }
  if (items.size)
    return [...items.entries()]
      .sort(([a], [b]) => a - b)
      .map(([, item]) => {
        if (item.parts) {
          item.content ??= Object.keys(item.parts)
            .map(Number)
            .sort((a, b) => a - b)
            .map((k) => item.parts[k])
            .join('');
          delete item.parts;
        }
        return item;
      });
  const raw = attrs[direction + '.value'];
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}
export function mapSpan(span: any, start = false): WireSpan {
  const attrs = span.attributes ?? {},
    ctx = span.spanContext();
  const parent = span.parentSpanContext ?? span.parentSpanId;
  const parentId = typeof parent === 'string' ? parent : (parent?.spanId ?? null);
  const oiKind = String(attrs['openinference.span.kind'] ?? '').toUpperCase();
  const result: WireSpan = {
    trace_id: ctx.traceId,
    span_id: ctx.spanId,
    parent_span_id: parentId,
    name: span.name,
    kind: kinds[oiKind] ?? (oiKind === 'CHAIN' && !parentId ? 'agent' : 'custom'),
    started_at: timestamp(span.startTime),
    ended_at: start ? null : timestamp(span.endTime),
  };
  if (!start) {
    result.status = span.status.code === 2 ? 'error' : span.status.code === 0 ? 'unset' : 'ok';
    for (const event of span.events ?? [])
      if (event.name === 'exception') {
        result.error = {
          type: String(event.attributes?.['exception.type'] ?? 'Error'),
          message: String(event.attributes?.['exception.message'] ?? ''),
        };
        result.status = 'error';
      }
    if (result.status === 'error' && !result.error)
      result.error = { type: 'Error', message: span.status.message ?? '' };
    result.usage = {};
    for (const [src, dest] of Object.entries(usageMap))
      if (attrs['llm.token_count.' + src] !== undefined)
        result.usage[dest] = attrs['llm.token_count.' + src];
  }
  for (const dir of (start ? ['input'] : ['input', 'output']) as Array<'input' | 'output'>) {
    const value = content(attrs, dir);
    if (value !== undefined) result[dir] = value;
  }
  if (attrs['llm.model_name'] !== undefined) result.model = attrs['llm.model_name'];
  const exclude = [
    'input.',
    'output.',
    'llm.input_messages.',
    'llm.output_messages.',
    'llm.tools.',
    'llm.token_count.',
    'retrieval.documents.',
    'embedding.',
    'llm.invocation_parameters',
    'tool.parameters',
    'llm.function_call',
    'llm.prompt_template.',
    'gen_ai.input.',
    'gen_ai.output.',
    'gen_ai.prompt',
    'gen_ai.completion',
  ];
  result.attributes = Object.fromEntries(
    Object.entries(attrs).filter(
      ([k]) =>
        !['openinference.span.kind', 'llm.model_name'].includes(k) &&
        !exclude.some((prefix) => k.startsWith(prefix)),
    ),
  );
  return result;
}
