const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const context = vm.createContext({});
vm.runInContext(readFileSync(`${__dirname}/agreement_table.js`, 'utf8')
  .replaceAll('export class ', 'class ') + '\nglobalThis.Actions = SigningActions;', context);

for (const [outcome, status, message] of [
  ['cancelled', 'cancelled', 'Request cancelled. Further signing is stopped; existing records are preserved.'],
  ['already_expired', 'expired', 'The signing deadline has passed. Earlier signatures and history are preserved.'],
  ['signatures_complete', 'finalizing', 'All required signatures were already recorded. The request was not cancelled. Continue finalization; no replacement was prepared.'],
  ['signatures_complete', 'completed', 'The agreement had already completed. Its completed documents are preserved.'],
]) {
  test(`cancellation reports the authoritative ${status} outcome`, async () => {
    const calls = [];
    const actions = Object.create(context.Actions.prototype);
    actions.confirm = async () => true;
    actions.request = async (url, payload) => {
      calls.push({ url, payload });
      return { outcome, file: { status } };
    };
    let refreshed = false;
    const table = { notice: {}, run: async (_message, action) => action(),
      refresh: async force => { refreshed = force; } };
    await actions.cancel({ id: 'file', title: 'Fictional agreement', cancelUrl: '/cancel/', requestId: 'request' }, table);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/cancel/');
    assert.equal(calls[0].payload.requestId, 'request');
    assert.equal(refreshed, true);
    assert.equal(table.notice.textContent, message);
  });
}
