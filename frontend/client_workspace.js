import { SessionClient, SessionError } from './shared/ui.js';
import { AgreementTable, SigningActions } from './agreement_table.js?v=ui26';

const node = (tag, text = '', className = '') => {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
};
const link = (text, href, className = 'text-link') => {
  const element = node('a', text, className);
  element.href = href;
  return element;
};

export class ClientDirectory {
  constructor() {
    this.root = document.querySelector('#client-directory');
    this.list = document.querySelector('#client-list');
    this.status = document.querySelector('#client-directory-status');
    this.search = document.querySelector('#client-search');
    this.client = new SessionClient();
    this.companyId = document.body.dataset.companyId;
    this.signingActions = new SigningActions((url, payload) => this.request(url, payload));
    this.tables = new Map();
    this.refreshTimer = setInterval(() => this.refreshOpenJobs(), 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.refreshOpenJobs(); });
    window.addEventListener('focus', () => this.refreshOpenJobs());
    window.addEventListener('pagehide', () => clearInterval(this.refreshTimer));
    this.baseUrl = document.body.dataset.clientsUrl;
    this.page = 1;
    this.requestNumber = 0;
    this.dirtyForms = new Set();
    this.newClientForm = document.querySelector('#new-client-form');
    this.root.addEventListener('input', event => {
      const form = event.target.closest('form');
      if (form) this.dirtyForms.add(form);
    });
    window.addEventListener('beforeunload', event => {
      if (this.dirtyForms.size) { event.preventDefault(); event.returnValue = ''; }
    });
    document.querySelector('#use-version-clear').addEventListener('click', () => this.clearChosenVersion());
    document.querySelector('#new-client-toggle').addEventListener('click', () => {
      document.querySelector('#new-client-details').open = true;
      this.newClientForm.elements.name.focus();
    });
    this.newClientForm.addEventListener('submit', event => {
      event.preventDefault();
      if (!this.newClientForm.reportValidity()) return;
      const payload = Object.fromEntries(new FormData(this.newClientForm));
      this.submit(this.newClientForm, this.baseUrl, payload, async result => {
        const row = this.clientRow({ ...result.client, startJob: true });
        this.list.prepend(row);
        document.querySelector('#new-client-details').open = false;
        row.open = true;
        this.status.textContent = 'Client saved. Add a job/file below, then choose its agreements.';
      });
    });
    this.search.addEventListener('input', () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => { this.page = 1; this.load(); }, 300);
    });
    for (const [id, direction] of [['clients-previous', -1], ['clients-next', 1]]) {
      document.querySelector(`#${id}`).addEventListener('click', () => { this.page += direction; this.load(); });
    }
    document.querySelector('#clients-retry').addEventListener('click', () => this.load());
  }

  async request(url, payload) {
    const endpoint = new URL(url, location.origin);
    if (payload === undefined) endpoint.searchParams.set('workspace_id', this.companyId);
    const result = await this.client.request(endpoint, payload === undefined ? undefined : { ...payload, workspace_id: this.companyId });
    if (result.companyId !== this.companyId) throw new SessionError('Your workspace changed. Reload before continuing.');
    return result;
  }

  show() { this.renderChosenVersion(); if (!this.loaded) this.load(); }

  chosenVersion() {
    try { return JSON.parse(sessionStorage.getItem('astra.useVersion')) || null; } catch { return null; }
  }

  renderChosenVersion() {
    const banner = document.querySelector('#use-version-banner');
    const chosen = this.chosenVersion();
    banner.hidden = !chosen;
    if (!chosen) return;
    const text = banner.querySelector('p');
    text.replaceChildren('Choose a client for ', node('strong', chosen.label), '. Open the client and start a job; this agreement will already be ticked.');
  }

  clearChosenVersion() {
    try { sessionStorage.removeItem('astra.useVersion'); } catch {}
    this.renderChosenVersion();
  }

  async load() {
    this.renderChosenVersion();
    const editing = [...this.dirtyForms].filter(form => form !== this.newClientForm && this.list.contains(form));
    if (editing.length && !window.confirm('Discard the unsaved job or agreement selections before reloading the client list?')) return;
    editing.forEach(form => this.dirtyForms.delete(form));
    const request = ++this.requestNumber;
    this.list.setAttribute('aria-busy', 'true');
    this.status.textContent = 'Loading clients…';
    const retry = document.querySelector('#clients-retry');
    retry.hidden = true;
    try {
      const url = new URL(this.baseUrl, location.origin);
      url.searchParams.set('q', this.search.value.trim());
      url.searchParams.set('page', this.page);
      const result = await this.request(url);
      if (request !== this.requestNumber) return;
      this.loaded = true;
      if (!this.search.value.trim()) document.querySelector('#client-nav-count').textContent = result.total;
      this.page = result.page;
      this.list.replaceChildren(...result.clients.map(client => this.clientRow(client)));
      this.status.textContent = result.total ? `${result.total} ${result.total === 1 ? 'client' : 'clients'} · expand a client to see jobs and signing progress.`
        : this.search.value.trim() ? 'No matching clients. Try a name, email or job reference.' : 'No clients yet. Add your first client to start a job.';
      document.querySelector('#clients-page').textContent = `Page ${result.page} of ${result.pages}`;
      document.querySelector('#clients-pagination').hidden = result.pages <= 1;
      document.querySelector('#clients-previous').disabled = !result.hasPrevious;
      document.querySelector('#clients-next').disabled = !result.hasNext;
      const selected = new URL(location.href).searchParams.get('client');
      let row = [...this.list.children].find(element => element.dataset.clientId === selected);
      if (!row && !this.search.value.trim() && /^[0-9a-f-]{36}$/i.test(selected || '')) {
        const selectedClient = await this.request(new URL(`${selected}/`, new URL(this.baseUrl, location.origin)));
        if (request !== this.requestNumber) return;
        row = this.clientRow(selectedClient.client);
        this.list.prepend(row);
      }
      // A single result (e.g. after a search) opens straight to its jobs.
      if (!row && this.list.children.length === 1) row = this.list.firstElementChild;
      if (row) row.open = true;
    } catch (error) {
      if (request === this.requestNumber) { this.status.textContent = error.message; retry.hidden = false; }
    } finally { if (request === this.requestNumber) this.list.setAttribute('aria-busy', 'false'); }
  }

  clientRow(client) {
    const row = node('details', '', 'client-row');
    row.dataset.clientId = client.id;
    const summary = node('summary');
    const identity = node('span', '', 'client-identity');
    identity.append(node('strong', client.name), node('span', client.email || 'No contact email saved'));
    const attention = node('span', '', 'client-action-needed');
    attention.hidden = !client.companySignatureCount;
    attention.textContent = `${client.companySignatureCount || 0} company ${client.companySignatureCount === 1 ? 'signature' : 'signatures'} needed`;
    identity.append(attention);
    const counts = node('span', '', 'client-counts');
    this.renderClientCounts(counts, client);
    const avatar = node('span', client.name.trim().split(/\s+/).slice(0,2).map(word => [...word][0]).join('').toUpperCase(), 'client-avatar');
    avatar.setAttribute('aria-hidden', 'true');
    const expand = node('span', '', 'client-expand');
    const expandLabel = node('span', 'View agreements');
    const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevron.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#icon-chevron'); chevron.append(use);
    expand.append(expandLabel, chevron);
    summary.append(avatar, identity, counts, expand);
    const content = node('div', '', 'client-content');
    row.append(summary, content);
    row.addEventListener('toggle', () => {
      expandLabel.textContent = row.open ? 'Hide agreements' : 'View agreements';
      if (row.open && !row.dataset.loaded) { row.dataset.loaded = 'true'; this.loadClient(client, content); }
    });
    return row;
  }

  async loadClient(client, content, page = 1) {
    const status = node('p', 'Loading jobs…', 'field-hint');
    content.append(status);
    try {
      const url = new URL(client.detailUrl, location.origin);
      url.searchParams.set('page', page);
      const openJob = new URL(location.href).searchParams.get('open_job');
      if (page === 1 && openJob) url.searchParams.set('open_job', openJob);
      const result = await this.request(url);
      const summary = content.closest('.client-row')?.querySelector('.client-counts');
      if (summary) this.renderClientCounts(summary, result.client);
      if (page === 1) {
        content.replaceChildren();
        const create = node('details', '', 'client-create-job');
        create.append(node('summary', '+ Start a new job for this client'), this.jobForm(client, content));
        // Opening the job form also opens its agreement list, so the choice is never hidden.
        create.addEventListener('toggle', () => { if (create.open) create.querySelector('.version-picker').open = true; });
        create.open = !!client.startJob || !!this.chosenVersion();
        content.append(create);
        if (!result.total) content.append(node('p', 'No jobs yet. Create a job/file, then select prepared agreements or upload a document.', 'field-hint client-empty'));
      } else status.remove();
      result.jobs.forEach((job, index) => content.append(this.jobRow(job, job.id === new URL(location.href).searchParams.get('open_job') || index === 0)));
      if (result.hasNext) {
        const more = node('button', 'Load more jobs', 'button secondary');
        more.type = 'button';
        more.addEventListener('click', () => { more.remove(); this.loadClient(client, content, result.page + 1); });
        content.append(more);
      }
    } catch (error) {
      status.textContent = error.message;
      const retry = node('button', 'Retry loading jobs', 'button secondary');
      retry.type = 'button';
      retry.addEventListener('click', () => { retry.remove(); status.remove(); this.loadClient(client, content, page); });
      content.append(retry);
    }
  }

  form() {
    const form = node('form', '', 'client-setup-form');
    const fields = node('fieldset');
    const status = node('p', '', 'field-hint');
    status.setAttribute('role', 'status');
    const error = node('p', '', 'field-error');
    error.setAttribute('role', 'alert');
    error.hidden = true;
    form.append(fields, status, error);
    return { form, fields };
  }

  input(parent, label, name, maxLength, required = false) {
    const wrapper = node('label', label);
    const input = node('input');
    input.name = name;
    input.maxLength = maxLength;
    input.required = required;
    input.autocomplete = 'off';
    wrapper.append(input);
    parent.append(wrapper);
  }

  jobForm(client, content) {
    const { form, fields } = this.form();
    this.input(fields, 'Job / file reference (required)', 'reference', 128, true);
    this.input(fields, 'Job / file title', 'title', 200);
    fields.append(this.versionPicker(document.body.dataset.templatesUrl));
    fields.append(node('p', 'Choose agreements now, or create the job first and upload a document. Nothing is issued automatically.', 'field-hint'));
    const button = node('button', 'Create job', 'button');
    button.type = 'submit';
    fields.append(button);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      this.submit(form, client.jobsUrl, {
        reference: form.elements.reference.value, title: form.elements.title.value,
        versions: [...form.querySelectorAll('[name="versions"]:checked')].map(input => input.value),
      }, result => {
        if (form.querySelector(`[name="versions"][value="${this.chosenVersion()?.id}"]:checked`)) this.clearChosenVersion();
        content.querySelector('.client-empty')?.remove();
        content.insertBefore(this.jobRow(result.job, true), form.closest('details').nextSibling);
        form.closest('details').open = false;
      });
    });
    return form;
  }

  versionPicker(url) {
    const details = node('details', '', 'version-picker');
    details.append(node('summary', 'Choose prepared agreements'));
    const options = node('div', '', 'version-options');
    const status = node('p', '', 'field-hint');
    details.append(options, status);
    let page = 1, busy = false;
    const latestShown = new Set();
    const load = async () => {
      if (busy) return;
      busy = true;
      status.textContent = 'Loading published versions…';
      try {
        const endpoint = new URL(url, location.origin);
        endpoint.searchParams.set('page', page);
        const result = await this.request(endpoint);
        for (const version of result.versions) {
          const label = node('label', '', 'version-choice');
          const input = node('input');
          input.type = 'checkbox'; input.name = 'versions'; input.value = version.id;
          input.checked = version.id === this.chosenVersion()?.id;
          // Versions arrive newest first: keep the latest per template visible and tuck older ones away.
          const older = !!version.agreementId && latestShown.has(version.agreementId) && !input.checked;
          if (version.agreementId) latestShown.add(version.agreementId);
          if (older) { label.classList.add('older-version'); label.hidden = !details.dataset.showOlder; }
          label.append(input, node('span', `${version.name} · Version ${version.number}${version.jobId ? ' · This job only' : ''}`), link('Preview', version.previewUrl));
          label.querySelector('a').target = '_blank';
          label.querySelector('a').rel = 'noopener';
          options.append(label);
        }
        page = result.pagination.number + 1;
        const olderCount = options.querySelectorAll('.older-version').length;
        let toggle = details.querySelector('.older-toggle');
        if (olderCount && !details.dataset.showOlder) {
          toggle ??= node('button', '', 'button secondary older-toggle');
          toggle.type = 'button';
          toggle.textContent = `Show older versions (${olderCount})`;
          toggle.onclick = () => { details.dataset.showOlder = 'true'; options.querySelectorAll('.older-version').forEach(item => { item.hidden = false; }); toggle.remove(); };
          options.after(toggle);
        }
        status.textContent = result.pagination.total ? 'Choose up to 20. Each agreement has its own review, signing request and completed PDF.' : 'No ready versions yet. Create this job and upload a document, or prepare a reusable template.';
        if (result.pagination.hasNext) {
          const more = node('button', 'Load more versions', 'button secondary');
          more.type = 'button';
          more.addEventListener('click', () => { more.remove(); load(); });
          details.append(more);
        }
        details.dataset.loaded = 'true';
      } catch (error) { status.textContent = `${error.message} Close and reopen this list to retry.`; }
      finally { busy = false; }
    };
    details.addEventListener('toggle', () => { if (details.open && !details.dataset.loaded) load(); });
    return details;
  }

  jobRow(job, open = false) {
    const row = node('details', '', 'client-job');
    row.open = open;
    row.append(node('summary', `${job.reference}${job.title ? ' · ' + job.title : ''} · ${job.agreementCount} ${job.agreementCount === 1 ? 'agreement' : 'agreements'}${job.readyToStart ? ' · Ready to start' : ''}`));
    const table = new AgreementTable(this, job);
    this.tables.set(job.id, table);
    row.append(table.root);
    row.addEventListener('toggle', () => { if (row.open) table.refresh(); });
    const actions = node('div', '', 'job-actions');
    actions.append(link('Upload a new agreement for this job', job.uploadUrl, 'button secondary'));
    const add = node('details', '', 'job-add-agreements');
    add.append(node('summary', '+ Add a prepared agreement to this job'));
    const { form, fields } = this.form();
    fields.append(this.versionPicker(job.templatesUrl));
    const button = node('button', 'Add agreement drafts', 'button');
    button.type = 'submit'; fields.append(button);
    form.addEventListener('submit', event => {
      event.preventDefault();
      this.submit(form, job.agreementsUrl, { versions: [...form.querySelectorAll('[name="versions"]:checked')].map(input => input.value) }, result => row.replaceWith(this.jobRow(result.job, true)));
    });
    add.append(form); row.append(actions, add);
    add.addEventListener('toggle', () => { if (add.open) form.querySelector('.version-picker').open = true; });
    return row;
  }

  renderClientCounts(target, client) {
    const attention = target.closest('.client-row')?.querySelector('.client-action-needed');
    if (attention) {
      attention.hidden = !client.companySignatureCount;
      attention.textContent = `${client.companySignatureCount || 0} company ${client.companySignatureCount === 1 ? 'signature' : 'signatures'} needed`;
    }
    target.replaceChildren(...[
      [client.jobCount, 'Jobs', ''], [client.agreementCount, 'Agreements', ''],
      [client.waitingCount, 'Awaiting client', 'waiting'], [client.completedCount, 'Completed', 'completed'],
    ].map(([count,label,tone]) => {
      const stat = node('span', '', `client-stat${count ? ' ' + tone : ''}`);
      stat.append(node('strong', String(count)), node('span', count === 1 && label === 'Jobs' ? 'Job' : count === 1 && label === 'Agreements' ? 'Agreement' : label));
      return stat;
    }));
  }

  async refreshOpenJobs() {
    if (document.hidden || this.root.closest('.workspace-view')?.hidden) return;
    let refreshed = 0;
    for (const [id, table] of this.tables) {
      if (!table.root.isConnected) { this.tables.delete(id); continue; }
      if (table.root.closest('.client-job').open && table.root.closest('.client-row').open && refreshed++ < 5) await table.refresh();
    }
  }

  async submit(form, url, payload, success) {
    if (form.dataset.busy === 'true') return;
    const fingerprint = JSON.stringify(payload);
    if (form.dataset.fingerprint !== fingerprint) { form.dataset.key = crypto.randomUUID(); form.dataset.fingerprint = fingerprint; }
    const status = form.querySelector('[role="status"]');
    const error = form.querySelector('[role="alert"]');
    const fields = form.querySelector('fieldset');
    error.hidden = true;
    form.dataset.busy = 'true'; form.setAttribute('aria-busy', 'true'); fields.disabled = true;
    status.textContent = 'Saving…';
    this.dirtyForms.add(form);
    try {
      const result = await this.request(url, { ...payload, creation_key: form.dataset.key });
      if (result.client) {
        const summary = form.closest('.client-row')?.querySelector('.client-counts');
        if (summary) this.renderClientCounts(summary, result.client);
        if (form === this.newClientForm) {
          const count = document.querySelector('#client-nav-count');
          count.textContent = result.clientCount;
        }
      }
      this.dirtyForms.delete(form);
      form.reset(); delete form.dataset.fingerprint;
      status.textContent = 'Saved. No signing request has been issued.';
      await success(result);
    } catch (failure) { status.textContent = ''; error.textContent = failure.message; error.hidden = false; }
    finally { form.dataset.busy = 'false'; form.setAttribute('aria-busy', 'false'); fields.disabled = false; }
  }
}
