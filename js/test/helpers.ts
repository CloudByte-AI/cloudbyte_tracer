import { createServer } from 'node:http';
import type { WireSpan } from '../src/types.js';
export const KEY = 'cbagt_' + 'T'.repeat(32);
export async function fakeServer() {
  const bodies: WireSpan[][] = [],
    headers: any[] = [];
  let status = 200,
    retry = '0',
    reply: unknown;
  const server = createServer((req, res) => {
    const parts: Buffer[] = [];
    req.on('data', (c) => parts.push(c));
    req.on('end', () => {
      const body = JSON.parse(Buffer.concat(parts).toString());
      bodies.push(body.spans);
      headers.push(req.headers);
      res.writeHead(status, { 'content-type': 'application/json', 'retry-after': retry });
      res.end(
        JSON.stringify(
          reply ??
            (status === 200
              ? { accepted: body.spans.length, rejected: [] }
              : { error: 'do not log sensitive body' }),
        ),
      );
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as { port: number };
  return {
    url: 'http://127.0.0.1:' + address.port,
    bodies,
    headers,
    setStatus: (code: number) => {
      status = code;
    },
    setRetry: (value: string) => {
      retry = value;
    },
    setReply: (value: unknown) => {
      reply = value;
    },
    spans: () => bodies.flat(),
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections();
      }),
  };
}
export const final = (spans: WireSpan[], name: string) => {
  const found = spans.filter((s) => s.name === name && s.ended_at);
  if (found.length !== 1) throw Error('Expected one final ' + name + ', got ' + found.length);
  return found[0];
};
