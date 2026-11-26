import { init } from 'cloudbyte-tracer';
import OpenAI from 'openai';
import { openaiResponse, mockFetch } from './mock.mjs';
const tracer = init({ autoCapture: ['openai'], modules: { openai: OpenAI } });
const client = new OpenAI({ apiKey: 'mock-provider-key', fetch: mockFetch(openaiResponse) });
try {
  await tracer.withSpan('chat', { kind: 'agent' }, async () => {
    console.log(
      (
        await client.chat.completions.create({
          model: 'test-model',
          messages: [{ role: 'user', content: 'hi' }],
        })
      ).choices[0].message.content,
    );
  });
} finally {
  await tracer.shutdown();
}
