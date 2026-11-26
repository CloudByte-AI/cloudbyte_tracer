export const openaiResponse = {
  id: 'chatcmpl-test',
  object: 'chat.completion',
  created: 1,
  model: 'test-model',
  choices: [{ index: 0, message: { role: 'assistant', content: 'hello' }, finish_reason: 'stop' }],
  usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
};
export const anthropicResponse = {
  id: 'msg_test',
  type: 'message',
  role: 'assistant',
  model: 'test-model',
  content: [{ type: 'text', text: 'hello' }],
  stop_reason: 'end_turn',
  stop_sequence: null,
  usage: { input_tokens: 7, output_tokens: 3 },
};
export const mockFetch = (response) => async () =>
  new Response(JSON.stringify(response), { headers: { 'content-type': 'application/json' } });
