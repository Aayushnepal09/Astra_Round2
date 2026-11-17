const element = (tag, text = '', className = '') => {
  const value = document.createElement(tag);
  value.textContent = text;
  value.className = className;
  return value;
};
const anchor = (text, url, className = 'button secondary compact') => {
  const value = element('a', text, className);
  value.href = url;
  return value;
};

export class SigningActions {
  constructor(request) {
    this.request = request;
    this.dialog = document.querySelector('#agreement-share-dialog');
    this.feedback = document.querySelector('#share-feedback');
    for (const type of ['link', 'code']) {
      document.querySelector(`#share-copy-${type}`).addEventListener('click', () => this.copy(type));
    }
    document.querySelector('#share-refresh').addEventListener('click', () => this.reloadAccess());
    document.querySelector('#share-replace-code').addEventListener('click', () => this.reloadAccess(true));
    this.dialog.addEventListener('close', () => {
      this.current = null;
      document.querySelector('#share-code').value = '';
      document.querySelector('#share-link').value = '';
    });
  }

  confirm(title, copy, action, danger = false) {
    const dialog = document.querySelector('#request-confirm-dialog');
    if (dialog.open) return Promise.resolve(false);
    document.querySelector('#request-confirm-title').textContent = title;
    document.querySelector('#request-confirm-copy').textContent = copy;
    const button = document.querySelector('#request-confirm-action');
    button.textContent = action;
    button.classList.toggle('danger', danger);
    dialog.returnValue = '';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true });
      dialog.showModal();
    });
  }

  async send(file, table) {
    if (!await this.confirm('Send this agreement?', `${file.templateName || file.title} will be issued to ${file.signerName} (${file.signerEmail}). Its version and prepared values will be fixed. Demo mode creates a signing link and code; no email is sent.`, 'Send request')) return;
    return table.run('Issuing the signing request…', async () => {
      const result = await this.request(file.requestUrl, { templateVersionId: file.templateVersionId, updatedAt: file.updatedAt, replacesRequestId: file.replacesRequestId });
      await table.refresh(true);
      this.showAccess(file, result.invitation);
    });
  }

  async cancel(file, table) {
    if (!await this.confirm('Cancel this signing request?', `Further signing of “${file.title || file.templateName}” will stop. Accepted signatures and history will be preserved. Other agreements in this job are unaffected.`, 'Cancel request', true)) return;
    return table.run('Cancelling this request…', async () => {
      const result = await this.request(file.cancelUrl, { requestId: file.requestId });
      if (this.current?.file.id === file.id) this.dialog.close();
      await table.refresh(true);
      table.notice.textContent = result.file.status === 'completed' ? 'The agreement had already completed. Its completed documents are preserved.' : 'Request cancelled. Further signing is stopped; existing records are preserved.';
    });
  }

  async share(file, table) {
    return table.run('Loading signing access…', async () => {
      const result = await this.request(file.requestUrl);
      this.showAccess(file, result.invitation);
      await table.refresh(true);
    });
  }

  showAccess(file, invitation) {
    if (!invitation) throw new Error('No issued request is available. Refresh this agreement’s status.');
    this.current = { file, invitation };
    const active = invitation.accessAvailable && !['cancelled', 'request_expired'].includes(invitation.state);
    const url = new URL(invitation.link, location.origin);
    if (url.origin !== location.origin) throw new Error('Invalid signing link.');
    document.querySelector('#agreement-share-title').textContent = invitation.readOnly ? 'Client’s recorded agreement' : 'Share signing access';
    document.querySelector('#share-recipient').textContent = `${file.title || file.templateName} · ${invitation.recipient.name} · ${invitation.recipient.email}`;
    document.querySelector('#share-link').value = active ? url.href : '';
    document.querySelector('#share-code').value = active ? invitation.code || '' : '';
    const open = document.querySelector('#share-open-client');
    open.hidden = !active; open.href = url.href;
    document.querySelector('#share-copy-link').disabled = !active;
    document.querySelector('#share-copy-code').disabled = !active || !invitation.code;
    document.querySelector('#share-replace-code').hidden = !active || !invitation.canReplace;
    const date = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    document.querySelector('#share-expiry').textContent = `${invitation.requestExpiresAt ? `Request deadline: ${date.format(new Date(invitation.requestExpiresAt))}. ` : ''}Code expires: ${date.format(new Date(invitation.expiresAt))}.`;
    this.feedback.textContent = !active ? 'This request is no longer available for signing. Refresh its status in the table.'
      : invitation.code ? 'Ready to share. The signer opens the link, enters this code, and reviews the agreement.'
      : invitation.canReplace ? 'The earlier code was used, locked, or expired. Select Get new code to restore access.'
      : 'Access has already been verified. The signer can continue in their verified browser session.';
    if (!this.dialog.open) this.dialog.showModal();
  }

  async reloadAccess(replace = false) {
    if (!this.current || this.accessBusy) return;
    this.accessBusy = true;
    const { file, invitation } = this.current;
    this.feedback.textContent = replace ? 'Creating a replacement access code…' : 'Checking access…';
    try {
      const result = await this.request(file.requestUrl, replace ? { replaceInvitationId: invitation.invitationId } : undefined);
      if (this.current?.file.id === file.id) this.showAccess(file, result.invitation);
    } catch (error) { this.feedback.textContent = error.message; }
    finally { this.accessBusy = false; }
  }

  async copy(type) {
    const input = document.querySelector(`#share-${type}`);
    if (!input.value) return;
    try { await navigator.clipboard.writeText(input.value); this.feedback.textContent = `${type === 'link' ? 'Link' : 'Code'} copied.`; }
    catch { input.focus(); input.select(); this.feedback.textContent = `Select and copy the ${type} above.`; }
  }
}

