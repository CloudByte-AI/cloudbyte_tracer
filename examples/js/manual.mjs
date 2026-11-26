import { init } from 'cloudbyte-tracer';
const tracer = init({ serviceName: 'example-agent' });
try {
  await tracer.withSpan(
    'handle_request',
    { kind: 'agent', input: { question: 'hello' } },
    async (root) => {
      await tracer.withSpan('lookup', { kind: 'tool' }, (step) =>
        step.setOutput({ answer: 'Hello from the manual example' }),
      );
      root.setOutput('Hello from the manual example');
    },
  );
} finally {
  await tracer.shutdown();
}
console.log('Manual example finished');
