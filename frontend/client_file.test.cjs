// Focused editor checks using Node's built-in test runner and a minimal DOM.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(`${__dirname}/client_file.js`, 'utf8')
  .replace("import { SessionClient } from './shared/ui.js';", '')
  .replace(/new ClientFileEditor\(document\.querySelector\('#draft-form'\)[\s\S]*$/, 'globalThis.Editor = ClientFileEditor;');
const template = readFileSync(`${__dirname}/client_file.html`, 'utf8');
const initial = {
  id: 'fictional-file', creationKey: 'fictional-creation-key', status: 'draft',
  reference: 'FICTIONAL-1', title: 'Saved title', clientName: 'Example Client', clientEmail: 'client@example.test',
  signerName: 'Example Signer', signerEmail: 'signer@example.test', companySignerName: '', companySignerEmail: '',
  templateVersionId: 'fictional-version',
  selectedTemplate: { id: 'fictional-version', name: 'Example template', number: 1, roles: ['client'],
    preparationFields: [{ id: 'fee', type: 'text', label: 'Fee', page: 1, required: true }], previewUrl: '/company/templates/' },
  preparationValues: { fee: 'Saved fee' }, updatedAt: '2026-11-15T10:00:00.000001+00:00',
  detailUrl: '/company/files/fictional-file/', requestUrl: '/company/files/fictional-file/request/',
};

function element() {
  return {
    hidden: false, disabled: false, value: '', dataset: {}, options: [], children: [], listeners: {}, classList: { toggle() {} },
    validity: { valid: true }, maxLength: -1, attributes: {},
    addEventListener(type, callback) { this.listeners[type] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    add(option) { this.options.push(option); },
    focus() {}, before() {}, scrollIntoView() {},
  };
}

function fixture(data = initial) {
  const nodes = new Map();
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const leaveListeners = new Set();
  const reloads = [];
  const confirmations = [];
  const window = {
    location: { origin: 'http://localhost', reload: () => reloads.push(true), replace() {} },
    history: { replaceState() {} },
    confirm(message) { confirmations.push(message); return false; },
    addEventListener(type, listener) { if (type === 'beforeunload') leaveListeners.add(listener); },
    removeEventListener(type, listener) { if (type === 'beforeunload') leaveListeners.delete(listener); },
  };
  const body = { dataset: { saveUrl: data.id ? initial.detailUrl : '/company/files/new/', companyId: 'fictional-company' } };
  const document = { body, querySelector: get, querySelectorAll: () => [], getElementById: id => get(`#${id}`),
    createElement: element, addEventListener() {} };
  const context = vm.createContext({ document, window, location: window.location, URL, URLSearchParams,
    SessionClient: class {}, Option: function (text, value) { return { text, value }; }, performance: { now: () => 0 }, setInterval() {} });
  vm.runInContext(source, context);
  const inputs = [...context.Editor.fieldNames.keys()].map(name => Object.assign(element(), { name }));
  const form = get('#draft-form');
  form.querySelectorAll = () => inputs;
  get('#draft-conflict').hidden = true;
  // Session startup is outside these save/reload checks.
  context.Editor.prototype.refreshSession = async function () { this.sessionReady = true; this.updateState(); };
  const editor = new context.Editor(form, structuredClone(data));
  const edit = (name, value) => {
    const input = editor.inputs.get(name);
    input.value = value;
    editor.handleInput({ target: input });
  };
  return { editor, nodes, window, reloads, confirmations, leaveListeners, edit, data };
}

const submit = editor => editor.save({ preventDefault() {} });
const snapshot = editor => JSON.stringify(editor.readValues());

test('a conflict preserves all local entries, baseline, and leave protection without retrying', async () => {
  assert.match(template, /id="draft-conflict" hidden/);
  assert.match(template, /This file was updated in another tab\. Your changes have not been saved\./);
  assert.match(template, /id="reload-latest-draft" type="button">Load latest saved details/);
  const f = fixture();
  f.edit('title', 'Unsaved local title');
  f.editor.preparationInputs.get('fee').value = 'Unsaved local fee';
  const before = snapshot(f.editor);
  const baseline = f.editor.savedValues;
  let calls = 0;
  f.editor.client.request = async (_url, payload) => {
    calls++;
    assert.equal(payload.updatedAt, initial.updatedAt);
    throw Object.assign(new Error('Conflict'), { status: 409, code: 'draft_conflict' });
  };
  await submit(f.editor);
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.editor.savedValues, baseline);
  assert.equal(f.editor.savedUpdatedAt, initial.updatedAt);
  assert.equal(f.nodes.get('#draft-conflict').hidden, false);
  assert.equal(f.editor.dirty, true);
  assert.equal(f.leaveListeners.size, 1);
  assert.equal(f.editor.fields.disabled, false);
  f.edit('title', 'Still local');
  assert.equal(f.nodes.get('#draft-conflict').hidden, false);
  assert.equal(f.editor.button.disabled, true);
  assert.equal(f.editor.sendButton.disabled, true);
  await submit(f.editor);
  assert.equal(calls, 1);
});

test('successful saves refresh the version used by the next save', async () => {
  const f = fixture();
  const next = { ...initial, title: 'First save', updatedAt: '2026-11-15T10:00:01.000001+00:00' };
  const versions = [];
  f.editor.client.request = async (_url, payload) => {
    versions.push(payload.updatedAt);
    return { file: { ...next, title: payload.title } };
  };
  f.edit('title', 'First save');
  await submit(f.editor);
  assert.equal(f.editor.savedUpdatedAt, next.updatedAt);
  assert.equal(f.editor.dirty, false);
  assert.equal(f.leaveListeners.size, 0);
  f.edit('title', 'Second save');
  await submit(f.editor);
  assert.deepEqual(versions, [initial.updatedAt, next.updatedAt]);
});

test('explicit reload warns before discarding; cancel preserves entries and accepting loads a new baseline', async () => {
  const f = fixture();
  f.edit('title', 'Unsaved title');
  const before = snapshot(f.editor);
  f.editor.reloadLatest.listeners.click();
  assert.match(f.confirmations[0], /discard the unsaved entries/);
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.reloads.length, 0);
  assert.equal(f.leaveListeners.size, 1);
  f.window.confirm = () => true;
  f.editor.reloadLatest.listeners.click();
  assert.equal(f.reloads.length, 1);
  assert.equal(f.leaveListeners.size, 0);
  const latest = { ...initial, title: 'Another tab saved this', updatedAt: '2026-11-15T11:00:00.000001+00:00' };
  const reloaded = fixture(latest);
  assert.equal(reloaded.editor.inputs.get('title').value, latest.title);
  assert.equal(reloaded.editor.savedUpdatedAt, latest.updatedAt);
  reloaded.edit('title', 'Edit after reload');
  reloaded.editor.client.request = async (_url, payload) => {
    assert.equal(payload.updatedAt, latest.updatedAt);
    return { file: { ...latest, title: payload.title, updatedAt: '2026-11-15T12:00:00.000001+00:00' } };
  };
  await submit(reloaded.editor);
  assert.equal(reloaded.editor.dirty, false);
});

