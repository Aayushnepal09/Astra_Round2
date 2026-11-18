import { SessionClient } from './shared/ui.js';
import { DocumentAssistant } from './agreement_assistant.js';
import { ScrollDocument } from './shared/scroll_document.js';

class AgreementReview {
  constructor() {
    this.data = JSON.parse(document.querySelector('#review-data').textContent);
    this.client = new SessionClient();
    this.page = 1;
    this.status = document.querySelector('#review-status');
    this.error = document.querySelector('#review-error');
    document.querySelector('#review-page-number').addEventListener('change', event => this.load(Number(event.target.value)));
    document.querySelector('#review-retry').addEventListener('click', () => this.refresh());
    document.querySelector('#review-zoom').addEventListener('change', event => {
      document.querySelector('#review-viewport').style.setProperty('--document-zoom', `${event.target.value}%`);
    });
    document.querySelector('#review-logout').addEventListener('click', () => this.logout());
    this.signing = new SigningController(this, this.data.signing);
    this.assistant = new DocumentAssistant(this);
    window.addEventListener('pageshow', event => { if (event.persisted) this.signing.check(); });
    window.addEventListener('pagehide', () => this.clearImage());
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.clearImage();
      else this.signing.check();
    });
    this.startViewer();
  }

  startViewer() {
    if (this.viewer || document.hidden || this.signing.expired) return;
    this.viewer = new ScrollDocument({ root: document.querySelector('#review-viewport'),
      count: this.data.pageCount, geometry: this.data.pageGeometry, url: number => this.data.pages[number - 1],
      onPage: number => this.setPage(number),
      onLoad: () => { this.signing.renderMarkers(); this.status.textContent = this.signing.state.receipt
        ? 'Recorded agreement. Your accepted fields are read-only.' : 'Scroll to review every page. Tap a numbered field to complete it.'; },
      onError: (error, number) => {
        if ([401, 403, 410].includes(error.status)) this.signing.accessExpired();
        this.error.hidden = false;
        document.querySelector('#review-error-copy').textContent = `Page ${number}: ${error.message}`;
      },
    });
    this.setPage(this.page);
    this.signing.renderMarkers();
  }

  setPage(page) {
    this.page = page;
    document.querySelector('#review-page-number').value = page;
    document.querySelector('#review-page-label').textContent = `of ${this.data.pageCount}`;
  }

  clearImage() {
    this.viewer?.destroy(); this.viewer = null;
  }

  load(page) {
    if (!Number.isInteger(page) || page < 1 || page > this.data.pageCount || this.signing.expired) return;
    this.startViewer();
    this.setPage(page);
    this.viewer?.goTo(page);
  }

  reveal(field) {
    if (this.signing.expired) return;
    this.startViewer();
    this.setPage(field.page);
    this.viewer?.revealField(field);
  }

  refresh() {
    this.startViewer();
    this.error.hidden = true;
    this.viewer?.reload();
    this.signing.renderMarkers();
  }

  async logout() {
    try {
      await this.client.request(document.body.dataset.logoutUrl, {});
      this.clearImage();
      location.replace(document.body.dataset.invitationUrl);
    } catch (error) {
      this.clearImage(); this.signing.accessExpired();
      this.error.hidden = false;
      document.querySelector('#review-error-copy').textContent = error.message;
    }
  }
}

