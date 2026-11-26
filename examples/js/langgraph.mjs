import { init } from 'cloudbyte-tracer';
import OpenAI from 'openai';
import * as callbacks from '@langchain/core/callbacks/manager';
import { StateGraph, START, END, Annotation } from '@langchain/langgraph';
import { openaiResponse, mockFetch } from './mock.mjs';
const tracer = init({
  autoCapture: ['openai', 'langchain'],
  modules: { openai: OpenAI, langchain: callbacks },
});
const client = new OpenAI({ apiKey: 'mock-provider-key', fetch: mockFetch(openaiResponse) });
const State = Annotation.Root({ result: Annotation() });
const graph = new StateGraph(State)
  .addNode('respond', async () => ({
    result: (
      await client.chat.completions.create({
        model: 'test-model',
        messages: [{ role: 'user', content: 'hi' }],
      })
    ).choices[0].message.content,
  }))
  .addEdge(START, 'respond')
  .addEdge('respond', END)
  .compile();
try {
  await tracer.withSpan('request', { kind: 'agent' }, async () =>
    console.log((await graph.invoke({ result: '' })).result),
  );
} finally {
  await tracer.shutdown();
}