test('new-file retry recovery keeps its creation key behavior and uses the saved version for later edits', async () => {
  const f = fixture({ ...initial, id: null, updatedAt: null });
  f.edit('title', 'Local retry content');
  const payloads = [];
  f.editor.client.request = async (_url, payload) => {
    payloads.push(payload);
    return { created: false, file: initial };
  };
  await submit(f.editor);
  assert.equal(payloads[0].creation_key, initial.creationKey);
  assert.equal(Object.hasOwn(payloads[0], 'updatedAt'), false);
  assert.equal(f.editor.inputs.get('title').value, 'Local retry content');
  assert.equal(f.editor.isNew, false);
  assert.equal(f.editor.savedUpdatedAt, initial.updatedAt);
  assert.equal(f.editor.dirty, true);
  await submit(f.editor);
  assert.equal(payloads[1].updatedAt, initial.updatedAt);
  assert.equal(Object.hasOwn(payloads[1], 'creation_key'), false);
});

test('ordinary save errors still preserve entries and allow an explicit retry', async () => {
  const f = fixture();
  f.edit('title', 'Unsaved title');
  const before = snapshot(f.editor);
  f.editor.client.request = async () => { throw Object.assign(new Error('Temporary failure'), { status: 503 }); };
  await submit(f.editor);
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.editor.button.disabled, false);
  assert.equal(f.editor.draftConflict, false);
  assert.equal(f.editor.saveError.hidden, false);
  assert.equal(f.leaveListeners.size, 1);
});

const activeFile = { ...initial, status: 'awaiting_client', currentRequestId: 'request-one',
  currentRequestStatus: 'awaiting_client', canCancelRequest: true, canPrepareReplacement: false,
  cancelUrl: '/company/files/fictional-file/request/cancel/', replacementUrl: '/company/files/fictional-file/request/prepare-replacement/',
  requestHistoryUrl: '/company/files/fictional-file/requests/' };
