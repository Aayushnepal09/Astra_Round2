const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(`${__dirname}/agreement_assistant.js`, 'utf8')
  .replace('export class DocumentAssistant', 'class DocumentAssistant') + '\nglobalThis.Assistant = DocumentAssistant;';

function element() {
  return { open: false, disabled: false, hidden: false, value: '', children: [], listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute() {}, replaceChildren(...children) { this.children = children; },
    append(...children) { this.children.push(...children); }, focus() {}, reportValidity: () => true };
}

function fixture(response) {
  const nodes = new Map();
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const calls = [], timers = [];
  const document = { hidden: false, body: { dataset: { assistantUrl: '/fictional/assistant/' } },
    querySelector: get, createElement: element, addEventListener() {} };
  const context = vm.createContext({ document, window: { matchMedia: () => ({ matches: false }) }, AbortController, clearTimeout() {},
    setTimeout(callback) { timers.push(callback); return timers.length; },
    fetch: async (...args) => { calls.push(args); return response; } });
  vm.runInContext(source, context);
  const review = { signing: { state: {}, expired: false, uncertain: false }, client: { token: { content: 'fictional-csrf' } } };
  return { assistant: new context.Assistant(review), review, calls, timers, nodes, get };
}

test('collapsed optional help makes no availability or inference request', async () => {
  const f = fixture({ ok: true, json: async () => ({ available: true, questionMaxChars: 2000 }) });
  assert.equal(f.calls.length, 0);
  f.assistant.root.open = true;
  await f.assistant.check();
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][1].method, 'GET');
  assert.equal(f.assistant.summary.disabled, false);
});

test('known quota waits affect assistance only and never automatically retry', async () => {
  const f = fixture({ ok: false, status: 429, headers: { get: () => '7' },
    json: async () => ({ error: 'Shared allowance in use.', retryAfter: 7 }) });
  f.assistant.available = true;
  const signingField = f.get('#signer-fictional');
  await f.assistant.send('summary');
  assert.match(f.assistant.error.textContent, /Try again in 7 seconds/);
  assert.equal(f.assistant.summary.disabled, true);
  assert.equal(signingField.disabled, false);
  assert.equal(f.review.signing.expired, false);
  assert.equal(f.calls.length, 1);
  f.timers[0]();
  assert.equal(f.calls.length, 1);
});

test('mixed and selected text summaries display their coverage limitation', () => {
  const f = fixture();
  f.assistant.render({ category: 'answer', claims: [{ text: 'Readable clause.', citations: [] }],
    coverage: { summary: true, complete: false, limitedPages: [1], selectionLimited: false, selectedPages: [1], pageCount: 1 } });
  assert.equal(f.assistant.answer.children[0].textContent, 'Readable-text summary');
  assert.ok(f.assistant.answer.children.some(child => /not a complete agreement summary/.test(child.textContent)));
  f.assistant.render({ category: 'answer', claims: [{ text: 'Selected clause.', citations: [] }],
    coverage: { summary: true, complete: false, limitedPages: [], selectionLimited: true, selectedPages: [1, 3], pageCount: 5 } });
  assert.equal(f.assistant.answer.children[0].textContent, 'Summary of selected readable passages');
  assert.ok(f.assistant.answer.children.some(child => /pages 1, 3 of 5/.test(child.textContent)));
});

test('recorded signatures disable help and clear an earlier answer', () => {
  const f = fixture();
  f.assistant.available = true;
  f.review.signing.state.receipt = { id: 'fictional-submission' };
  f.assistant.update();
  assert.equal(f.assistant.summary.disabled, true);
  assert.equal(f.assistant.answer.hidden, true);
});
