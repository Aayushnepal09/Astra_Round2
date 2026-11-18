const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { test } = require('node:test');
const vm = require('node:vm');

function progress(counts) {
  const nodes = new Map();
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { dataset: {}, removeAttribute() {} });
    return nodes.get(selector);
  };
  const context = vm.createContext({ document: { querySelector: get, querySelectorAll: () => [] } });
  const source = readFileSync(`${__dirname}/dashboard.js`, 'utf8')
    .replace(/^import .*;\r?\n/gm, '')
    .replace(/new WorkspaceDashboard\(JSON\.parse\(document\.querySelector\('#workspace-data'\)\.textContent\)\);/, '');
  vm.runInContext(source + '\nglobalThis.Dashboard = WorkspaceDashboard;', context);
  const dashboard = Object.create(context.Dashboard.prototype);
  dashboard.counts = counts;
  dashboard.renderProgress();
  return get;
}

test('overview does not claim final records are saved while any agreement is finalizing', () => {
  const get = progress({ all: 2, draft: 0, waiting: 0, ready: 1, attention: 0 });
  assert.equal(get('#setup-title').textContent, 'Final records still need processing.');
  assert.equal(get('#setup-action').dataset.filterLink, 'all');
});

test('overview confirms saved records when every agreement is completed', () => {
  const get = progress({ all: 2, draft: 0, waiting: 0, ready: 2, attention: 0 });
  assert.equal(get('#setup-title').textContent, 'Everything is signed and saved.');
  assert.equal(get('#setup-action').dataset.view, 'clients');
});