const cancelledFile = { ...activeFile, status: 'cancelled', currentRequestStatus: 'cancelled', canCancelRequest: false, canPrepareReplacement: true };
const invitation = (progress = 'awaiting_client', requestId = 'request-one') => ({
  requestId, progress, selectedTemplate: initial.selectedTemplate, templateVersionId: initial.templateVersionId,
  preparationValues: initial.preparationValues, state: progress === 'cancelled' ? 'cancelled' : 'pending',
  link: '/client/fictional-invitation/', invitationId: 'fictional-invitation',
  expiresAt: '2026-11-16T10:00:00+00:00', serverNow: '2026-11-15T10:00:00+00:00',
  accessAvailable: progress !== 'cancelled', canReplace: false, code: null,
});

test('cancellation requires confirmation and preserves entries while exposing explicit replacement preparation', async () => {
  const f = fixture(activeFile);
  const before = snapshot(f.editor);
  const calls = [];
  f.editor.client.request = async (url, payload) => {
    calls.push({ url, payload });
    return { file: cancelledFile, invitation: invitation('cancelled'), outcome: 'cancelled' };
  };
  assert.equal(f.editor.cancelButton.hidden, false);
  assert.equal(f.editor.prepareButton.hidden, true);
  await f.editor.changeRequest('cancel');
  assert.equal(calls.length, 0);
  assert.match(f.confirmations[0], /Further signing will stop.*signing records.*retained/);
  f.window.confirm = () => true;
  await f.editor.changeRequest('cancel');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, activeFile.cancelUrl);
  assert.equal(calls[0].payload.requestId, activeFile.currentRequestId);
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.editor.savedUpdatedAt, initial.updatedAt);
  assert.equal(f.editor.cancelButton.hidden, true);
  assert.equal(f.editor.prepareButton.hidden, false);
  assert.equal(f.editor.sendButton.disabled, true);
  assert.equal(f.nodes.get('#demo-invitation').hidden, true);
  assert.match(f.nodes.get('#request-lifecycle-status').textContent, /Request cancelled/);
  assert.equal(f.reloads.length, 0);
});

test('an expired request offers explicit replacement and hides signing access', () => {
  const expired = { ...cancelledFile, status: 'expired', currentRequestStatus: 'expired' };
  const f = fixture(expired);
  f.editor.renderInvitation({ ...invitation('expired'), state: 'request_expired',
    requestExpiresAt: '2026-11-29T10:00:00+00:00' });
  assert.equal(f.editor.cancelButton.hidden, true);
  assert.equal(f.editor.prepareButton.hidden, false);
  assert.equal(f.editor.sendButton.hidden, true);
  assert.equal(f.nodes.get('#demo-invitation').hidden, true);
  assert.equal(f.nodes.get('#demo-code').value, '');
  assert.equal(f.nodes.get('#file-status').textContent, 'Expired');
  assert.match(f.nodes.get('#request-lifecycle-status').textContent, /Earlier signing records are retained/);
});

test('invitation preview identifies frozen recipient and keeps request and code deadlines distinct', () => {
  const f = fixture(activeFile);
  f.editor.renderInvitation({ ...invitation(), recipient: { name: 'Demo Client', email: 'demo@example.test' },
    requestExpiresAt: '2026-11-29T10:00:00+00:00', code: '123456' });
  assert.equal(f.nodes.get('#demo-recipient').textContent, 'Demo Client <demo@example.test>');
  assert.equal(f.nodes.get('#demo-subject').textContent, 'Please review and sign your agreement');
  assert.match(f.nodes.get('#demo-message').textContent, /Sign & submit/);
  assert.match(f.nodes.get('#request-deadline').textContent, /Access-code replacement does not extend it/);
  assert.match(f.nodes.get('#demo-expiry').textContent, /Code expires in/);
  assert.match(template, /Normally this invitation is emailed to the client. In this demo/);
});

