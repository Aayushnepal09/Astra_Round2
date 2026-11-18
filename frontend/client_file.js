import { SessionClient } from './shared/ui.js';

class ClientFileEditor {
  static fieldNames = new Map([
    ['reference', 'reference'],
    ['title', 'title'],
    ['client_name', 'clientName'],
    ['client_email', 'clientEmail'],
    ['signer_name', 'signerName'],
    ['signer_email', 'signerEmail'],
    ['template_version', 'templateVersionId'],
    ['company_signer_name', 'companySignerName'],
    ['company_signer_email', 'companySignerEmail'],
  ]);

  constructor(form, data) {
    this.form = form;
    this.client = new SessionClient();
    this.inputs = new Map([...form.querySelectorAll('input[name], select[name]')].map(input => [input.name, input]));
    this.errors = new Map([...this.inputs.keys()].map(name => [name, document.getElementById(`${name}-error`)]));
    this.fields = document.querySelector('#draft-fields');
    this.button = document.querySelector('#save-draft');
    this.buttonLabel = document.querySelector('#save-draft-label');
    this.saveError = document.querySelector('#draft-save-error');
    this.saveStatus = document.querySelector('#draft-save-status');
    this.conflictNotice = document.querySelector('#draft-conflict');
    this.reloadLatest = document.querySelector('#reload-latest-draft');
    this.draftConflict = false;
    this.reloadLatest.addEventListener('click', () => this.reloadLatestDraft());
    this.sessionError = document.querySelector('#draft-session-error');
    this.sessionRetry = document.querySelector('#retry-session');
    this.heading = document.querySelector('#draft-title');
    this.savedAt = document.querySelector('#draft-saved-at');
    this.unsavedNote = document.querySelector('#draft-unsaved');
    this.dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    this.saveUrl = document.body.dataset.saveUrl;
    this.companyId = document.body.dataset.companyId;
    this.creationKey = data.creationKey;
    this.isNew = !data.id;
    this.issued = data.status === 'awaiting_client';
    this.recoveryRequired = false;
    this.requestBusy = false;
    this.cancellationPending = false;
    this.cancelButton = document.querySelector('#cancel-request');
    this.prepareButton = document.querySelector('#prepare-replacement');
    this.stateReload = document.querySelector('#request-state-reload');
    this.cancelButton.addEventListener('click', () => this.changeRequest('cancel'));
    this.prepareButton.addEventListener('click', () => this.changeRequest('prepare'));
    this.stateReload.addEventListener('click', () => this.reloadLatestDraft());
    document.querySelector('#company-countersign').addEventListener('click', event => {
      if (this.requestBusy || this.cancellationPending) event.preventDefault();
    });
    this.sendButton = document.querySelector('#send-demo');
    this.invitation = null;
    this.sendButton.addEventListener('click', () => this.saveAndSend());
    document.querySelector('#file-summary-edit').addEventListener('click', () => {
      const details = document.querySelector('#draft-setup-details');
      details.open = true;
      details.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    document.querySelector('#finalize-agreement').addEventListener('click', () => this.finalize());
    document.querySelector('#demo-replace').addEventListener('click', () => this.requestInvitation(true));
    document.querySelector('#demo-refresh').addEventListener('click', () => this.loadInvitation());
    for (const kind of ['link', 'code', 'all']) document.querySelector(`#demo-copy-${kind}`).addEventListener('click', () => this.copyInvitation(kind));
    document.querySelector('#invitation-open').addEventListener('click', () => this.showInvitation());
    document.querySelector('#invitation-close').addEventListener('click', () => document.querySelector('#invitation-dialog').close());
    setInterval(() => this.renderCountdown(), 1000);
    const refreshIssuedStatus = () => {
      if (!document.hidden && this.sessionReady && !this.checkingSession && !this.requestBusy && !this.saving && !this.dirty
          && ['awaiting_client','awaiting_company','finalizing'].includes(this.invitation?.progress)) this.loadInvitation();
    };
    const statusTimer = setInterval(refreshIssuedStatus,15000);
    window.addEventListener('focus', refreshIssuedStatus);
    window.addEventListener('pagehide', () => clearInterval(statusTimer));
    this.checkingSession = false;
    this.sessionReady = false;
    this.saving = false;
    this.dirty = false;
    this.guardingLeave = false;
    this.allowLeave = false;
    this.templates = new Map();
    this.preparationInputs = new Map();
    this.preparationValues = {};
    this.templatePage = 1;
    this.templatesLoading = false;
    this.templatesStarted = false;
    this.beforeUnload = event => {
      event.preventDefault();
      event.returnValue = '';
    };
    this.applyFile(data);
    this.savedValues = JSON.stringify(this.readValues());
    this.form.addEventListener('input', event => this.handleInput(event));
    this.inputs.get('template_version').addEventListener('change', event => this.handleInput(event));
    document.querySelector('#template-load-more').addEventListener('click', () => this.loadTemplates());
    document.querySelector('#use-client-details').addEventListener('click', () => {
      for (const suffix of ['name', 'email']) this.inputs.get(`signer_${suffix}`).value = this.inputs.get(`client_${suffix}`).value;
      this.handleInput({ target: this.inputs.get('signer_name') });
      this.clearFieldError('signer_email');
      this.inputs.get('signer_name').focus();
    });
    this.form.addEventListener('submit', event => this.save(event));
    this.sessionRetry.addEventListener('click', () => this.refreshSession());
    window.addEventListener('pageshow', event => { if (event.persisted) this.refreshSession(); });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !this.isNew && !this.requestBusy) this.refreshSession();
    });
    this.refreshSession();
  }

  readValues() {
    return { ...Object.fromEntries([...this.inputs].map(([name, input]) => [name, input.value])),
      preparation_values: Object.fromEntries([...this.preparationInputs].map(([id, input]) => [id, input.value])) };
  }

  fileValues(file) {
    return { ...Object.fromEntries([...ClientFileEditor.fieldNames].map(([name, property]) => [name, file[property] || ''])),
      preparation_values: file.preparationValues || {} };
  }

  normalizedSnapshot(values) {
    return JSON.stringify({ ...Object.fromEntries([...ClientFileEditor.fieldNames.keys()].map(name => {
      const value = (values[name] || '').trim();
      return [name, name.endsWith('_email') ? value.toLowerCase() : value];
    })), preparation_values: Object.fromEntries(Object.entries(values.preparation_values || {}).sort(([a], [b]) => a.localeCompare(b)).map(([id, value]) => [id, value.trim()])) });
  }

  applyFile(file) {
    if (file.selectedTemplate) this.addTemplateOption(file.selectedTemplate);
    for (const [name, value] of Object.entries(this.fileValues(file))) {
      if (name !== 'preparation_values') this.inputs.get(name).value = value;
    }
    this.preparationValues = file.preparationValues || {};
    this.renderPreparation();
    this.renderTemplateSetup();
    this.updateFileMetadata(file);
  }

  updateFileMetadata(file) {
    if (file.id) {
      document.body.dataset.requestUrl = file.requestUrl;
      this.issued = file.status !== 'draft';
      this.savedUpdatedAt = file.updatedAt;
      this.loadedRequestId = file.currentRequestId || null;
      this.replacesRequestId = file.replacesRequestId || null;
      this.heading.textContent = file.job ? `${file.title || 'Agreement'} · ${file.clientName}` : file.clientName;
      document.title = `${file.clientName} · Astra`;
      const context = document.querySelector('#agreement-job-context');
      context.hidden = !file.job;
      if (file.job) {
        document.querySelector('#agreement-job-link').href = file.job.workspaceUrl;
        document.querySelector('#agreement-job-link').textContent = file.job.clientName;
        document.querySelector('#agreement-job-name').textContent = ` · Job/file ${file.job.reference}${file.job.title ? ' · ' + file.job.title : ''}`;
      }
    }
    this.renderRequestActions(file);
    const timestamp = Date.parse(file.updatedAt);
    this.savedAt.textContent = timestamp ? `Last saved ${this.dateFormat.format(timestamp)}` : 'Not saved yet';
  }

  handleInput(event) {
    const preparation = event.target.dataset.preparationId;
    if (!preparation && !this.inputs.has(event.target.name)) return;
    if (!preparation) this.clearFieldError(event.target.name);
    document.querySelector('#preparation_values-error').hidden = true;
    for (const input of this.preparationInputs.values()) input.removeAttribute('aria-invalid');
    this.saveError.hidden = true;
    this.saveStatus.hidden = true;
    if (event.target.name === 'template_version') {
      this.preparationValues = {};
      this.renderPreparation();
    }
    if (event.target.name === 'template_version' && !this.selectedTemplate()?.roles.includes('company')) {
      for (const name of ['company_signer_name', 'company_signer_email']) {
        this.inputs.get(name).value = '';
        this.clearFieldError(name);
      }
    }
    this.renderTemplateSetup();
    this.dirty = JSON.stringify(this.readValues()) !== this.savedValues;
    this.updateState();
  }

  updateState() {
    this.renderSummary();
    const busy = this.checkingSession || this.saving || this.requestBusy;
    this.fields.disabled = busy || !this.sessionReady || this.issued;
    this.button.disabled = busy || !this.sessionReady || this.issued || this.draftConflict || (!this.isNew && !this.dirty);
    // Missing setup is caught here, before the server has to reject the send.
    const setupMissing = this.requestState?.status === 'draft' && this.setupReady === false;
    this.sendButton.disabled = busy || !this.sessionReady || this.isNew || setupMissing || this.recoveryRequired || this.draftConflict
      || this.cancellationPending || ['cancelled', 'expired'].includes(this.requestState?.status) || (this.requestState?.status === 'draft' && this.issued);
    this.reloadLatest.disabled = busy || !this.sessionReady;
    this.stateReload.disabled = busy || !this.sessionReady;
    this.cancelButton.disabled = busy || !this.sessionReady;
    this.prepareButton.disabled = busy || !this.sessionReady || this.cancellationPending;
    this.cancelButton.textContent = this.requestBusy && this.cancellationPending ? 'Cancellation pending…' : 'Cancel request';
    this.sendButton.textContent = this.requestBusy ? 'Checking request…' : this.saving ? 'Saving…' : this.dirty ? 'Save & send to client' : 'Send to client';
    this.sendButton.setAttribute('aria-busy', String(this.requestBusy));
    document.querySelector('#demo-replace').disabled = busy || !this.sessionReady || !this.canReplaceCode();
    document.querySelector('#demo-refresh').disabled = busy || !this.sessionReady;
    document.querySelector('#finalize-agreement').disabled = busy || !this.sessionReady;
    document.querySelector('#company-countersign').setAttribute('aria-disabled', String(busy || this.cancellationPending));
    this.buttonLabel.textContent = this.saving ? 'Saving…' : this.checkingSession ? 'Checking session…' : 'Save draft';
    this.button.setAttribute('aria-busy', String(busy));
    this.form.setAttribute('aria-busy', String(busy));
    if (this.recoveryReload) this.recoveryReload.disabled = busy || !this.sessionReady;
    this.unsavedNote.hidden = !this.dirty;
    if (this.requestState?.status === 'draft') {
      document.querySelector('#file-status').textContent = this.dirty ? 'Unsaved changes' : this.setupReady ? 'Ready to send' : 'Needs details';
      document.querySelector('#demo-send-hint').textContent = setupMissing
        ? `Before sending: ${this.setupIssues.join(' ')}`
        : this.isNew
        ? 'Save this new file first. Then review the selected version, recipients and company values before issuing.'
        : this.dirty
        ? 'Your changes are saved first, then the invitation is sent to the client.'
        : 'Setup saved. Review the details above, then send the invitation to the client.';
    }
    const needsGuard = !this.allowLeave && (this.dirty || this.saving);
    if (needsGuard === this.guardingLeave) return;
    window[needsGuard ? 'addEventListener' : 'removeEventListener']('beforeunload', this.beforeUnload);
    this.guardingLeave = needsGuard;
  }

  requireIssuedReload() {
    if (this.recoveryRequired) return;
    this.recoveryRequired = true;
    const notice = document.createElement('section');
    notice.className = 'draft-section';
    notice.setAttribute('aria-labelledby', 'issued-recovery-heading');
    const heading = document.createElement('h2');
    heading.id = 'issued-recovery-heading';
    heading.textContent = 'Local draft entries — request already issued';
    const message = document.createElement('p');
    message.className = 'draft-section-copy';
    message.setAttribute('role', 'status');
    message.textContent = 'Another tab or an earlier send issued the saved version. The entries below are your local draft, not a confirmed view of the issued details. Unsaved edits in this tab were not applied to that request. Copy any entries you need before reloading to view the issued details.';
    this.recoveryReload = document.createElement('button');
    this.recoveryReload.type = 'button';
    this.recoveryReload.className = 'button secondary';
    this.recoveryReload.textContent = 'Reload issued details';
    this.recoveryReload.addEventListener('click', () => {
      if (this.recoveryReload.disabled) return;
      if (this.dirty && !window.confirm('Reload the issued details and discard the unsaved entries in this tab? Copy anything you need before continuing.')) return;
      this.allowLeave = true;
      this.updateState();
      window.location.reload();
    });
    notice.append(heading, message, this.recoveryReload);
    this.fields.before(notice);
    this.saveStatus.hidden = true;
    this.savedAt.textContent = 'Local draft shown — reload to view issued details';
  }

  reloadLatestDraft() {
    if (this.reloadLatest.disabled) return;
    if (this.dirty && !window.confirm('Load the latest saved details and discard the unsaved entries in this tab? Copy anything you need before continuing.')) return;
    this.allowLeave = true;
    this.updateState();
    window.location.reload();
  }

  canReplaceCode() {
    const invitation = this.invitation;
    return Boolean(!this.cancellationPending && invitation?.invitationId && invitation.accessAvailable && invitation.canReplace
      && ['expired', 'locked'].includes(invitation.state)
      && (invitation.progress === 'awaiting_client' || invitation.readOnly));
  }

  renderRequestActions(file) {
    if (!file) return;
    this.requestState = file;
    const changed = (file.currentRequestId || null) !== (this.loadedRequestId || null) || (file.status === 'draft' && this.issued);
    this.cancelButton.hidden = !file.canCancelRequest || changed;
    this.prepareButton.hidden = !file.canPrepareReplacement || changed;
    this.stateReload.hidden = !changed;
    const history = document.querySelector('#request-history');
    history.hidden = !file.id;
    if (file.requestHistoryUrl) history.href = file.requestHistoryUrl;
    document.querySelector('#demo-refresh').hidden = !file.id;
    document.querySelector('#request-options').hidden = !file.id;
    document.querySelector('#request-lifecycle-status').textContent = changed
      ? 'The saved request state changed. Load the latest saved details before continuing. Your local entries are still here.'
      : file.status === 'cancelled' ? 'Request cancelled. Further signing has stopped; existing signing records are retained.'
      : file.status === 'expired' ? 'Signing deadline passed. Prepare a replacement to collect fresh signatures. Earlier signing records are retained.'
      : file.replacesRequestId ? 'Replacement draft. Review the template, recipients and company-prepared values, save your setup, then select Send to client. Earlier signatures and consent are not reused.' : '';
    if (['cancelled', 'expired'].includes(file.status)) this.issued = true;
    this.renderStage(file.status);
  }

  // Progress follows the authoritative request status; the countersign step shows only when the version needs it.
  renderProgress(status) {
    const order = ['draft', 'awaiting_client', 'awaiting_company', 'finalizing', 'completed'];
    const current = order.indexOf(status);
    const countersign = !document.querySelector('#company-signer-section').hidden;
    let number = 0;
    document.querySelectorAll('[data-progress]').forEach(step => {
      const index = order.indexOf(step.dataset.progress);
      step.hidden = step.dataset.progress === 'awaiting_company' && !countersign;
      if (!step.hidden) step.querySelector('span').textContent = String(++number);
      step.dataset.done = String(current >= 0 && (index < current || status === 'completed'));
      if (index === current && status !== 'completed') step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    });
  }

  renderStage(status) {
    const copy = {
      draft: ['Review & issue', 'Review the template, recipients and company-prepared values. Save your setup before sending.', 'Saving setup does not issue a request. Sending freezes the selected version and recipients.'],
      awaiting_client: ['Awaiting the client', 'The client has been invited to review and sign. Use View invitation link & code to share access again.', 'Issued details are fixed. Cancel an unfinished request before preparing corrected setup.'],
      awaiting_company: ['Your company signature is needed', 'The client’s signature is recorded. Review the agreement and complete the company countersignature.', 'The existing client signature is preserved if this request is cancelled. A replacement requires fresh signatures.'],
      finalizing: ['Save the final records', 'All required signatures are recorded. Generate the completed PDF and signing history to finish this file.', 'If processing fails, retry finalization. No new signatures are needed.'],
      completed: ['Completed documents', 'The completed PDF and signing history are ready. Open or download the final records below.', 'This completed request is read-only. Its documents and signing history are retained.'],
      cancelled: ['Prepare a replacement', 'This request is cancelled. Select Prepare replacement to correct its setup, then review and explicitly send a new request.', 'Earlier signing records are retained. A replacement starts with fresh signatures and consent.'],
      expired: ['Prepare a replacement', 'The signing deadline has passed. Select Prepare replacement, review its setup, and explicitly send a new request.', 'Access-code replacement cannot extend a request. Earlier signing records are retained; replacement requires fresh signatures.'],
    }[status];
    if (!copy) return;
    document.querySelector('#draft-stage-copy').textContent = copy[1];
    document.querySelector('#draft-guidance-heading').textContent = copy[0];
    document.querySelector('#demo-request-heading').textContent = copy[0];
    document.querySelector('#draft-guidance-copy').textContent = copy[2];
    document.body.dataset.fileStatus = status;
    this.renderProgress(status);
    // A saved, complete draft starts collapsed behind the summary card; anything unfinished stays open.
    document.querySelector('#draft-setup-details').open = (status === 'draft' && !(this.setupReady && !this.isNew)) || this.recoveryRequired || this.dirty;
    document.querySelector('#demo-send-hint').textContent = status === 'draft' ? copy[2] : '';
    this.sendButton.hidden = status !== 'draft';
    this.button.hidden = status !== 'draft';
  }

  async changeRequest(action) {
    const button = action === 'cancel' ? this.cancelButton : this.prepareButton;
    if (button.disabled || button.hidden || !this.requestState?.currentRequestId) return;
    if (action === 'cancel' && !window.confirm('Cancel this request? Further signing will stop. Existing signing records, including any accepted signatures, will be retained.')) return;
    if (action === 'prepare' && this.dirty && !window.confirm('Prepare replacement setup and discard the unsaved entries in this tab? Copy anything you need before continuing.')) return;
    this.requestBusy = true;
    if (action === 'cancel') this.cancellationPending = true;
    this.updateState();
    document.querySelector('#demo-error').hidden = true;
    document.querySelector('#demo-status').textContent = action === 'cancel' ? 'Cancellation pending…' : 'Preparing replacement setup…';
    try {
      const result = await this.client.request(action === 'cancel' ? this.requestState.cancelUrl : this.requestState.replacementUrl,
        { workspace_id: this.companyId, requestId: this.requestState.currentRequestId });
      this.cancellationPending = false;
      if (action === 'prepare') {
        this.allowLeave = true;
        this.updateState();
        window.location.reload();
        return;
      }
      this.renderRequestActions(result.file);
      this.renderInvitation(result.invitation);
      if (result.outcome === 'signatures_complete') {
        document.querySelector('#demo-status').textContent = 'All required signatures were already recorded. The request was not cancelled. Continue finalization; no replacement was prepared.';
      }
    } catch (error) {
      this.requestError(error);
      document.querySelector('#demo-status').textContent = 'The request state could not be confirmed. Refresh status before continuing, or retry the same action.';
    } finally {
      this.requestBusy = false;
      this.updateState();
    }
  }

  clearFieldError(name) {
    this.inputs.get(name).removeAttribute('aria-invalid');
    this.errors.get(name).hidden = true;
  }

  showFieldError(name, message) {
    if (!this.inputs.has(name)) return;
    this.inputs.get(name).setAttribute('aria-invalid', 'true');
    const error = this.errors.get(name);
    error.textContent = message;
    error.hidden = false;
  }

  validate() {
    let firstInvalid;
    for (const [name, input] of this.inputs) {
      this.clearFieldError(name);
      const value = input.value.trim();
      let message = '';
      if (input.required && !value) message = name === 'reference' ? 'Enter a file reference.' : 'Enter the client’s name.';
      else if (input.maxLength > 0 && value.length > input.maxLength) message = `Use ${input.maxLength} characters or fewer.`;
      else if (!input.validity.valid) message = input.type === 'email' ? 'Enter a valid email address, or leave this field blank.' : 'Check this value.';
      if (!message) continue;
      this.showFieldError(name, message);
      firstInvalid ??= input;
    }
    firstInvalid?.focus();
    return !firstInvalid;
  }

  async refreshSession() {
    if (this.checkingSession || this.saving) return;
    this.checkingSession = true;
    this.sessionError.hidden = true;
    this.sessionRetry.disabled = true;
    this.updateState();
    try {
      const session = await this.client.request(document.body.dataset.sessionUrl);
      if (session.companyId !== this.companyId) {
        this.showSessionError('You are signed in to a different company workspace. Sign in to this file’s company in a new tab, then select Try again. Your entered details are still here.');
        return;
      }
      this.sessionReady = true;
      if (!this.isNew) await this.loadInvitation();
      if (!this.templatesStarted) {
        this.templatesStarted = true;
        this.loadTemplates();
      }
    } catch (error) {
      this.sessionReady = false;
      this.showSessionError(`${error.message} Your entered details are still here. Sign in if needed, then select Try again to continue saving.`);
    } finally {
      this.checkingSession = false;
      this.sessionRetry.disabled = false;
      this.updateState();
    }
  }

  showSessionError(message) {
    this.sessionReady = false;
    this.invitation = null;
    document.querySelector('#demo-invitation').hidden = true;
    document.querySelector('#company-countersign').hidden = true;
    document.querySelector('#file-completion').hidden = true;
    document.querySelector('#demo-code').value = '';
    document.querySelector('#draft-session-error-message').textContent = message;
    this.sessionError.hidden = false;
    this.sessionError.scrollIntoView({ block: 'nearest' });
  }

  selectedTemplate() {
    return this.templates.get(this.inputs.get('template_version').value);
  }

  addTemplateOption(version) {
    this.templates.set(version.id, version);
    const select = this.inputs.get('template_version');
    if (![...select.options].some(option => option.value === version.id)) {
      select.add(new Option(`${version.name} · Version ${version.number}`, version.id));
    }
  }

  // Compact read-out of the setup, kept in sync with the form so staff can check it at a glance.
  renderSummary() {
    const summary = document.querySelector('#file-summary');
    const version = this.inputs?.get('template_version');
    summary.hidden = !version || this.isNew || !version.value;
    if (summary.hidden) return;
    const value = name => this.inputs.get(name)?.value.trim() || '';
    const person = (name, email) => [name, email && `(${email})`].filter(Boolean).join(' ');
    const rows = [['Agreement', version.selectedOptions?.[0]?.textContent || ''], ['Client signer', person(value('signer_name'), value('signer_email'))]];
    if (!document.querySelector('#company-signer-section').hidden) rows.push(['Countersigner', person(value('company_signer_name'), value('company_signer_email'))]);
    for (const field of this.selectedTemplate()?.preparationFields || []) rows.push([field.label || field.type, this.preparationInputs.get(field.id)?.value.trim() || '']);
    document.querySelector('#file-summary-list').replaceChildren(...rows.flatMap(([term, detail]) => {
      const dt = document.createElement('dt');
      const dd = document.createElement('dd');
      dt.textContent = term;
      dd.textContent = detail || 'Not set yet';
      dd.classList.toggle('missing', !detail);
      return [dt, dd];
    }));
    document.querySelector('#file-summary-edit').textContent = this.issued || (this.requestState && this.requestState.status !== 'draft') ? 'View full setup' : 'Edit setup';
  }

  renderPreparation() {
    const fields = this.selectedTemplate()?.preparationFields || [];
    const container = document.querySelector('#preparation-inputs');
    container.replaceChildren();
    this.preparationInputs.clear();
    document.querySelector('#preparation-section').hidden = !fields.length;
    for (const field of fields) {
      const row = document.createElement('div');
      row.className = 'field';
      const label = document.createElement('label');
      label.htmlFor = `preparation-${field.id}`;
      label.textContent = `${field.label || field.type} · Page ${field.page} · ${field.required ? 'Required before sending' : 'Optional'}`;
      const input = document.createElement(field.type === 'text' ? 'textarea' : 'input');
      input.id = label.htmlFor;
      input.dataset.preparationId = field.id;
      if (field.type !== 'text') input.type = field.type === 'date' ? 'date' : 'text';
      input.autocomplete = 'off';
      input.value = this.preparationValues[field.id] || '';
      input.setAttribute('aria-describedby', 'preparation_values-error');
      const wrap = document.createElement('div');
      wrap.className = 'input-wrap';
      wrap.append(input);
      row.append(label, wrap);
      container.append(row);
      this.preparationInputs.set(field.id, input);
    }
  }

  renderTemplateSetup() {
    const prepared = this.selectedTemplate();
    const preparationStatus = document.querySelector('#file-preparation-status');
    preparationStatus.hidden = !prepared || this.recoveryRequired;
    preparationStatus.textContent = prepared ? `Template: ${prepared.name} · Version ${prepared.number}` : '';
    if (this.recoveryRequired) {
      const item = document.createElement('li');
      item.textContent = 'These are local draft entries. Reload issued details to review the frozen agreement setup.';
      document.querySelector('#agreement-setup-issues').replaceChildren(item);
      return;
    }
    const version = this.selectedTemplate();
    document.querySelector('#company-signer-section').hidden = !version?.roles.includes('company');
    if (document.body.dataset.fileStatus) this.renderProgress(document.body.dataset.fileStatus);
    const preview = document.querySelector('#selected-template-preview');
    preview.hidden = !version;
    if (version) preview.href = version.previewUrl;
    const issues = [];
    if (!version) issues.push('Select a published template version.');
    else {
      if (!this.inputs.get('signer_name').value.trim()) issues.push("Add the client signer's name.");
      if (!this.inputs.get('signer_email').value.trim()) issues.push("Add the client signer's email.");
      for (const field of version.preparationFields || []) {
        if (field.required && !this.preparationInputs.get(field.id)?.value.trim()) issues.push(`Complete company preparation: ${field.label || field.type}.`);
      }
      if (version.roles.includes('company')) {
        if (!this.inputs.get('company_signer_name').value.trim()) issues.push("Add the company countersigner's name.");
        if (!this.inputs.get('company_signer_email').value.trim()) issues.push("Add the company countersigner's email.");
      }
    }
    const list = document.querySelector('#agreement-setup-issues');
    this.setupReady = !issues.length;
    this.setupIssues = issues;
    list.replaceChildren();
    for (const message of issues.length ? issues : [this.issued ? 'Request issued. The selected version and recipients are fixed.' : 'Signer setup details are present. No request has been created.']) {
      const item = document.createElement('li');
      item.textContent = message;
      list.append(item);
    }
  }

  async loadTemplates() {
    if (this.templatesLoading) return;
    this.templatesLoading = true;
    const button = document.querySelector('#template-load-more');
    const status = document.querySelector('#template-version-status');
    const errorElement = document.querySelector('#template-list-error');
    button.disabled = true;
    errorElement.hidden = true;
    status.textContent = 'Loading published versions…';
    try {
      const url = new URL(document.body.dataset.templatesUrl, location.origin);
      url.search = new URLSearchParams({ workspace_id: this.companyId, page: this.templatePage });
      if (this.requestState?.jobId) url.searchParams.set('job_id', this.requestState.jobId);
      const result = await this.client.request(url);
      if (result.companyId !== this.companyId) throw new Error('Your workspace has changed. Sign in to this file’s workspace and retry.');
      for (const version of result.versions) this.addTemplateOption(version);
      this.templatePage = result.pagination.number + 1;
      button.hidden = !result.pagination.hasNext;
      button.textContent = 'Load more versions';
      status.textContent = result.pagination.total
        ? `${this.templates.size} of ${result.pagination.total} published versions loaded. Select a specific version; it will stay fixed.`
        : 'No published versions yet. Publish an agreement under Agreement templates, then reload this list.';
      if (!result.pagination.total) {
        button.hidden = false;
        button.textContent = 'Refresh published versions';
        this.templatePage = 1;
      }
      this.renderTemplateSetup();
    } catch (error) {
      status.textContent = 'Published versions could not be loaded. You can still save an incomplete draft.';
      errorElement.textContent = `${error.message} Your current selection and entered details are still here.`;
      errorElement.hidden = false;
      button.hidden = false;
      button.textContent = 'Retry loading versions';
    } finally {
      this.templatesLoading = false;
      button.disabled = false;
    }
  }

  // Saves pending edits, then issues; a failed save or validation stops before any request is created.
  async saveAndSend() {
    if (this.dirty) {
      await this.save(new Event('submit'));
      if (this.dirty) return;
    }
    return this.requestInvitation();
  }

  async save(event) {
    event.preventDefault();
    if (this.button.disabled) return;
    this.saveError.hidden = true;
    this.saveStatus.hidden = true;
    if (!this.validate()) return;
    const data = this.readValues();
    for (const name of this.inputs.keys()) data[name] = data[name].trim();
    data.workspace_id = this.companyId;
    if (this.isNew) data.creation_key = this.creationKey;
    else data.updatedAt = this.savedUpdatedAt;
    this.saving = true;
    this.updateState();
    let firstInvalid;
    try {
      const result = await this.client.request(this.saveUrl, data);
      const file = result.file;
      if (this.isNew) {
        const destination = new URL(file.detailUrl, window.location.origin);
        if (destination.origin !== window.location.origin) throw new Error('The file was saved, but its page could not be opened. Return to client files to find it.');
        if (result.created === false && this.normalizedSnapshot(data) !== this.normalizedSnapshot(this.fileValues(file))) {
          this.saveUrl = destination.href;
          this.isNew = false;
          this.creationKey = null;
          window.history.replaceState(null, '', destination.href);
          this.updateFileMetadata(file);
          this.savedValues = JSON.stringify(this.fileValues(file));
          this.dirty = JSON.stringify(this.readValues()) !== this.savedValues;
          this.saveStatus.textContent = 'A previous save created this draft. Your current edits are still here. Select Save draft to apply them.';
          this.saveStatus.dataset.state = 'recovery';
          this.saveStatus.hidden = false;
          return;
        }
        this.allowLeave = true;
        this.updateState();
        window.location.replace(destination.href);
        return;
      }
      this.applyFile(file);
      this.savedValues = JSON.stringify(this.readValues());
      this.dirty = false;
      this.saveStatus.textContent = 'Draft saved. No signing request was created.';
      globalThis.showToast?.('Draft saved');
      this.saveStatus.dataset.state = 'saved';
      this.saveStatus.hidden = false;
    } catch (error) {
      if (error.status === 409 && error.code === 'draft_conflict') {
        this.draftConflict = true;
        this.conflictNotice.hidden = false;
        return;
      }
      if (error.status === 401 || error.status === 403 || error.code === 'workspace_changed') {
        const message = error.status === 403 ? 'Your sign-in session has changed.' : error.message;
        this.showSessionError(`${message} Your entered details are still here. Sign in to this file’s company in a new tab, then select Try again.`);
        return;
      }
      for (const [name, messages] of Object.entries(error.fieldErrors || {})) {
        if (name === 'preparation_values') {
          const element = document.querySelector('#preparation_values-error');
          element.textContent = Array.isArray(messages) ? messages.join(' ') : String(messages);
          element.hidden = false;
          for (const input of this.preparationInputs.values()) input.setAttribute('aria-invalid', 'true');
          firstInvalid ??= this.preparationInputs.values().next().value;
          continue;
        }
        if (!this.inputs.has(name)) continue;
        this.showFieldError(name, Array.isArray(messages) ? messages.join(' ') : String(messages));
        firstInvalid ??= this.inputs.get(name);
      }
      this.saveError.textContent = `${error.message} Your entered details are still here.`;
      this.saveError.hidden = false;
    } finally {
      this.saving = false;
      this.updateState();
      firstInvalid?.focus();
    }
  }

  async loadInvitation() {
    if (this.isNew || this.requestBusy) return;
    this.requestBusy = true;
    this.updateState();
    try {
      const result = await this.client.request(document.body.dataset.requestUrl);
      this.cancellationPending = false;
      this.renderRequestActions(result.file);
      this.renderInvitation(result.invitation);
      document.querySelector('#demo-error').hidden = true;
    } catch (error) {
      this.requestError(error);
    } finally {
      this.requestBusy = false;
      this.updateState();
    }
  }

  async requestInvitation(replace = false) {
    if (this.checkingSession || this.saving || this.requestBusy || !this.sessionReady || this.isNew) return;
    if (replace ? !this.canReplaceCode() : this.sendButton.disabled) return;
    this.requestBusy = true;
    this.updateState();
    document.querySelector('#demo-error').hidden = true;
    document.querySelector('#demo-status').textContent = replace ? 'Generating a replacement code…' : 'Checking setup and issuing the request…';
    const payload = { workspace_id: this.companyId };
    if (!replace) {
      payload.templateVersionId = this.inputs.get('template_version').value;
      payload.updatedAt = this.savedUpdatedAt;
      payload.replacesRequestId = this.replacesRequestId || null;
    }
    if (replace) payload.replaceInvitationId = this.invitation.invitationId;
    try {
      const result = await this.client.request(document.body.dataset.requestUrl, payload);
      if (!replace && result.created === true) this.loadedRequestId = result.file.currentRequestId;
      this.renderRequestActions(result.file);
      this.renderInvitation(result.invitation, { issuedHere: !replace && result.created === true });
      globalThis.showToast?.(replace ? 'New access code ready' : 'Request sent. Share the link and code below.');
    } catch (error) {
      this.requestError(error);
    } finally {
      this.requestBusy = false;
      this.updateState();
    }
  }

  requestError(error) {
    const element = document.querySelector('#demo-error');
    element.textContent = error.message;
    element.hidden = false;
    if (error.fieldErrors?.preparation_values) {
      const preparationError = document.querySelector('#preparation_values-error');
      preparationError.textContent = error.fieldErrors.preparation_values.join(' ');
      preparationError.hidden = false;
      for (const input of this.preparationInputs.values()) input.setAttribute('aria-invalid', 'true');
      this.preparationInputs.values().next().value?.focus();
    }
    document.querySelector('#demo-status').textContent = [400, 409, 422].includes(error.status)
      ? 'Not sent. Fix the details above, then send again.'
      : 'Request not confirmed. Retry or refresh status to recover the existing request.';
    if ([401, 403].includes(error.status) || error.code === 'workspace_changed') this.showSessionError(error.message);
  }

  renderInvitation(invitation, { issuedHere = false } = {}) {
    this.invitation = invitation;
    document.querySelector('#demo-invitation').hidden = !invitation || ['cancelled', 'expired'].includes(invitation.progress);
    const deadline = document.querySelector('#request-deadline');
    deadline.hidden = !invitation?.requestExpiresAt;
    deadline.textContent = invitation?.requestExpiresAt
      ? `Signing deadline: ${this.dateFormat.format(Date.parse(invitation.requestExpiresAt))}. Access-code replacement does not extend it.` : '';
    const countersign = document.querySelector('#company-countersign');
    countersign.hidden = !invitation?.companySigningUrl;
    if (invitation?.companySigningUrl) {
      countersign.href = invitation.companySigningUrl;
      countersign.textContent = ['finalizing', 'completed'].includes(invitation.progress) ? 'Review recorded company submission' : 'Sign for company';
      countersign.classList.toggle('secondary', invitation.progress !== 'awaiting_company');
    }
    if (!invitation) return;
    this.renderStage(invitation.progress);
    document.querySelector('#invitation-open').textContent = invitation.readOnly ? 'View client access link & code' : 'View invitation link & code';
    document.querySelector('#invitation-open').classList.toggle('secondary', invitation.progress !== 'awaiting_client');
    if (this.renderedRequestId !== invitation.requestId) {
      // A freshly sent invitation opens straight away; reloads keep it one click away.
      if (issuedHere && invitation.progress === 'awaiting_client') this.showInvitation();
      this.renderedRequestId = invitation.requestId;
    }
    if (['cancelled', 'expired'].includes(invitation.progress)) {
      this.issued = true;
      document.querySelector('#file-status').textContent = invitation.progress === 'expired' ? 'Expired' : 'Cancelled';
      document.querySelector('#demo-status').textContent = invitation.progress === 'expired'
        ? 'Request expired. Prepare a replacement to collect fresh signatures.'
        : 'Request cancelled. Prepare a replacement when you are ready to correct its setup.';
      document.querySelector('#demo-code').value = '';
      this.renderCompletion(null);
      return;
    }
    if (this.loadedRequestId && this.loadedRequestId !== invitation.requestId && !issuedHere) this.requireIssuedReload();
    if (!this.issued && !issuedHere) this.requireIssuedReload();
    this.issued = true;
    if (invitation.selectedTemplate && !this.recoveryRequired) {
      this.addTemplateOption(invitation.selectedTemplate);
      this.inputs.get('template_version').value = invitation.templateVersionId;
      this.preparationValues = invitation.preparationValues || {};
      this.renderPreparation();
    }
    document.querySelector('#file-status').textContent = ({ awaiting_company: 'Company signature needed', finalizing: 'Finalizing', completed: 'Completed' })[invitation.progress] || 'Awaiting client';
    this.renderCompletion(invitation.finalization);
    if (this.recoveryRequired) document.querySelector('#demo-send-hint').textContent = 'Reload issued details to review the fixed agreement setup. Your local draft entries are still here.';
    this.renderTemplateSetup();
    document.querySelector('#demo-link').value = new URL(invitation.link, location.origin).href;
    document.querySelector('#demo-recipient').textContent = invitation.recipient
      ? `${invitation.recipient.name} <${invitation.recipient.email}>` : '';
    document.querySelector('#demo-subject').textContent = invitation.readOnly ? 'Your recorded agreement' : 'Please review and sign your agreement';
    document.querySelector('#demo-message').textContent = invitation.readOnly
      ? 'Your signature is recorded. Use the link and access code below to review your agreement. Access is read-only.'
      : 'Please review the agreement, complete your required fields, and select Sign & submit. Open the link below and enter the access code to begin.';
    document.querySelector('#demo-open').href = invitation.link;
    document.querySelector('#demo-open').textContent = invitation.readOnly ? 'Open client’s recorded agreement' : 'Open client signing page';
    document.querySelector('#demo-open').classList.toggle('secondary', invitation.progress !== 'awaiting_client');
    document.querySelector('#demo-code').value = invitation.code || '';
    for (const id of ['#demo-copy-code', '#demo-copy-all']) document.querySelector(id).disabled = !invitation.code;
    document.querySelector('#demo-replace').hidden = !this.canReplaceCode();
    document.querySelector('label[for="demo-link"]').textContent = invitation.readOnly ? 'Read-only client access link' : 'Signing link';
    this.remainingAtLoad = Date.parse(invitation.expiresAt) - Date.parse(invitation.serverNow);
    this.requestRemainingAtLoad = invitation.requestExpiresAt
      ? Date.parse(invitation.requestExpiresAt) - Date.parse(invitation.serverNow) : Infinity;
    this.countdownStarted = performance.now();
    const messages = {
      pending: invitation.readOnly ? 'Verify this code to restore read-only client access. It does not reopen signing.' : 'Request created · awaiting client. Opening the link still requires code verification.',
      expired: 'Code expired. Get a replacement code for this same request.',
      locked: 'Code locked after unsuccessful attempts. Get a replacement code for this same request.',
      verified: invitation.readOnly ? 'The client can reuse this code until it expires to view their recorded agreement.' : 'Access verified. No signature has been recorded yet. The client can reuse this link and code until it expires.',
      replaced: 'This code was replaced. Refresh status to load the current invitation.',
      request_expired: 'This signing request has expired. Contact the company; a replacement code cannot extend it.',
    };
    const progress = {
      awaiting_company: 'Client submission recorded. Awaiting company countersignature; the completed PDF is not yet available.',
      finalizing: 'All required signatures recorded. Final processing is pending; the completed PDF is not yet available.',
      completed: 'Completed PDF and signing history saved. Client access is read-only.',
    }[invitation.progress];
    document.querySelector('#demo-status').textContent = [progress, messages[invitation.state]].filter(Boolean).join(' ');
    this.renderCountdown();
  }

  renderCompletion(state) {
    const visible = state && ['finalizing', 'completed'].includes(state.state);
    document.querySelector('#file-completion').hidden = !visible;
    if (!visible) return;
    const complete = state.state === 'completed';
    document.querySelector('#completion-status').textContent = complete
      ? `Completed PDF and signing history saved ${this.dateFormat.format(Date.parse(state.completedAt))}.`
      : 'All required signatures are recorded. The completed PDF and signing history are still pending.';
    document.querySelector('#completion-error').textContent = state.error || '';
    document.querySelector('#completion-error').hidden = !state.error;
    const retry = document.querySelector('#finalize-agreement');
    retry.hidden = complete;
    retry.textContent = state.error ? 'Retry finalization' : 'Generate final agreement';
    document.querySelector('#completion-downloads').hidden = !complete;
    if (complete) {
      document.querySelector('#completed-pdf-view').href = state.pdfUrl;
      document.querySelector('#completed-pdf-download').href = `${state.pdfUrl}?download=1`;
      document.querySelector('#signing-history-view').href = state.historyUrl;
      document.querySelector('#signing-history-download').href = `${state.historyUrl}?download=1`;
    }
  }

  async finalize() {
    if (this.requestBusy || !this.sessionReady || this.invitation?.progress !== 'finalizing') return;
    this.requestBusy = true;
    this.updateState();
    document.querySelector('#completion-error').hidden = true;
    document.querySelector('#completion-status').textContent = 'Generating, saving and verifying the completed agreement…';
    try {
      const result = await this.client.request(this.invitation.finalization.retryUrl, { workspace_id: this.companyId });
      this.renderInvitation({ ...this.invitation, progress: result.finalization.state, finalization: result.finalization });
    } catch (error) {
      document.querySelector('#completion-error').textContent = error.message;
      document.querySelector('#completion-error').hidden = false;
      document.querySelector('#completion-status').textContent = 'Completion is not confirmed. Refresh status or retry finalization safely; recorded signatures are preserved.';
      if ([401, 403].includes(error.status)) this.showSessionError(error.message);
    } finally {
      this.requestBusy = false;
      this.updateState();
    }
  }

  renderCountdown() {
    if (!this.invitation || ['cancelled', 'expired'].includes(this.invitation.progress)) return;
    if (['awaiting_client', 'awaiting_company'].includes(this.invitation.progress)
        && this.requestRemainingAtLoad <= performance.now() - this.countdownStarted) {
      document.querySelector('#demo-code').value = '';
      for (const id of ['#demo-copy-code', '#demo-copy-all']) document.querySelector(id).disabled = true;
      document.querySelector('#demo-open').removeAttribute('href');
      document.querySelector('#demo-replace').hidden = true;
      document.querySelector('#demo-expiry').textContent = 'Signing deadline reached. Refresh status to confirm the request outcome.';
      const key = `${this.invitation.requestId}:${this.invitation.requestExpiresAt}`;
      if (!this.requestBusy && this.expiryRefreshKey !== key) {
        this.expiryRefreshKey = key;
        void this.loadInvitation();
      }
      return;
    }
    const seconds = Math.max(0, Math.ceil((this.remainingAtLoad - (performance.now() - this.countdownStarted)) / 1000));
    const expiry = document.querySelector('#demo-expiry');
    expiry.textContent = this.invitation.state === 'request_expired' ? 'Request expired. Replacement codes cannot extend its deadline.'
      : this.invitation.state === 'locked' ? 'Code locked. Verification is unavailable with this code.'
      : seconds ? `Code expires in ${Math.floor(seconds / 3600)}h ${Math.floor(seconds / 60) % 60}m ${seconds % 60}s · ${this.dateFormat.format(Date.parse(this.invitation.expiresAt))}`
      : 'Code expired. The server requires a replacement code.';
    if (!seconds && ['pending', 'verified'].includes(this.invitation.state)) {
      this.invitation.state = 'expired';
      this.invitation.canReplace = Boolean(this.invitation.accessAvailable
        && (this.invitation.progress === 'awaiting_client' || this.invitation.readOnly));
      document.querySelector('#demo-code').value = '';
      for (const id of ['#demo-copy-code', '#demo-copy-all']) document.querySelector(id).disabled = true;
      document.querySelector('#demo-replace').hidden = !this.canReplaceCode();
      document.querySelector('#demo-status').textContent = this.invitation.readOnly
        ? 'Read-only access code expired. Get a replacement code for this same agreement; the recorded signature is preserved.'
        : 'Code expired. Get a replacement code for this same request.';
      this.updateState();
    }
  }

  showInvitation() {
    const dialog = document.querySelector('#invitation-dialog');
    document.querySelector('#invitation-copy-status').textContent = '';
    if (!dialog.open) dialog.showModal?.();
  }

  // 'all' copies a ready-to-paste message with both the link and the code.
  async copyInvitation(kind) {
    const input = document.querySelector(`#demo-${kind === 'all' ? 'link' : kind}`);
    const text = kind === 'all'
      ? `Please review and sign your agreement.
Signing link: ${input.value}
Access code: ${document.querySelector('#demo-code').value}`
      : input.value;
    try {
      await navigator.clipboard.writeText(text);
      const copied = { link: 'Link copied.', code: 'Code copied.', all: 'Link and code copied, ready to paste.' }[kind];
      document.querySelector('#demo-status').textContent = copied;
      document.querySelector('#invitation-copy-status').textContent = copied;
    } catch {
      input.focus();
      input.select();
      document.querySelector('#demo-status').textContent = 'Copy is unavailable. The value is selected; use your device’s copy command.';
      document.querySelector('#invitation-copy-status').textContent = 'Copy is unavailable. The value is selected; use your device’s copy command.';
    }
  }
}

new ClientFileEditor(document.querySelector('#draft-form'), JSON.parse(document.querySelector('#draft-data').textContent));