export class AgreementTable {
  constructor(directory, job) {
    this.directory = directory;
    this.job = job;
    this.root = element('div', '', 'agreement-table-section');
    this.toolbar = element('div', '', 'agreement-table-toolbar');
    this.notice = element('p', 'Status refreshes automatically while this job is open.', 'field-hint');
    this.notice.setAttribute('role', 'status');
    const refresh = element('button', 'Refresh status', 'button secondary compact');
    refresh.type = 'button'; refresh.addEventListener('click', () => this.run('Refreshing status…', () => this.refresh(true)));
    this.toolbar.append(this.notice, refresh);
    const scroll = element('div', '', 'agreement-table-scroll');
    const table = element('table', '', 'agreement-table');
    table.append(element('caption', `Agreements for ${job.reference}`, 'sr-only'));
    const head = element('thead'); const header = element('tr');
    for (const label of ['Agreement', 'Signer', 'Status', 'Actions']) { const cell = element('th', label); cell.scope = 'col'; header.append(cell); }
    head.append(header);
    this.body = element('tbody'); table.append(head, this.body); scroll.append(table);
    this.pagination = element('nav', '', 'agreement-table-pagination'); this.pagination.setAttribute('aria-label', 'Agreement pages');
    this.previous = this.button('Previous agreements', () => this.changePage(-1));
    this.next = this.button('Next agreements', () => this.changePage(1));
    this.pageLabel = element('span'); this.pagination.append(this.previous, this.pageLabel, this.next);
    this.root.append(this.toolbar, scroll, this.pagination);
    this.render(job);
  }

  button(label, action, primary = false, danger = false) {
    const button = element('button', label, `button compact${primary ? '' : ' secondary'}${danger ? ' danger' : ''}`);
    button.type = 'button'; button.dataset.requestAction = 'true';
    button.addEventListener('click', action);
    return button;
  }

