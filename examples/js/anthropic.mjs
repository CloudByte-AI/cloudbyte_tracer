import { init } from 'cloudbyte-tracer';
import Anthropic from '@anthropic-ai/sdk';
import { anthropicResponse, mockFetch } from './mock.mjs';
const tracer = init({ autoCapture: ['anthropic'], modules: { anthropic: Anthropic } });
const client = new Anthropic({ apiKey: 'mock-provider-key', fetch: mockFetch(anthropicResponse) });
try {
  await tracer.withSpan('chat', { kind: 'agent' }, async () => {
    console.log(
      (
        await client.messages.create({
          model: 'test-model',
          max_tokens: 10,
          messages: [{ role: 'user', content: 'hi' }],
        })
      ).content[0].text,
    );
  });
} finally {
  await tracer.shutdown();
}