class SigningController {
  constructor(review, state) {
    this.review = review;
    this.state = state;
    this.client = review.client;
    this.inputs = new Map();
    this.markers = new Map();
    this.form = document.querySelector('#signing-form');
    this.fields = document.querySelector('#signing-fields');
    this.consent = document.querySelector('#signing-consent');
    this.submit = document.querySelector('#signing-submit');
    this.save = document.querySelector('#signing-save');
    this.status = document.querySelector('#signing-status');
    this.error = document.querySelector('#signing-error');
    this.beforeUnload = event => { event.preventDefault(); event.returnValue = ''; };
    document.querySelector('#signing-consent-copy').textContent = state.consentText;
    this.renderFields();
    this.setValues(state.values);
    this.activeFieldId = state.fields[0]?.id || null;
    this.savedValues = JSON.stringify(this.values());
    this.form.addEventListener('input', event => {
      this.clearErrors();
      this.consent.removeAttribute('aria-invalid');
      if (event.target !== this.consent) this.consent.checked = false;
      if (this.state.fields.some(field => field.type === 'signature' && this.inputs.get(field.id) === event.target)) this.adoptedSignature = null;
      this.update();
    });
    this.form.addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT') event.preventDefault();
    });
    this.form.addEventListener('submit', event => {
      event.preventDefault();
      if (event.submitter === this.submit) this.send('submit');
    });
    this.save.addEventListener('click', () => this.send('save'));
    document.querySelector('#signing-check').addEventListener('click', () => this.check());
    document.querySelector('#signing-restore').addEventListener('click', () => this.check(true));
    document.querySelector('#signing-field-select').addEventListener('change', event => this.activateField(event.target.value, { navigate: true, edit: true }));
    document.querySelector('#signing-next-field').addEventListener('click', () => this.nextIncomplete());
    document.querySelector('#review-next-required').addEventListener('click', () => this.nextIncomplete());
    document.querySelector('#signing-autofill').addEventListener('click', () => this.autofill());
    document.querySelector('#signature-adoption').addEventListener('submit', event => this.adoptSignature(event));
    document.querySelector('#signature-cancel').addEventListener('click', () => document.querySelector('#signature-dialog').close());
    document.querySelector('#signing-editor-close').addEventListener('click', () => this.closeFieldEditor());
    window.matchMedia?.('(max-width: 850px)').addEventListener?.('change', event => { if (!event.matches) this.closeFieldEditor(); });
    if (state.receipt) this.confirm(state);
    this.update();
  }

  renderFields() {
    const labels = { text: 'Text', name: 'Name', date: 'Date', acknowledgement: 'Acknowledgement', initials: 'Initials', signature: 'Typed signature' };
    const container = document.querySelector('#signing-inputs');
    this.state.fields.forEach((field, index) => {
      const row = document.createElement('div');
      row.className = 'field';
      row.hidden = index !== 0;
      document.querySelector('#signing-field-select').add(new Option(`${index + 1}. ${field.label || labels[field.type]} · Page ${field.page}`, field.id));
      const label = document.createElement('label');
      label.htmlFor = `signer-${field.id}`;
      label.textContent = `${index + 1}. ${field.label || labels[field.type]}${field.required ? ' · Required' : ' · Optional'}`;
      const input = document.createElement(field.type === 'text' ? 'textarea' : 'input');
      input.id = label.htmlFor;
      input.name = field.id;
      input.required = field.required;
      if (field.type !== 'text') input.type = field.type === 'acknowledgement' ? 'checkbox' : field.type === 'date' ? 'date' : 'text';
      input.autocomplete = 'off';
      if (field.type === 'signature') input.placeholder = 'Type your assigned signer name';
      const wrap = document.createElement('div');
      if (field.type === 'acknowledgement') {
        label.className = 'signing-acknowledgement';
        label.prepend(input);
        row.append(label);
      } else {
        wrap.className = field.type === 'text' ? '' : 'input-wrap';
        wrap.append(input);
        row.append(label, wrap);
      }
      const reference = document.createElement('div');
      reference.className = 'signing-field-reference';
      const type = document.createElement('span');
      type.textContent = labels[field.type];
      const page = document.createElement('button');
      page.type = 'button';
      page.textContent = `View page ${field.page}`;
      page.addEventListener('click', () => {
        this.review.load(field.page);
      });
      reference.append(type, page);
      const error = document.createElement('p');
      error.id = `${input.id}-error`;
      error.className = 'field-error';
      error.hidden = true;
      input.setAttribute('aria-describedby', error.id);
      row.append(reference, error);
      container.append(row);
      this.inputs.set(field.id, input);
    });
  }

  editable() {
    return !this.busy && !this.expired && !this.uncertain && !this.state.receipt;
  }

  renderMarkers() {
    if (!this.review.viewer) return;
    this.review.viewer.pages.forEach(page => { page.fields.hidden = page.image.hidden; });
    if (this.state.completion?.pdfUrl) {
      this.review.viewer.pages.forEach(page => page.fields.replaceChildren()); this.markers.clear(); return;
    }
    const values = this.values();
    this.state.fields.forEach((field, index) => {
      const container = this.review.viewer.pages.get(field.page)?.fields;
      if (!container) return;
      let marker = this.markers.get(field.id);
      if (!marker) {
        marker = document.createElement('button');
        marker.type = 'button'; marker.className = 'review-field-marker';
        marker.dataset.fieldId = field.id;
        marker.addEventListener('click', () => this.activateField(field.id, { place: true }));
        this.markers.set(field.id, marker);
      }
      const value = values[field.id];
      const completed = field.type === 'acknowledgement' ? value === true : !!value?.trim();
      marker.dataset.complete = String(completed);
      marker.dataset.highlight = String(field.id === this.activeFieldId);
      Object.assign(marker.style, { left: `${field.x * 100}%`, top: `${field.y * 100}%`, width: `${field.width * 100}%`, height: `${field.height * 100}%` });
      marker.textContent = `${index + 1}. ${completed ? field.type === 'acknowledgement' ? 'Acknowledged' : value : field.type === 'signature' ? 'Tap to sign' : field.label || field.type}`;
      marker.setAttribute('aria-label', `${index + 1}. ${field.label || field.type}, ${completed ? 'completed' : field.required ? 'required' : 'optional'}`);
      marker.disabled = !this.editable();
      if (marker.parentElement !== container) container.append(marker);
    });
  }

  activateField(id, { place = false, navigate = false, edit = false } = {}) {
    const field = this.state.fields.find(item => item.id === id);
    if (!field) return;
    this.activeFieldId = id;
    this.navigationStarted = true;
    for (const [key, input] of this.inputs) input.closest('.field').hidden = key !== id;
    document.querySelector('#signing-field-select').value = id;
    this.renderMarkers();
    if (!this.editable()) { if (navigate) this.revealField(field); return; }
    const input = this.inputs.get(id);
    const mobile = window.matchMedia?.('(max-width: 850px)').matches;
    if (mobile && navigate && !place && !edit) this.closeFieldEditor();
    if (place && field.type === 'signature') {
      if (this.adoptedSignature) {
        input.value = this.adoptedSignature;
        this.changed();
      } else {
        this.signatureFieldId = id;
        document.querySelector('#signature-name').value = input.value || this.state.signer?.name || '';
        document.querySelector('#signature-error').hidden = true;
        document.querySelector('#signature-dialog').showModal();
      }
    } else if (place && field.type === 'acknowledgement') {
      input.checked = !input.checked;
      this.changed();
    } else if (place || edit || (navigate && !mobile)) {
      if (mobile) {
        const slot = document.querySelector('#signing-form-slot');
        if (!this.form.classList.contains('is-editing')) slot.style.minHeight = `${this.form.getBoundingClientRect().height}px`;
        this.form.classList.add('is-editing');
        document.querySelector('#signing-editor-close').hidden = false;
      }
      input.focus({ preventScroll: true });
      if (!mobile) input.scrollIntoView({ block: 'nearest' });
    }
    if (navigate || place) this.revealField(field);
  }

  revealField(field) {
    if (this.review.reveal) this.review.reveal(field);
    else this.review.load(field.page);
  }

  closeFieldEditor() {
    this.form.classList.remove('is-editing');
    document.querySelector('#signing-form-slot').style.minHeight = '';
    document.querySelector('#signing-editor-close').hidden = true;
  }

  changed() {
    this.clearErrors(); this.consent.checked = false;
    this.status.textContent = '';
    this.update();
  }

  adoptSignature(event) {
    event.preventDefault();
    if (!this.editable()) { document.querySelector('#signature-dialog').close(); return; }
    const name = document.querySelector('#signature-name').value.trim();
    const normalize = value => value.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
    if (!name || normalize(name) !== normalize(this.state.signer?.name || '')) {
      const error = document.querySelector('#signature-error');
      error.textContent = `Enter your assigned signer name: ${this.state.signer?.name || ''}.`;
      error.hidden = false; return;
    }
    this.adoptedSignature = name;
    this.inputs.get(this.signatureFieldId).value = name;
    document.querySelector('#signature-dialog').close();
    this.changed();
  }

  autofill() {
    if (!this.editable()) return;
    let count = 0;
    for (const field of this.state.fields) {
      const input = this.inputs.get(field.id);
      if (input.value.trim() || field.type === 'acknowledgement') continue;
      const label = (field.label || '').trim().replace(/[:*]+$/, '').trim();
      let value = '';
      if (field.type === 'name' && /^(?:(?:your|client|signer|full|printed|company signer|company countersigner)\s+)?name$/i.test(label)) value = this.state.signer?.name || '';
      if (field.type === 'text' && /^(?:(?:your|client|signer|company signer)\s+)?e-?mail(?: address)?$/i.test(label)) value = this.state.signer?.email || '';
      if (value) { input.value = value; count++; }
    }
    this.changed();
    this.status.textContent = count ? `Filled ${count} empty name or email fields. Review the values before saving. Signatures and consent still need your action.`
      : 'No empty name or email fields could be matched. Existing entries are unchanged.';
  }

  nextIncomplete() {
    if (!this.editable()) return;
    const values = this.values();
    const fields = this.state.fields;
    const start = this.navigationStarted ? fields.findIndex(field => field.id === this.activeFieldId) + 1 : 0;
    const ordered = [...fields.slice(start), ...fields.slice(0, start)];
    const field = ordered.find(field => field.required && (field.type === 'acknowledgement' ? values[field.id] !== true : !values[field.id]?.trim()));
    if (field) this.activateField(field.id, { navigate: true });
    else { this.status.textContent = 'Required fields are filled. Review your entries and confirm your intent before Sign & submit.'; this.consent.focus(); }
  }

  values() {
    return Object.fromEntries([...this.inputs].map(([id, input]) => [id, input.type === 'checkbox' ? input.checked : input.value]));
  }

  setValues(values) {
    this.adoptedSignature = null;
    for (const [id, input] of this.inputs) {
      if (input.type === 'checkbox') input.checked = values[id] === true;
      else input.value = values[id] || '';
    }
  }

  update() {
    const instructions = document.querySelector('#signing-field-instructions');
    if (instructions) instructions.hidden = !!this.state.receipt;
    const fieldsLink = document.querySelector('#signing-fields-link');
    if (fieldsLink) fieldsLink.textContent = this.state.receipt ? 'View recorded fields' : 'Complete required fields';
    const locked = this.busy || this.expired || this.uncertain || !!this.state.receipt;
    this.fields.disabled = !!locked;
    this.consent.disabled = !!locked;
    this.save.disabled = this.submit.disabled = !!locked;
    this.save.parentElement.hidden = !!this.state.receipt;
    this.consent.closest('.signing-consent').hidden = !!this.state.receipt;
    document.querySelector('#signing-restore').hidden = !!this.state.receipt;
    document.querySelector('#signing-help').hidden = !!this.state.receipt;
    document.querySelector('#signing-check').disabled = !!this.busy;
    document.querySelector('#signing-restore').disabled = !!this.busy || !!this.uncertain || !!this.state.receipt;
    if (this.expired || this.uncertain) document.querySelector('#signing-recovery').open = true;
    this.form.setAttribute('aria-busy', String(!!this.busy));
    document.querySelector('#signing-autofill').disabled = !!locked;
    document.querySelector('#signing-autofill').hidden = !!this.state.receipt;
    document.querySelector('#signing-next-field').disabled = !!locked;
    document.querySelector('#review-next-required').disabled = !!locked;
    document.querySelector('#review-next-required').hidden = !!this.state.receipt;
    document.querySelector('#signing-field-select').disabled = !!this.busy || !!this.expired || !!this.uncertain;
    const values = this.values();
    const required = this.state.fields.filter(field => field.required);
    const complete = required.filter(field => field.type === 'acknowledgement' ? values[field.id] === true : !!values[field.id]?.trim()).length;
    const ready = complete === required.length;
    const recorded = !!this.state.receipt;
    this.submit.classList.toggle('secondary', !ready);
    for (const id of ['signing-next-field', 'review-next-required']) {
      const next = document.querySelector(`#${id}`);
      next.textContent = ready ? 'Review & sign' : 'Next required field';
      next.classList.toggle('secondary', ready);
      next.hidden = recorded;
    }
    document.querySelector('#signing-next-step').textContent = recorded ? 'Your recorded fields are read-only.'
      : this.expired || this.uncertain ? 'Check your access or submission status below before continuing.'
      : ready ? 'Review your entries, confirm your consent, then select Sign & submit.'
      : `${required.length - complete} required ${required.length - complete === 1 ? 'field remains' : 'fields remain'}. Next takes you to an unfinished field.`;
    document.querySelector('#signing-required-progress').textContent = `${complete} of ${required.length} required fields filled`;
    document.querySelector('#review-required-progress').textContent = `${complete} of ${required.length} required fields filled`;
    // Friendly meter above the document: fills with required fields and turns green when ready to sign.
    const meter = document.querySelector('#signing-meter');
    const percent = required.length ? Math.round(complete / required.length * 100) : 100;
    meter.hidden = recorded;
    meter.dataset.ready = String(ready);
    meter.setAttribute('aria-valuenow', String(percent));
    document.querySelector('#signing-meter-fill').style.width = `${percent}%`;
    document.querySelector('#signing-meter-label').textContent = ready
      ? `All ${required.length} required fields done. Confirm your consent, then Sign & submit.`
      : `Hi ${meter.dataset.name} · ${complete} of ${required.length} required fields done`;
    this.renderMarkers();
    const dirty = !this.state.receipt && (JSON.stringify(this.values()) !== this.savedValues || this.uncertain);
    const saveState = document.querySelector('#signing-save-state');
    saveState.textContent = recorded ? 'Submission recorded.' : this.busy ? 'Checking or saving…'
      : this.uncertain ? 'Submission not confirmed. Check status before retrying.'
      : dirty ? 'Unsaved changes. Save progress before leaving.' : 'No unsaved changes.';
    saveState.dataset.dirty = String(!!dirty);
    window[dirty ? 'addEventListener' : 'removeEventListener']('beforeunload', this.beforeUnload);
    this.review.assistant?.update();
  }

  clearErrors() {
    this.error.hidden = true;
    document.querySelector('#consent-error').hidden = true;
    for (const input of this.inputs.values()) {
      input.removeAttribute('aria-invalid');
      document.getElementById(`${input.id}-error`).hidden = true;
    }
  }

  accessExpired() {
    this.expired = true;
    this.consent.checked = false;
    this.review.clearImage();
    this.closeFieldEditor();
    this.clearCompletedAccess();
    document.querySelector('#signing-reverify').hidden = false;
    this.status.textContent = this.state.role === 'company'
      ? 'Company access is unavailable. Your entries remain in this tab. Sign in to this workspace in a new tab, then check submission status here.'
      : this.state.receipt
      ? 'Access expired. Ask the company for a replacement link and code from its demo panel. Verify the replacement link in a new tab, then check status here. Your recorded signature is preserved.'
      : 'Access expired. Your entries remain in this tab. Ask the company for a replacement link and code, verify in a new tab, then check status here.';
    this.update();
  }

  showError(error) {
    this.error.textContent = error.message;
    this.error.hidden = false;
    let first;
    for (const [id, messages] of Object.entries(error.fieldErrors || {})) {
      const input = id === 'consent' ? this.consent : this.inputs.get(id);
      if (!input) continue;
      const message = id === 'consent' ? document.querySelector('#consent-error') : document.getElementById(`${input.id}-error`);
      message.textContent = messages.join(' ');
      message.hidden = false;
      input.setAttribute('aria-invalid', 'true');
      first ??= input;
    }
    if ([401, 403, 410].includes(error.status)) this.accessExpired();
    if (first && first !== this.consent) this.activateField(first.name);
    first?.focus();
  }

  async send(action) {
    if (this.busy || this.expired || this.uncertain || this.state.receipt) return;
    this.clearErrors();
    if (action === 'submit' && !this.consent.checked) {
      this.showError({ message: 'Confirm your intent to sign first.', fieldErrors: { consent: ['Consent is required to sign.'] } });
      return;
    }
    const payload = Object.fromEntries(['requestId', 'templateVersionId', 'recipientId', 'pdfSha256'].map(key => [key, this.state[key]]));
    Object.assign(payload, { action, values: this.values(), revision: this.state.revision });
    if (action === 'submit') Object.assign(payload, { consent: true, submissionKey: crypto.randomUUID() });
    this.busy = true;
    this.status.textContent = action === 'submit' ? 'Recording your submission…' : 'Saving your progress…';
    this.update();
    try {
      const result = await this.client.request(document.body.dataset.signingUrl, payload);
      this.state = result.signing;
      this.savedValues = JSON.stringify(this.values());
      if (this.state.receipt) {
        this.confirm(this.state);
        globalThis.showToast?.('Signature recorded');
        this.review.refresh();
      }
      else {
        this.status.textContent = 'Progress saved. No signature has been recorded.';
        globalThis.showToast?.('Progress saved');
      }
    } catch (error) {
      if (action === 'submit' && (!error.status || error.status >= 500 || error.code === 'already_submitted')) {
        this.uncertain = true;
        this.status.textContent = 'The submission result is not confirmed. Check submission status before editing or retrying; your entries remain here.';
      } else this.status.textContent = 'Your entries remain here. Correct the highlighted fields or retry.';
      this.showError(error);
    } finally {
      this.busy = false;
      this.update();
    }
  }

  async check(restore = false) {
    if (this.busy) return;
    if (restore && JSON.stringify(this.values()) !== this.savedValues && !window.confirm('Replace your current entries with the last saved progress?')) return;
    this.busy = true;
    this.update();
    this.clearErrors();
    this.status.textContent = 'Checking server records…';
    try {
      const result = await this.client.request(document.body.dataset.signingUrl);
      this.expired = false;
      document.querySelector('#signing-reverify').hidden = true;
      if (result.signing.receipt) this.confirm(result.signing);
      else {
        if (restore || JSON.stringify(this.values()) === this.savedValues) {
          this.state = result.signing;
          this.setValues(this.state.values);
          this.savedValues = JSON.stringify(this.values());
          this.consent.checked = false;
        }
        this.uncertain = false;
        this.status.textContent = restore ? 'Saved progress loaded. Confirm consent before signing.' : 'No submission recorded. Your entries remain here; you can continue or retry Sign & submit.';
      }
      this.review.refresh();
      this.review.assistant?.check();
    } catch (error) { this.showError(error); }
    finally { this.busy = false; this.update(); }
  }

  confirm(state) {
    this.closeFieldEditor();
    this.state = state;
    this.uncertain = false;
    this.setValues(state.values);
    this.consent.checked = true;
    document.querySelector('#signing-confirmation').hidden = false;
    document.querySelector('#confirmation-copy').textContent = state.progress === 'completed'
      ? state.role === 'company'
        ? 'All required signatures, the completed PDF and signing history are saved. The company can access the final artifacts from the client file.'
        : state.completion?.pdfUrl
        ? 'All required signatures and final processing are complete. You can view or download your completed agreement below.'
        : 'Completion is recorded, but the completed PDF is unavailable. Contact the company; your signature is preserved.'
      : state.role === 'company'
      ? 'Your company countersignature is recorded. All required signatures are recorded; final-document generation is pending. This file is not complete.'
      : state.progress === 'awaiting_company'
      ? 'Your client signature is recorded. A company countersignature is still required. This file is not complete.'
      : 'Your client signature is recorded. Final-document generation is pending. This file is not complete.';
    document.querySelector('#confirmation-time').textContent = `Recorded for ${state.receipt.signerName} at ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'long' }).format(Date.parse(state.receipt.submittedAt))}.`;
    document.querySelector('#confirmation-reference').textContent = `Submission reference: ${state.receipt.id}`;
    document.querySelector('#signing-progress').textContent = state.progress === 'completed' ? 'Completed' : state.progress === 'awaiting_company' ? 'Awaiting company' : 'Finalizing';
    document.querySelector('#signing-notice').textContent = 'Your accepted values are now read-only. You can continue reviewing the agreement while your access remains authorized.';
    if (state.role === 'company') {
      const notice = document.querySelector('.client-signed-preview');
      if (notice) notice.dataset.complete = String(state.progress === 'completed');
      const title = document.querySelector('#client-signature-status-title');
      const explanation = document.querySelector('#company-preview-status');
      if (title) title.textContent = 'Client signature recorded';
      if (explanation) explanation.textContent = state.progress === 'completed'
        ? 'All required signatures are received. The completed document is available from the client file.'
        : 'All required signatures are recorded. Final document processing is pending.';
    }
    this.status.textContent = state.progress === 'completed' ? 'Submission accepted. Final processing complete.'
      : state.progress === 'awaiting_company' ? 'Awaiting company countersignature. Check submission status for updates.'
      : 'All required signatures are recorded. Final processing is pending. Check submission status for updates; the company can retry processing.';
    this.clearCompletedAccess();
    const completionError = document.querySelector('#client-completion-error');
    completionError.textContent = state.completion?.error || '';
    completionError.hidden = !completionError.textContent;
    if (!this.expired && state.completion?.pdfUrl) {
      document.querySelector('#client-completed-view').href = state.completion.pdfUrl;
      document.querySelector('#client-completed-download').href = `${state.completion.pdfUrl}?download=1`;
      document.querySelector('#client-completed-downloads').hidden = false;
    }
    this.update();
    document.querySelector('#signing-confirmation').focus();
  }

  clearCompletedAccess() {
    document.querySelector('#client-completed-downloads').hidden = true;
    for (const id of ['client-completed-view', 'client-completed-download']) document.getElementById(id).removeAttribute('href');
  }
}

new AgreementReview();