  render(job) {
    this.job = job;
    this.page = job.page || 1;
    this.snapshot = JSON.stringify(job.agreements);
    this.body.replaceChildren(...job.agreements.map(file => {
      const row = element('tr');
      const cell = label => { const td = element('td'); td.dataset.label = label; row.append(td); return td; };
      const name = cell('Agreement');
      name.append(anchor(file.title || file.templateName || file.reference, file.detailUrl, 'agreement-title'),
        element('span', file.prepared ? `Preparation ready · Version ${file.versionNumber}` : 'Needs field preparation', `agreement-subline${file.prepared ? ' prepared-copy' : ''}`));
      const signer = cell('Signer');
      signer.append(element('span', file.signerName || 'Signer not set'), element('span', file.signerEmail || 'Add an email before sending', 'agreement-subline'));
      const label = ({ draft: file.readyToSend ? 'Ready to send' : 'Needs details', awaiting_client: 'Awaiting client', awaiting_company: 'Needs your signature', finalizing: 'Finalizing', completed: 'Completed', cancelled: 'Cancelled', expired: 'Expired' })[file.status] || file.status;
      const tone = file.status === 'completed' ? 'success' : file.status === 'expired' ? 'error' : ['awaiting_client', 'awaiting_company'].includes(file.status) ? 'waiting' : 'neutral';
      const status = cell('Status'); status.append(element('span', label, `status-badge ${tone}`));
      if (file.status === 'awaiting_company') status.append(element('span', 'Client signed · company signature required', 'agreement-subline'));
      if (file.setupIssues?.length) status.append(element('span', file.setupIssues[0], 'agreement-subline'));
      const actions = cell('Actions'); const buttons = element('div', '', 'agreement-row-actions');
      const service = this.directory.signingActions;
      if (file.status === 'draft') {
        buttons.append(file.readyToSend ? this.button('Send request', () => service.send(file, this), true)
          : anchor('Review & send', file.detailUrl, 'button compact'));
        if (file.readyToSend) buttons.append(anchor('Review details', file.detailUrl));
      } else if (file.status === 'completed') {
        buttons.append(anchor('Download PDF', `${file.pdfUrl}?download=1`, 'button compact'));
      } else if (file.status === 'awaiting_company') {
        buttons.append(anchor('Sign for company', file.countersignUrl, 'button compact'));
      } else if (['cancelled', 'expired'].includes(file.status)) {
        buttons.append(anchor('Prepare replacement', file.detailUrl, 'button compact'));
      } else if (file.status === 'finalizing') {
        buttons.append(anchor('Finish processing', file.detailUrl, 'button compact'));
      }
      if (file.clientLink) {
        buttons.append(this.button('Share link & code', () => service.share(file, this), file.status === 'awaiting_client'));
        const open = anchor('Open client view', file.clientLink); open.target = '_blank'; open.rel = 'noopener noreferrer'; buttons.append(open);
      }
      if (file.canCancel) buttons.append(this.button('Cancel request', () => service.cancel(file, this), false, true));
      buttons.append(anchor('History', file.historyUrl, 'table-text-link'));
      actions.append(buttons);
      return row;
    }));
    if (!job.agreements.length) {
      const row = element('tr'); const cell = element('td', 'No agreements yet. Add a prepared template or upload a document for this job.', 'agreement-table-empty');
      cell.colSpan = 4; row.append(cell); this.body.append(row);
    }
    const summary = this.root.closest('.client-job')?.querySelector(':scope > summary');
    if (summary) summary.textContent = `${job.reference}${job.title ? ' · ' + job.title : ''} · ${job.agreementCount} ${job.agreementCount === 1 ? 'agreement' : 'agreements'}${job.readyToStart ? ' · Ready to start' : ''}`;
    this.updatePagination();
  }

  updatePagination() {
    this.pagination.hidden = this.page === 1 && !this.job.hasNext;
    this.previous.disabled = this.busy || this.page <= 1;
    this.next.disabled = this.busy || !this.job.hasNext;
    this.pageLabel.textContent = `Page ${this.page}`;
  }

  changePage(direction) {
    return this.run('Loading agreements…', async () => {
      const url = new URL(this.job.agreementsUrl, location.origin); url.searchParams.set('page', this.page + direction);
      const result = await this.directory.request(url); this.render(result.job);
      this.notice.textContent = `Page ${this.page} loaded.`;
    });
  }

  async refresh(force = false) {
    if (this.pendingRefresh) { await this.pendingRefresh; if (!force) return; }
    if (this.busy && !force || !this.root.isConnected) return;
    this.pendingRefresh = this.fetchStatus(force);
    try { await this.pendingRefresh; } finally { this.pendingRefresh = null; }
  }

  async fetchStatus(force) {
    try {
      const url = new URL(this.job.agreementsUrl, location.origin); url.searchParams.set('page', this.page);
      const result = await this.directory.request(url);
      if (JSON.stringify(result.job.agreements) !== this.snapshot) this.render(result.job);
      const summary = this.root.closest('.client-row')?.querySelector('.client-counts');
      if (summary && result.client) this.directory.renderClientCounts(summary, result.client);
      const current = this.directory.signingActions.current;
      if (current && result.job.agreements.some(file => file.id === current.file.id && ['cancelled', 'expired'].includes(file.status))) this.directory.signingActions.dialog.close();
      if (force) this.notice.textContent = `Status updated at ${new Intl.DateTimeFormat(undefined, {timeStyle:'short'}).format(new Date())}.`;
    } catch (error) { this.notice.textContent = `Status could not be refreshed. ${error.message}`; if (force) throw error; }
  }

  async run(message, action) {
    if (this.busy) return;
    this.busy = true;
    this.notice.textContent = message;
    this.root.setAttribute('aria-busy', 'true');
    this.root.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try { await action(); }
    catch (error) { try { await this.refresh(true); } catch {} this.notice.textContent = error.message; }
    finally {
      this.busy = false; this.root.setAttribute('aria-busy', 'false');
      this.root.querySelectorAll('button').forEach(button => { button.disabled = false; });
      this.updatePagination();
    }
  }
}