test('pending or uncertain cancellation blocks sending and countersigning until explicit status recovery', async () => {
  const f = fixture(activeFile);
  f.window.confirm = () => true;
  let reject;
  let calls = 0;
  f.editor.client.request = () => { calls++; return new Promise((_resolve, failure) => { reject = failure; }); };
  const cancellation = f.editor.changeRequest('cancel');
  assert.equal(f.editor.requestBusy, true);
  assert.equal(f.editor.cancelButton.textContent, 'Cancellation pending…');
  assert.equal(f.editor.sendButton.disabled, true);
  let prevented = false;
  f.nodes.get('#company-countersign').listeners.click({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  reject(Object.assign(new Error('Connection lost'), { status: 503 }));
  await cancellation;
  assert.equal(f.editor.cancellationPending, true);
  assert.equal(f.editor.sendButton.disabled, true);
  assert.equal(calls, 1);
  f.editor.client.request = async () => ({ file: cancelledFile, invitation: invitation('cancelled') });
  await f.editor.loadInvitation();
  assert.equal(f.editor.cancellationPending, false);
  assert.equal(f.editor.prepareButton.hidden, false);
});

test('preparing a replacement is explicit, warns about unsaved entries, and never sends it', async () => {
  const f = fixture(cancelledFile);
  f.editor.inputs.get('title').value = 'Local entries to keep';
  f.editor.dirty = true;
  f.editor.updateState();
  let calls = 0;
  f.editor.client.request = async (url, payload) => {
    calls++;
    assert.equal(url, cancelledFile.replacementUrl);
    assert.equal(payload.requestId, cancelledFile.currentRequestId);
    return { file: { ...cancelledFile, status: 'draft', replacesRequestId: 'request-one' }, invitation: null };
  };
  await f.editor.changeRequest('prepare');
  assert.equal(calls, 0);
  assert.equal(f.reloads.length, 0);
  assert.match(f.confirmations[0], /discard the unsaved entries/);
  f.window.confirm = () => true;
  await f.editor.changeRequest('prepare');
  assert.equal(calls, 1);
  assert.equal(f.reloads.length, 1);
});

test('replacement setup sends only after explicit action with its saved version and cancelled-request identity', async () => {
  const draft = { ...cancelledFile, status: 'draft', replacesRequestId: 'request-one', canPrepareReplacement: false };
  const f = fixture(draft);
  let calls = 0;
  f.editor.client.request = async (_url, payload) => {
    calls++;
    assert.equal(payload.replacesRequestId, 'request-one');
    assert.equal(payload.updatedAt, draft.updatedAt);
    return { file: { ...activeFile, currentRequestId: 'request-two' }, invitation: invitation('awaiting_client', 'request-two'), created: true };
  };
  assert.equal(calls, 0);
  assert.equal(f.editor.fields.disabled, false);
  assert.match(f.nodes.get('#request-lifecycle-status').textContent, /Replacement draft.*Earlier signatures and consent are not reused/);
  await f.editor.requestInvitation();
  assert.equal(calls, 1);
  assert.equal(f.editor.issued, true);
});

test('a completion-winning cancellation reports completion and does not offer or prepare a replacement', async () => {
  const f = fixture(activeFile);
  f.window.confirm = () => true;
  f.editor.client.request = async () => ({ file: { ...activeFile, status: 'finalizing', canCancelRequest: false },
    invitation: invitation('finalizing'), outcome: 'signatures_complete' });
  await f.editor.changeRequest('cancel');
  assert.equal(f.editor.prepareButton.hidden, true);
  assert.equal(f.editor.cancelButton.hidden, true);
  assert.match(f.nodes.get('#demo-status').textContent, /not cancelled.*no replacement was prepared/);
  assert.equal(f.reloads.length, 0);
});

test('status refresh does not overwrite a replacement draft or silently advance its saved version', async () => {
  const draft = { ...cancelledFile, status: 'draft', replacesRequestId: 'request-one', canPrepareReplacement: false };
  const f = fixture(draft);
  f.edit('title', 'Local unsaved correction');
  f.editor.preparationInputs.get('fee').value = 'Local unsaved fee';
  const before = snapshot(f.editor);
  f.editor.client.request = async () => ({ file: { ...draft, title: 'Remote saved edit', updatedAt: '2026-11-15T12:00:00+00:00' }, invitation: null });
  await f.editor.loadInvitation();
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.editor.savedUpdatedAt, draft.updatedAt);
  assert.equal(f.leaveListeners.size, 1);
});

test('another tab issuing a replacement preserves old local entries and requires an explicit reload', async () => {
  const f = fixture({ ...cancelledFile, status: 'draft', replacesRequestId: 'request-one', canPrepareReplacement: false });
  f.edit('title', 'Local unsaved correction');
  const before = snapshot(f.editor);
  f.editor.client.request = async () => ({ file: { ...activeFile, currentRequestId: 'request-two' }, invitation: invitation('awaiting_client', 'request-two') });
  await f.editor.loadInvitation();
  assert.equal(snapshot(f.editor), before);
  assert.equal(f.editor.stateReload.hidden, false);
  assert.equal(f.editor.cancelButton.hidden, true);
  assert.equal(f.editor.recoveryRequired, true);
  assert.equal(f.leaveListeners.size, 1);
});
