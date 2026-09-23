import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import test from 'node:test';
import {evaluateJev, ticketRequest} from '../scripts/jev_handler.mjs';

test('passes state and questions through, retaining answers and token usage', async () => {
  const result = {answers: {urgent: {type: 'boolean', probability: 0.7}},
    usage: {inputTokens: 100, totalTokens: 100}};
  let calls = 0;
  assert.equal(await evaluateJev(ticketRequest, {evaluate: async request => {
    calls++;
    assert.equal(request.model, 'typesafe-ai/jev');
    assert.equal(request.state, ticketRequest.state);
    assert.equal(request.questions, ticketRequest.questions);
    assert.equal(request.maxRetries, 0);
    assert.ok(request.abortSignal instanceof AbortSignal);
    return result;
  }}), result);
  assert.equal(calls, 1);
});

test('propagates provider failures without retrying', async () => {
  let calls = 0;
  await assert.rejects(evaluateJev(ticketRequest, {evaluate: async () => {
    calls++;
    throw new Error('provider failed');
  }}), /provider failed/);
  assert.equal(calls, 1);
});

test('deadline aborts the provider request', async () => {
  await assert.rejects(evaluateJev(ticketRequest, {timeoutMs: 10,
    evaluate: ({abortSignal}) => new Promise((resolve, reject) => {
      const keepAlive = setTimeout(resolve, 500);
      abortSignal.addEventListener('abort', () => {
        clearTimeout(keepAlive);
        reject(abortSignal.reason);
      }, {once: true});
    }),
  }), {name: 'TimeoutError'});
});

test('CLI fails clearly without a key and never prints credentials', () => {
  const child = spawnSync(process.execPath, ['scripts/jev_handler.mjs', '--example'], {
    encoding: 'utf8', env: {...process.env, AI_GATEWAY_API_KEY: ''},
  });
  assert.equal(child.status, 1);
  assert.equal(child.stdout, '');
  assert.match(child.stderr, /AI_GATEWAY_API_KEY is required/);
});
