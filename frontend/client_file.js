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
    this.requestBusy = false;
    this.sendButton = document.querySelector('#send-demo');
    this.invitation = null;
    this.sendButton.addEventListener('click', () => this.requestInvitation());
    document.querySelector('#finalize-agreement').addEventListener('click', () => this.finalize());
    document.querySelector('#demo-replace').addEventListener('click', () => this.requestInvitation(true));
    document.querySelector('#demo-refresh').addEventListener('click', () => this.loadInvitation());
    for (const kind of ['link', 'code']) document.querySelector(`#demo-copy-${kind}`).addEventListener('click', () => this.copyInvitation(kind));
    setInterval(() => this.renderCountdown(), 1000);
    this.checkingSession = false;
    this.sessionReady = false;
    this.saving = false;
    this.dirty = false;
    this.guardingLeave = false;
    this.allowLeave = false;
    this.templates = new Map();
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
    return Object.fromEntries([...this.inputs].map(([name, input]) => [name, input.value]));
  }

  fileValues(file) {
    return Object.fromEntries([...ClientFileEditor.fieldNames].map(([name, property]) => [name, file[property] || '']));
  }

  normalizedSnapshot(values) {
    return JSON.stringify(Object.fromEntries([...ClientFileEditor.fieldNames.keys()].map(name => {
      const value = (values[name] || '').trim();
      return [name, name.endsWith('_email') ? value.toLowerCase() : value];
    })));
  }

  applyFile(file) {
    if (file.selectedTemplate) this.addTemplateOption(file.selectedTemplate);
    for (const [name, value] of Object.entries(this.fileValues(file))) this.inputs.get(name).value = value;
    this.renderTemplateSetup();
    this.updateFileMetadata(file);
  }

  updateFileMetadata(file) {
    if (file.id) {
      document.body.dataset.requestUrl = file.requestUrl;
      this.issued = file.status !== 'draft';
      this.heading.textContent = file.clientName;
      document.title = `${file.clientName} · Astra`;
    }
    const timestamp = Date.parse(file.updatedAt);
    this.savedAt.textContent = timestamp ? `Last saved ${this.dateFormat.format(timestamp)}` : 'Not saved yet';
  }

  handleInput(event) {
    if (!this.inputs.has(event.target.name)) return;
    this.clearFieldError(event.target.name);
    this.saveError.hidden = true;
    this.saveStatus.hidden = true;
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
    const busy = this.checkingSession || this.saving || this.requestBusy;
    this.fields.disabled = busy || !this.sessionReady || this.issued;
    this.button.disabled = busy || !this.sessionReady || this.issued || (!this.isNew && !this.dirty);
    this.sendButton.disabled = busy || !this.sessionReady || this.isNew || this.dirty;
    this.sendButton.textContent = this.requestBusy ? 'Checking request…' : 'Send request — Demo';
    this.sendButton.setAttribute('aria-busy', String(this.requestBusy));
    document.querySelector('#demo-replace').disabled = busy || !this.sessionReady;
    document.querySelector('#demo-refresh').disabled = busy || !this.sessionReady;
    document.querySelector('#finalize-agreement').disabled = busy || !this.sessionReady;
    this.buttonLabel.textContent = this.saving ? 'Saving…' : this.checkingSession ? 'Checking session…' : 'Save draft';
    this.button.setAttribute('aria-busy', String(busy));
    this.form.setAttribute('aria-busy', String(busy));
    this.unsavedNote.hidden = !this.dirty;
    const needsGuard = !this.allowLeave && (this.dirty || this.saving);
    if (needsGuard === this.guardingLeave) return;
    window[needsGuard ? 'addEventListener' : 'removeEventListener']('beforeunload', this.beforeUnload);
    this.guardingLeave = needsGuard;
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

  renderTemplateSetup() {
    const version = this.selectedTemplate();
    document.querySelector('#company-signer-section').hidden = !version?.roles.includes('company');
    const preview = document.querySelector('#selected-template-preview');
    preview.hidden = !version;
    if (version) preview.href = version.previewUrl;
    const issues = [];
    if (!version) issues.push('Select a published template version.');
    else {
      if (!this.inputs.get('signer_name').value.trim()) issues.push("Add the client signer's name.");
      if (!this.inputs.get('signer_email').value.trim()) issues.push("Add the client signer's email.");
      if (version.roles.includes('company')) {
        if (!this.inputs.get('company_signer_name').value.trim()) issues.push("Add the company countersigner's name.");
        if (!this.inputs.get('company_signer_email').value.trim()) issues.push("Add the company countersigner's email.");
      }
    }
    const list = document.querySelector('#agreement-setup-issues');
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

  async save(event) {
    event.preventDefault();
    if (this.button.disabled) return;
    this.saveError.hidden = true;
    this.saveStatus.hidden = true;
    if (!this.validate()) return;
    const data = Object.fromEntries([...this.inputs].map(([name, input]) => [name, input.value.trim()]));
    data.workspace_id = this.companyId;
    if (this.isNew) data.creation_key = this.creationKey;
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
      this.saveStatus.dataset.state = 'saved';
      this.saveStatus.hidden = false;
    } catch (error) {
      if (error.status === 401 || error.status === 403 || error.code === 'workspace_changed') {
        const message = error.status === 403 ? 'Your sign-in session has changed.' : error.message;
        this.showSessionError(`${message} Your entered details are still here. Sign in to this file’s company in a new tab, then select Try again.`);
        return;
      }
      for (const [name, messages] of Object.entries(error.fieldErrors || {})) {
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
    if (this.sendButton.disabled || this.requestBusy) return;
    this.requestBusy = true;
    this.updateState();
    document.querySelector('#demo-error').hidden = true;
    document.querySelector('#demo-status').textContent = replace ? 'Generating a replacement code…' : 'Checking setup and issuing the request…';
    const payload = { workspace_id: this.companyId };
    if (replace) payload.replaceInvitationId = this.invitation.invitationId;
    try {
      const result = await this.client.request(document.body.dataset.requestUrl, payload);
      this.renderInvitation(result.invitation);
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
    document.querySelector('#demo-status').textContent = 'Request not confirmed. Retry or refresh status to recover the existing request.';
    if ([401, 403].includes(error.status) || error.code === 'workspace_changed') this.showSessionError(error.message);
  }

  renderInvitation(invitation) {
    this.invitation = invitation;
    document.querySelector('#demo-invitation').hidden = !invitation;
    const countersign = document.querySelector('#company-countersign');
    countersign.hidden = !invitation?.companySigningUrl;
    if (invitation?.companySigningUrl) {
      countersign.href = invitation.companySigningUrl;
      countersign.textContent = ['finalizing', 'completed'].includes(invitation.progress) ? 'Review recorded company submission' : 'Review & countersign';
    }
    if (!invitation) return;
    this.issued = true;
    document.querySelector('#file-status').textContent = ({ awaiting_company: 'Awaiting company', finalizing: 'Finalizing', completed: 'Ready to start' })[invitation.progress] || 'Awaiting client';
    this.renderCompletion(invitation.finalization);
    document.querySelector('#demo-send-hint').textContent = 'Request issued. The selected agreement and recipients are fixed. Repeating Send recovers this same request.';
    this.renderTemplateSetup();
    document.querySelector('#demo-link').value = new URL(invitation.link, location.origin).href;
    document.querySelector('#demo-open').href = invitation.link;
    document.querySelector('#demo-code').value = invitation.code || '';
    document.querySelector('#demo-copy-code').disabled = !invitation.code;
    document.querySelector('#demo-replace').hidden = !invitation.canReplace;
    this.remainingAtLoad = Date.parse(invitation.expiresAt) - Date.parse(invitation.serverNow);
    this.countdownStarted = performance.now();
    const messages = {
      pending: 'Request created · awaiting client. Opening the link still requires code verification.',
      expired: 'Code expired. Get a replacement code for this same request.',
      locked: 'Code locked after unsuccessful attempts. Get a replacement code for this same request.',
      verified: 'Access verified. No signature has been recorded. A replacement code lets you verify again if your session has ended.',
      replaced: 'This code was replaced. Refresh status to load the current invitation.',
      submitted: invitation.progress === 'completed' ? 'All required signatures, the completed PDF and signing history are saved. Ready to start.' : invitation.progress === 'awaiting_company' ? 'Client submission recorded. Awaiting company countersignature; this file is not complete.' : 'Client submission recorded. Final document generation is pending; this file is not complete.',
      request_expired: 'This signing request has expired. Contact the company; a replacement code cannot extend it.',
    };
    document.querySelector('#demo-status').textContent = messages[invitation.state];
    this.renderCountdown();
  }

  renderCompletion(state) {
    const visible = state && ['finalizing', 'completed'].includes(state.state);
    document.querySelector('#file-completion').hidden = !visible;
    if (!visible) return;
    const complete = state.state === 'completed';
    document.querySelector('#completion-status').textContent = complete
      ? `Completed PDF and signing history saved ${this.dateFormat.format(Date.parse(state.completedAt))}. Ready to start.`
      : 'All required signatures are recorded. Final artifacts are still pending; this file is not ready to start.';
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
    if (!this.invitation) return;
    const seconds = Math.max(0, Math.ceil((this.remainingAtLoad - (performance.now() - this.countdownStarted)) / 1000));
    const expiry = document.querySelector('#demo-expiry');
    expiry.textContent = this.invitation.state === 'submitted' ? 'Client submission recorded. Invitation verification is no longer a signing action.'
      : this.invitation.state === 'request_expired' ? 'Request expired. Replacement codes cannot extend its deadline.'
      : this.invitation.state === 'verified' ? 'Code already used. Your verified session has its own expiry.'
      : this.invitation.state === 'locked' ? 'Code locked. Verification is unavailable with this code.'
      : seconds ? `Code expires in ${Math.floor(seconds / 3600)}h ${Math.floor(seconds / 60) % 60}m ${seconds % 60}s · ${this.dateFormat.format(Date.parse(this.invitation.expiresAt))}`
      : 'Code expired. The server requires a replacement code.';
    if (!seconds && this.invitation.state === 'pending') {
      this.invitation.state = 'expired';
      document.querySelector('#demo-code').value = '';
      document.querySelector('#demo-copy-code').disabled = true;
      document.querySelector('#demo-replace').hidden = false;
      document.querySelector('#demo-status').textContent = 'Code expired. Get a replacement code for this same request.';
    }
  }

  async copyInvitation(kind) {
    const input = document.querySelector(`#demo-${kind}`);
    try {
      await navigator.clipboard.writeText(input.value);
      document.querySelector('#demo-status').textContent = `${kind === 'link' ? 'Link' : 'Code'} copied.`;
    } catch {
      input.focus();
      input.select();
      document.querySelector('#demo-status').textContent = 'Copy is unavailable. The value is selected; use your device’s copy command.';
    }
  }
}

new ClientFileEditor(document.querySelector('#draft-form'), JSON.parse(document.querySelector('#draft-data').textContent));
