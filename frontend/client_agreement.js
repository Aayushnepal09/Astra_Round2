import { SessionClient } from './shared/ui.js';

class AgreementReview {
  constructor() {
    this.data = JSON.parse(document.querySelector('#review-data').textContent);
    this.client = new SessionClient();
    this.page = 1;
    this.image = document.querySelector('#review-image');
    this.status = document.querySelector('#review-status');
    this.error = document.querySelector('#review-error');
    this.previous = document.querySelector('#review-previous');
    this.next = document.querySelector('#review-next');
    this.previous.addEventListener('click', () => this.load(this.page - 1));
    this.next.addEventListener('click', () => this.load(this.page + 1));
    document.querySelector('#review-retry').addEventListener('click', () => this.load(this.page));
    document.querySelector('#review-zoom').addEventListener('change', event => { document.querySelector('#review-sheet').style.width = `${event.target.value}%`; });
    document.querySelector('#review-logout').addEventListener('click', () => this.logout());
    window.addEventListener('pageshow', event => { if (event.persisted) this.load(this.page); });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.clearImage();
      else this.load(this.page);
    });
    this.signing = new SigningController(this, this.data.signing);
    this.load(this.page);
  }

  clearImage() {
    this.cancelImageLoad?.();
    this.cancelImageLoad = null;
    this.image.hidden = true;
    document.querySelector('#review-field-markers').replaceChildren();
    this.image.removeAttribute('src');
    if (this.imageUrl) URL.revokeObjectURL(this.imageUrl);
    this.imageUrl = null;
  }

  async load(page) {
    if (page < 1 || page > this.data.pageCount) return;
    if (this.loading) { this.queuedPage = page; return; }
    this.loading = true;
    this.page = page;
    this.clearImage();
    this.error.hidden = true;
    this.previous.disabled = this.next.disabled = true;
    document.querySelector('#review-page-label').textContent = `Page ${page} of ${this.data.pageCount}`;
    this.status.textContent = 'Loading agreement page…';
    document.querySelector('#review-viewport').setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(this.data.pages[page - 1], { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) {
        const result = await response.json();
        if ([401, 410].includes(response.status)) this.signing.accessExpired();
        throw new Error(result.error || 'Couldn’t load this page. Retry.');
      }
      const blob = await response.blob();
      if (blob.type !== 'image/png') throw new Error('Couldn’t load this page. Verify access again.');
      if (document.hidden) return;
      this.imageUrl = URL.createObjectURL(blob);
      await new Promise((resolve, reject) => {
        this.cancelImageLoad = resolve;
        this.image.onload = () => { this.cancelImageLoad = null; resolve(); };
        this.image.onerror = () => reject(new Error('Couldn’t display this page. Retry.'));
        this.image.src = this.imageUrl;
      });
      if (document.hidden || !this.imageUrl) { this.clearImage(); return; }
      this.image.alt = `Agreement page ${page} of ${this.data.pageCount}`;
      this.image.hidden = false;
      this.status.textContent = 'Complete your fields, then use Sign & submit when ready.';
      this.signing.renderMarkers();
    } catch (error) {
      this.clearImage();
      this.status.textContent = 'Agreement page unavailable.';
      document.querySelector('#review-error-copy').textContent = error.message;
      this.error.hidden = false;
    } finally {
      this.loading = false;
      this.previous.disabled = page === 1;
      this.next.disabled = page === this.data.pageCount;
      document.querySelector('#review-viewport').setAttribute('aria-busy', 'false');
      const queuedPage = this.queuedPage;
      this.queuedPage = null;
      if (queuedPage && !document.hidden) this.load(queuedPage);
    }
  }

  async logout() {
    try {
      await this.client.request(document.body.dataset.logoutUrl, {});
      this.clearImage();
      location.replace(document.body.dataset.invitationUrl);
    } catch (error) {
      this.clearImage();
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
    this.savedValues = JSON.stringify(this.values());
    this.form.addEventListener('input', event => {
      this.clearErrors();
      this.consent.removeAttribute('aria-invalid');
      if (event.target !== this.consent) this.consent.checked = false;
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
    if (state.receipt) this.confirm(state);
    this.update();
  }

  renderFields() {
    const labels = { text: 'Text', name: 'Name', date: 'Date', acknowledgement: 'Acknowledgement', initials: 'Initials', signature: 'Typed signature' };
    const container = document.querySelector('#signing-inputs');
    this.state.fields.forEach((field, index) => {
      const row = document.createElement('div');
      row.className = 'field';
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
        document.querySelector('#review-viewport').scrollIntoView({ block: 'nearest' });
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

  renderMarkers() {
    const container = document.querySelector('#review-field-markers');
    container.replaceChildren();
    this.state.fields.forEach((field, index) => {
      if (field.page !== this.review.page) return;
      const marker = document.createElement('span');
      marker.className = 'review-field-marker';
      Object.assign(marker.style, { left: `${field.x * 100}%`, top: `${field.y * 100}%`, width: `${field.width * 100}%`, height: `${field.height * 100}%` });
      marker.textContent = String(index + 1);
      container.append(marker);
    });
  }

  values() {
    return Object.fromEntries([...this.inputs].map(([id, input]) => [id, input.type === 'checkbox' ? input.checked : input.value]));
  }

  setValues(values) {
    for (const [id, input] of this.inputs) {
      if (input.type === 'checkbox') input.checked = values[id] === true;
      else input.value = values[id] || '';
    }
  }

  update() {
    const locked = this.busy || this.expired || this.uncertain || !!this.state.receipt;
    this.fields.disabled = !!locked;
    this.consent.disabled = !!locked;
    this.save.disabled = this.submit.disabled = !!locked;
    document.querySelector('#signing-check').disabled = !!this.busy;
    document.querySelector('#signing-restore').disabled = !!this.busy || !!this.uncertain || !!this.state.receipt;
    this.form.setAttribute('aria-busy', String(!!this.busy));
    const dirty = !this.state.receipt && (JSON.stringify(this.values()) !== this.savedValues || this.uncertain);
    window[dirty ? 'addEventListener' : 'removeEventListener']('beforeunload', this.beforeUnload);
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
    document.querySelector('#review-field-markers').replaceChildren();
    document.querySelector('#signing-reverify').hidden = false;
    this.status.textContent = this.state.role === 'company'
      ? 'Company access is unavailable. Your entries remain in this tab. Sign in to this workspace in a new tab, then check submission status here.'
      : 'Access expired. Your entries remain in this tab. Verify access in a new tab, then check submission status here.';
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
      if (this.state.receipt) this.confirm(this.state);
      else this.status.textContent = 'Progress saved. No signature has been recorded.';
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
    } catch (error) { this.showError(error); }
    finally { this.busy = false; this.update(); }
  }

  confirm(state) {
    this.state = state;
    this.uncertain = false;
    this.setValues(state.values);
    this.consent.checked = true;
    document.querySelector('#signing-confirmation').hidden = false;
    document.querySelector('#confirmation-copy').textContent = state.progress === 'completed'
      ? 'All required signatures, the completed PDF and signing history are saved. The company can access the final artifacts from the client file.'
      : state.role === 'company'
      ? 'Your company countersignature is recorded. All required signatures are recorded; final-document generation is pending. This file is not complete.'
      : state.progress === 'awaiting_company'
      ? 'Your client signature is recorded. A company countersignature is still required. This file is not complete.'
      : 'Your client signature is recorded. Final-document generation is pending. This file is not complete.';
    document.querySelector('#confirmation-time').textContent = `Recorded for ${state.receipt.signerName} at ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'long' }).format(Date.parse(state.receipt.submittedAt))}.`;
    document.querySelector('#confirmation-reference').textContent = `Submission reference: ${state.receipt.id}`;
    document.querySelector('#signing-progress').textContent = state.progress === 'completed' ? 'Completed' : state.progress === 'awaiting_company' ? 'Awaiting company' : 'Finalizing';
    document.querySelector('#signing-notice').textContent = 'Your accepted values are now read-only. You can continue reviewing the agreement while your access remains authorized.';
    this.status.textContent = state.progress === 'completed' ? 'Submission accepted. Final artifacts saved.' : 'Submission accepted by the server. Final artifacts are pending; the company can retry finalization from the client file.';
    this.update();
    document.querySelector('#signing-confirmation').focus();
  }
}

new AgreementReview();
