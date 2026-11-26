import { test } from 'node:test';
import assert from 'node:assert/strict';
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';
import * as CallbackManagerModule from '@langchain/core/callbacks/manager';
import { StateGraph, START, END, Annotation } from '@langchain/langgraph';
import { trace } from '@opentelemetry/api';
import { Tracer } from '../src/core.js';
import { fakeServer, KEY, final } from './helpers.js';
const openaiResponse = {
  id: 'chatcmpl-test',
  object: 'chat.completion',
  created: 1,
  model: 'test-model',
  choices: [{ index: 0, message: { role: 'assistant', content: 'hello' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
};
const anthropicResponse = {
  id: 'msg_test',
  type: 'message',
  role: 'assistant',
  model: 'test-model',
  content: [{ type: 'text', text: 'hello' }],
  stop_reason: 'end_turn',
  stop_sequence: null,
  usage: { input_tokens: 7, output_tokens: 3 },
};
const modules = { openai: OpenAI, anthropic: Anthropic, langchain: CallbackManagerModule };
function providerFetch(kind: string, streaming: boolean): typeof fetch {
  return (async () => {
    if (!streaming)
      return new Response(JSON.stringify(kind === 'openai' ? openaiResponse : anthropicResponse), {
        headers: { 'content-type': 'application/json' },
      });
    if (kind === 'openai') {
      const chunks = [
        {
          id: 'stream',
          object: 'chat.completion.chunk',
          created: 1,
          model: 'test-model',
          choices: [
            { index: 0, delta: { role: 'assistant', content: 'hello' }, finish_reason: null },
          ],
        },
        {
          id: 'stream',
          object: 'chat.completion.chunk',
          created: 1,
          model: 'test-model',
          choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
          usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
        },
      ];
      return new Response(
        chunks.map((c) => 'data: ' + JSON.stringify(c) + '\n\n').join('') + 'data: [DONE]\n\n',
        { headers: { 'content-type': 'text/event-stream' } },
      );
    }
    const chunks = [
      [
        'message_start',
        {
          type: 'message_start',
          message: {
            ...anthropicResponse,
            content: [],
            usage: { input_tokens: 7, output_tokens: 0 },
          },
        },
      ],
      [
        'content_block_start',
        { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
      ],
      [
        'content_block_delta',
        { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'hello' } },
      ],
      ['content_block_stop', { type: 'content_block_stop', index: 0 }],
      [
        'message_delta',
        {
          type: 'message_delta',
          delta: { stop_reason: 'end_turn', stop_sequence: null },
          usage: { output_tokens: 3 },
        },
      ],
      ['message_stop', { type: 'message_stop' }],
    ];
    return new Response(
      chunks.map(([e, c]) => 'event: ' + e + '\ndata: ' + JSON.stringify(c) + '\n\n').join(''),
      { headers: { 'content-type': 'text/event-stream' } },
    );
  }) as typeof fetch;
}
for (const kind of ['openai', 'anthropic'] as const)
  for (const streaming of [false, true])
    test(
      kind + ' ' + (streaming ? 'stream' : 'completion') + ' preserves manual parent and usage',
      async () => {
        const server = await fakeServer(),
          t = new Tracer({ apiKey: KEY, baseUrl: server.url, autoCapture: [kind], modules });
        try {
          assert.ok(t.bridge, 'optional integration loaded');
          const client =
            kind === 'openai'
              ? new OpenAI({
                  apiKey: 'mock-provider-key',
                  fetch: providerFetch(kind, streaming),
                  maxRetries: 0,
                })
              : new Anthropic({
                  apiKey: 'mock-provider-key',
                  fetch: providerFetch(kind, streaming),
                  maxRetries: 0,
                });
          await t.withSpan('manual', { kind: 'agent' }, async (_root) => {
            const result =
              kind === 'openai'
                ? await (client as OpenAI).chat.completions.create({
                    model: 'test-model',
                    messages: [{ role: 'user', content: 'hi' }],
                    stream: streaming,
                  })
                : await (client as Anthropic).messages.create({
                    model: 'test-model',
                    max_tokens: 10,
                    messages: [{ role: 'user', content: 'hi' }],
                    stream: streaming,
                  });
            if (streaming) {
              const chunks = [];
              for await (const chunk of result as any) chunks.push(chunk);
              assert.ok(chunks.length);
            }
          });
          await t.flush();
          const root = final(server.spans(), 'manual'),
            llms = server.spans().filter((x) => x.kind === 'llm' && x.ended_at);
          assert.equal(llms.length, 1, JSON.stringify(server.spans()));
          const span = llms[0];
          assert.equal(span.parent_span_id, root.span_id);
          assert.equal(span.trace_id, root.trace_id);
          assert.equal(span.model, 'test-model');
          assert.equal(span.usage?.input_tokens, 7);
          assert.equal(span.usage?.output_tokens, 3);
          assert.ok(JSON.stringify(span.output).includes('hello'));
        } finally {
          await t.shutdown();
          await server.close();
        }
      },
    );
test('manual to LangGraph to OpenAI forms one trace tree', async () => {
  const s = await fakeServer(),
    before = trace.getTracerProvider(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, autoCapture: ['langchain', 'openai'], modules });
  try {
    assert.equal(trace.getTracerProvider(), before);
    const client = new OpenAI({
      apiKey: 'mock-provider-key',
      fetch: providerFetch('openai', false),
      maxRetries: 0,
    });
    const State = Annotation.Root({ result: Annotation<string> });
    const graph = new StateGraph(State)
      .addNode('respond', async () => {
        const answer = await client.chat.completions.create({
          model: 'test-model',
          messages: [{ role: 'user', content: 'hi' }],
        });
        return { result: answer.choices[0].message.content! };
      })
      .addEdge(START, 'respond')
      .addEdge('respond', END)
      .compile();
    await t.withSpan('manual', { kind: 'agent' }, async () => {
      assert.equal((await graph.invoke({ result: '' })).result, 'hello');
    });
    await t.flush();
    const spans = s.spans().filter((x) => x.ended_at),
      root = final(s.spans(), 'manual');
    assert.ok(spans.length >= 4, JSON.stringify(spans));
    assert.deepEqual(new Set(spans.map((x) => x.trace_id)), new Set([root.trace_id]));
    const llm = spans.find((x) => x.kind === 'llm')!;
    assert.equal(spans.find((x) => x.span_id === llm.parent_span_id)?.name, 'respond');
    const chain = spans.find((x) => x.name === 'LangGraph')!;
    assert.equal(chain.parent_span_id, root.span_id);
    assert.equal(chain.kind, 'custom');
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('content disabled suppresses provider input/output', async () => {
  const s = await fakeServer(),
    t = new Tracer({
      apiKey: KEY,
      baseUrl: s.url,
      autoCapture: ['openai'],
      modules,
      captureContent: false,
    });
  try {
    const client = new OpenAI({
      apiKey: 'mock-provider-key',
      fetch: providerFetch('openai', false),
      maxRetries: 0,
    });
    await t.withSpan('manual', { input: 'SECRET' }, async () => {
      await client.chat.completions.create({
        model: 'test-model',
        messages: [{ role: 'user', content: 'SECRET' }],
      });
    });
    await t.flush();
    assert.ok(!JSON.stringify(s.spans()).includes('SECRET'));
    assert.ok(s.spans().every((x) => x.input === undefined && x.output === undefined));
  } finally {
    await t.shutdown();
    await s.close();
  }
});

test('shared v2 provider factory stays usable after SDK shutdown', async () => {
  const { BasicTracerProvider } = await import('@opentelemetry/sdk-trace-base');
  const s = await fakeServer();
  let provider: any;
  const t = new Tracer({
    apiKey: KEY,
    baseUrl: s.url,
    tracerProvider: (processor: any) =>
      (provider = new BasicTracerProvider({ spanProcessors: [processor] })),
  });
  try {
    await t.withSpan('shared', {}, () => 42);
    await t.flush();
    assert.equal(s.spans().length, 2);
    await t.shutdown();
    const span = provider.getTracer('app').startSpan('after');
    assert.ok(span.isRecording());
    span.end();
  } finally {
    await t.shutdown();
    await provider.shutdown();
    await s.close();
  }
});
test('instrumentation start failure cannot prevent application work', async () => {
  const s = await fakeServer(),
    t = new Tracer({
      apiKey: KEY,
      baseUrl: s.url,
      tracerProvider: () => ({
        getTracer: () => ({
          startSpan: () => {
            throw Error('broken tracer');
          },
        }),
      }),
    });
  try {
    assert.equal(
      t.withSpan('work', {}, () => 42),
      42,
    );
    await t.flush();
    assert.equal(final(s.spans(), 'work').status, 'ok');
  } finally {
    await t.shutdown();
    await s.close();
  }
});
test('root CHAIN maps to agent and manual descendants stay linked', async () => {
  const s = await fakeServer(),
    t = new Tracer({ apiKey: KEY, baseUrl: s.url, autoCapture: ['langchain'], modules });
  try {
    await t.bridge!.tracer.startActiveSpan(
      'chain',
      { attributes: { 'openinference.span.kind': 'CHAIN' } },
      async (span: any) => {
        try {
          await t.withSpan('manual-tool', { kind: 'tool' }, () => {});
        } finally {
          span.end();
        }
      },
    );
    await t.flush();
    const chain = final(s.spans(), 'chain');
    assert.equal(chain.kind, 'agent');
    assert.equal(final(s.spans(), 'manual-tool').parent_span_id, chain.span_id);
  } finally {
    await t.shutdown();
    await s.close();
  }
});
