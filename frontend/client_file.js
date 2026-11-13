import { SessionClient } from './shared/ui.js';

class ClientFileEditor {
  static fieldNames = new Map([
    ['reference', 'reference'],
    ['title', 'title'],
    ['client_name', 'clientName'],
    ['client_email', 'clientEmail'],
    ['signer_name', 'signerName'],
    ['signer_email', 'signerEmail'],
  ]);

  constructor(form, data) {
    this.form = form;
    this.client = new SessionClient();
    this.inputs = new Map([...form.querySelectorAll('input[name]')].map(input => [input.name, input]));
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
    this.checkingSession = false;
    this.sessionReady = false;
    this.saving = false;
    this.dirty = false;
    this.guardingLeave = false;
    this.allowLeave = false;
    this.beforeUnload = event => {
      event.preventDefault();
      event.returnValue = '';
    };
    this.applyFile(data);
    this.savedValues = JSON.stringify(this.readValues());
    this.form.addEventListener('input', event => this.handleInput(event));
    this.form.addEventListener('submit', event => this.save(event));
    this.sessionRetry.addEventListener('click', () => this.refreshSession());
    window.addEventListener('pageshow', event => { if (event.persisted) this.refreshSession(); });
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
    for (const [name, value] of Object.entries(this.fileValues(file))) this.inputs.get(name).value = value;
    this.updateFileMetadata(file);
  }

  updateFileMetadata(file) {
    if (file.id) {
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
    this.dirty = JSON.stringify(this.readValues()) !== this.savedValues;
    this.updateState();
  }

  updateState() {
    const busy = this.checkingSession || this.saving;
    this.fields.disabled = busy || !this.sessionReady;
    this.button.disabled = busy || !this.sessionReady || (!this.isNew && !this.dirty);
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
      else if (value.length > input.maxLength) message = `Use ${input.maxLength} characters or fewer.`;
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
    document.querySelector('#draft-session-error-message').textContent = message;
    this.sessionError.hidden = false;
    this.sessionError.scrollIntoView({ block: 'nearest' });
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
}

new ClientFileEditor(document.querySelector('#draft-form'), JSON.parse(document.querySelector('#draft-data').textContent));
