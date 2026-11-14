import { SessionClient, SessionError } from './shared/ui.js';

export class AgreementWorkspace {
  constructor(navigate) {
    this.navigateWorkspace = navigate;
    this.client = new SessionClient();
    this.companyId = document.body.dataset.companyId;
    this.baseUrl = new URL(document.body.dataset.agreementsUrl, window.location.origin);
    this.listPage = 1;
    this.pageNumber = 1;
    this.active = false;
    this.route = null;
    this.file = null;
    this.creationKey = null;
    this.limits = null;
    this.agreement = null;
    this.fields = [];
    this.selectedFieldId = null;
    this.fieldsDirty = false;
    this.fieldsSaving = false;
    this.fieldEditVersion = 0;
    this.fieldSession = 0;
    this.dragState = null;
    this.objectUrl = null;
    this.previewAttempt = 0;
    this.dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    this.bindEvents();
  }

  element(id) { return document.getElementById(id); }

  bindEvents() {
    this.element('choose-agreement').addEventListener('click', () => this.element('agreement-file').click());
    this.element('agreement-file').addEventListener('change', event => {
      const file = event.target.files[0];
      if (!file) return;
      this.file = file;
      this.creationKey = crypto.randomUUID();
      this.element('agreement-upload-filename').textContent = file.name;
      this.element('agreement-upload-filename').hidden = false;
      event.target.value = '';
      this.upload();
    });
    this.element('retry-agreement-upload').addEventListener('click', () => this.upload());
    this.element('retry-agreement-list').addEventListener('click', () => this.loadList());
    this.element('previous-agreement-list').addEventListener('click', () => { this.listPage--; this.loadList(); });
    this.element('next-agreement-list').addEventListener('click', () => { this.listPage++; this.loadList(); });
    this.element('all-agreements').addEventListener('click', event => {
      if (!this.plainClick(event)) return;
      event.preventDefault();
      this.navigateWorkspace('templates');
    });
    this.element('retry-agreement-preview').addEventListener('click', () => {
      if (this.agreement) this.loadPage(this.pageNumber);
      else this.open(this.route.split('/')[1]);
    });
    this.element('previous-agreement-page').addEventListener('click', () => this.loadPage(this.pageNumber - 1));
    this.element('next-agreement-page').addEventListener('click', () => this.loadPage(this.pageNumber + 1));
    this.element('agreement-page-number').addEventListener('change', event => {
      const page = Number(event.target.value);
      if (Number.isInteger(page) && page >= 1 && page <= this.agreement?.pageCount) this.loadPage(page);
      else event.target.value = this.pageNumber;
    });
    this.element('agreement-zoom').addEventListener('click', () => {
      const enlarged = this.element('agreement-page-viewport').classList.toggle('is-enlarged');
      this.element('agreement-zoom').setAttribute('aria-pressed', String(enlarged));
      this.element('agreement-zoom').textContent = enlarged ? 'Fit page to width' : 'Enlarge page';
    });
    this.element('agreement-prepare-toggle').addEventListener('click', () => {
      const editor = this.element('agreement-field-editor');
      const visible = editor.hidden;
      editor.hidden = !visible;
      this.element('agreement-prepare-toggle').textContent = visible ? 'Hide field tools' : 'Prepare fields';
      this.element('agreement-prepare-toggle').setAttribute('aria-expanded', String(visible));
      this.renderFields();
    });
    this.element('agreement-field-editor').addEventListener('click', event => {
      const add = event.target.closest('.field-add');
      if (add) this.addField(add.dataset.fieldType);
      if (event.target.id === 'agreement-field-delete') this.deleteSelectedField();
    });
    this.element('agreement-field-editor').addEventListener('input', event => this.updateSelectedField(event));
    this.element('agreement-field-editor').addEventListener('change', event => this.updateSelectedField(event));
    this.element('agreement-fields-save').addEventListener('click', () => this.saveFields());
    this.element('agreement-fields-reload').addEventListener('click', () => {
      if (this.agreement && this.confirmLeave()) this.open(this.agreement.id);
    });
    this.element('agreement-field-picker').addEventListener('change', event => {
      this.selectedFieldId = event.target.value || null;
      this.renderFields();
    });
    window.addEventListener('beforeunload', event => {
      if (!this.fieldsDirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
    document.addEventListener('click', event => {
      const link = event.target.closest('a[href]');
      if (!link || !this.fieldsDirty || !this.plainClick(event) || link.target === '_blank') return;
      const destination = new URL(link.href, location.href);
      if (destination.origin !== location.origin || destination.pathname !== location.pathname || destination.search !== location.search) {
        if (!this.confirmLeave()) {
          event.preventDefault();
          event.stopPropagation();
        }
      }
    }, true);
    window.addEventListener('pagehide', () => this.dispose());
    window.addEventListener('pageshow', event => {
      if (event.persisted) {
        this.route = null;
        this.navigate(window.location.hash.slice(1));
      }
    });
  }

  plainClick(event) { return !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey; }

  confirmLeave() {
    if (this.fieldsSaving) {
      window.alert('The field draft is being saved. Wait for the result before leaving.');
      return false;
    }
    return !this.fieldsDirty || window.confirm('Discard unsaved field changes and leave this agreement?');
  }

  canNavigate(route) {
    return !this.active || this.route === route || this.confirmLeave();
  }

  url(path = '', params = {}) {
    const url = new URL(path, this.baseUrl);
    if (url.origin !== window.location.origin) throw new SessionError('This action must stay within your workspace.');
    url.search = new URLSearchParams({ workspace_id: this.companyId, ...params });
    return url;
  }

  handleSession(error) {
    if (error.status === 401) {
      this.dispose();
      window.location.replace(document.body.dataset.loginUrl);
      return true;
    }
    if (error.code === 'workspace_changed') {
      this.dispose();
      window.location.reload();
      return true;
    }
    return false;
  }

  checkCompany(result) {
    if (result.companyId !== this.companyId) throw new SessionError('Your workspace has changed.', 409, {}, 'workspace_changed');
    return result;
  }

  showError(id, message) {
    const container = this.element(id);
    container.querySelector('p').textContent = message;
    container.hidden = false;
  }

  navigate(route) {
    if (this.route !== route) {
      this.stopFieldPointer?.();
      this.fieldSession++;
      this.fieldsDirty = false;
    }
    const [view, id] = route.split('/');
    if (view !== 'templates') {
      this.active = false;
      this.route = null;
      this.suspend();
      return;
    }
    if (this.route === route && this.active) return;
    this.active = true;
    this.route = route;
    this.suspend();
    const hasId = Boolean(id);
    this.element('agreement-library').hidden = hasId;
    this.element('agreement-preview').hidden = !hasId;
    if (hasId) this.open(id);
    else this.loadList();
  }

  async loadList() {
    this.listController?.abort();
    const controller = new AbortController();
    this.listController = controller;
    this.element('agreement-list-error').hidden = true;
    this.element('agreement-empty').hidden = true;
    this.element('agreement-list').setAttribute('aria-busy', 'true');
    this.element('agreement-list-status').textContent = 'Loading saved agreements…';
    for (const id of ['previous-agreement-list', 'next-agreement-list', 'retry-agreement-list']) this.element(id).disabled = true;
    try {
      const result = this.checkCompany(await this.client.request(this.url('', { page: this.listPage }), undefined, { signal: controller.signal }));
      if (controller.signal.aborted) return;
      this.limits = result.limits;
      this.element('agreement-format').textContent = `PDF or DOCX · up to ${result.limits.maxBytes / (1024 * 1024)} MiB · ${result.limits.maxPages} PDF pages.`;
      const fragment = document.createDocumentFragment();
      for (const agreement of result.agreements) {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.className = 'file-name';
        link.href = `#templates/${agreement.id}`;
        link.textContent = agreement.filename;
        link.addEventListener('click', event => {
          if (!this.plainClick(event)) return;
          event.preventDefault();
          this.navigateWorkspace(`templates/${agreement.id}`);
        });
        const details = document.createElement('div');
        details.className = 'agreement-list-details';
        const badge = document.createElement('span');
        badge.className = 'status-badge waiting';
        badge.textContent = 'Unprepared';
        const processing = document.createElement('span');
        processing.textContent = agreement.processingLabel;
        const saved = document.createElement('span');
        const date = new Date(agreement.createdAt);
        saved.textContent = [agreement.pageCount ? `${agreement.pageCount} ${agreement.pageCount === 1 ? 'page' : 'pages'}` : '', Number.isNaN(date.getTime()) ? '' : this.dateFormatter.format(date)].filter(Boolean).join(' · ');
        details.append(badge, processing, saved);
        item.append(link, details);
        fragment.append(item);
      }
      this.element('agreement-list').replaceChildren(fragment);
      const pagination = result.pagination;
      this.listPage = pagination.number;
      this.element('agreement-list-status').textContent = `${pagination.total} saved ${pagination.total === 1 ? 'agreement' : 'agreements'}`;
      this.element('agreement-empty').hidden = pagination.total > 0;
      this.element('agreement-list-pagination').hidden = pagination.pages <= 1;
      this.element('agreement-list-page').textContent = `Page ${pagination.number} of ${pagination.pages}`;
      this.element('previous-agreement-list').disabled = !pagination.hasPrevious;
      this.element('next-agreement-list').disabled = !pagination.hasNext;
    } catch (error) {
      if (error.name === 'AbortError' || this.handleSession(error)) return;
      this.showError('agreement-list-error', error.message);
      this.element('agreement-list-status').textContent = 'Saved agreements could not be loaded.';
    } finally {
      if (this.listController === controller) {
        this.element('agreement-list').setAttribute('aria-busy', 'false');
        this.element('retry-agreement-list').disabled = false;
      }
    }
  }

  setUploadBusy(busy) {
    this.element('choose-agreement').disabled = busy;
    this.element('choose-agreement').setAttribute('aria-busy', String(busy));
    this.element('retry-agreement-upload').disabled = busy;
    this.element('agreement-upload-progress').hidden = !busy;
  }

  async upload() {
    if (!this.file || this.xhr) return;
    this.element('agreement-upload-error').hidden = true;
    this.element('agreement-upload-status').textContent = '';
    if (!this.file.size) return this.showError('agreement-upload-error', 'This file is empty. Choose a PDF or DOCX with at least one page.');
    if (this.limits && this.file.size > this.limits.maxBytes) return this.showError('agreement-upload-error', `This file is too large. Choose a PDF or DOCX up to ${this.limits.maxBytes / (1024 * 1024)} MiB.`);
    this.setUploadBusy(true);
    this.element('agreement-upload-progress').value = 0;
    this.element('agreement-upload-status').textContent = 'Uploading document…';
    try {
      const result = this.checkCompany(await this.sendFile());
      this.element('agreement-upload-status').textContent = 'Document validated and saved privately. Agreement is Unprepared.';
      this.listPage = 1;
      this.file = null;
      this.creationKey = null;
      if (this.active) this.navigateWorkspace(`templates/${result.agreement.id}`);
    } catch (error) {
      if (error.name === 'AbortError') {
        this.element('agreement-upload-status').textContent = 'Upload interrupted.';
        this.showError('agreement-upload-error', 'Retry this upload to check whether it was saved, or choose another document.');
        return;
      }
      if (this.handleSession(error)) return;
      this.element('agreement-upload-status').textContent = 'Upload needs attention.';
      this.showError('agreement-upload-error', error.message);
    } finally {
      this.xhr = null;
      this.setUploadBusy(false);
    }
  }

  sendFile() {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      this.xhr = xhr;
      xhr.open('POST', this.url('upload/'));
      xhr.timeout = 120000;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-CSRFToken', document.querySelector('meta[name="csrf-token"]').content);
      xhr.setRequestHeader('X-Workspace-ID', this.companyId);
      xhr.setRequestHeader('X-Upload-Key', this.creationKey);
      xhr.setRequestHeader('X-Upload-Filename', encodeURIComponent(this.file.name));
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable) {
          const percent = Math.round(event.loaded / event.total * 100);
          this.element('agreement-upload-progress').value = percent;
          this.element('agreement-upload-status').textContent = percent < 100 ? `Uploading document… ${percent}%` : 'Checking every page and saving privately…';
        }
      });
      xhr.upload.addEventListener('load', () => {
        this.element('agreement-upload-progress').removeAttribute('value');
        this.element('agreement-upload-status').textContent = 'Checking every page and saving privately…';
      });
      xhr.addEventListener('load', () => {
        if (xhr.status === 403) return reject(new SessionError('Your session has changed. Reload this page and try again.', 403));
        let result;
        try { result = JSON.parse(xhr.responseText); }
        catch { return reject(new SessionError('The server could not confirm this upload. Retry to check and save the same upload.', xhr.status)); }
        if (xhr.status < 200 || xhr.status >= 300) return reject(new SessionError(result.error || 'The upload could not be completed. Try again.', xhr.status, {}, result.code));
        resolve(result);
      });
      xhr.addEventListener('error', () => reject(new SessionError('Connection lost. Retry this upload to check whether it was saved.')));
      xhr.addEventListener('timeout', () => reject(new SessionError('The server did not respond in time. Retry this upload to check whether it was saved.')));
      xhr.addEventListener('abort', () => reject(new DOMException('Upload interrupted.', 'AbortError')));
      xhr.send(this.file);
    });
  }

  async open(id) {
    this.fieldSession++;
    this.fieldsSaving = false;
    this.agreement = null;
    this.fields = [];
    this.selectedFieldId = null;
    this.fieldsDirty = false;
    this.element('agreement-field-editor').hidden = true;
    this.element('agreement-fields-save').disabled = true;
    this.element('agreement-fields-reload').hidden = true;
    this.element('agreement-fields-status').classList.remove('has-error');
    this.element('agreement-fields-status').textContent = 'No unsaved field changes.';
    this.element('agreement-preview-error').hidden = true;
    this.element('retry-agreement-preview').disabled = false;
    this.element('agreement-preview-toolbar').hidden = true;
    this.element('agreement-prepare-toggle').hidden = true;
    this.element('agreement-field-editor').hidden = true;
    this.element('agreement-preview-filename').textContent = 'Opening agreement…';
    this.element('agreement-processing-status').textContent = 'Loading document status…';
    this.element('agreement-page-status').textContent = '';
    const controller = new AbortController();
    this.detailController = controller;
    try {
      if (!/^[0-9a-f-]{36}$/i.test(id)) throw new SessionError('This agreement link is invalid. Return to all agreements.');
      const result = this.checkCompany(await this.client.request(this.url(`${id}/`), undefined, { signal: controller.signal }));
      if (controller.signal.aborted) return;
      this.agreement = result.agreement;
      this.fields = this.agreement.fields || [];
      this.selectedFieldId = null;
      this.fieldsDirty = false;
      this.element('agreement-preview-filename').textContent = this.agreement.filename;
      this.element('agreement-processing-status').textContent = [this.agreement.processingLabel, this.agreement.conversionVersion].filter(Boolean).join(' · ');
      if (this.agreement.processingState !== 'ready') {
        this.element('agreement-page-status').textContent = this.agreement.processingError || 'Preview is available after document validation succeeds. Upload a PDF to try again.';
        return;
      }
      this.pageNumber = 1;
      this.element('agreement-page-number').max = this.agreement.pageCount;
      this.element('agreement-page-total').textContent = `of ${this.agreement.pageCount}`;
      this.element('agreement-preview-toolbar').hidden = false;
      this.element('agreement-prepare-toggle').hidden = false;
      this.element('agreement-field-editor').hidden = true;
      this.element('agreement-prepare-toggle').textContent = 'Prepare fields';
      this.element('agreement-prepare-toggle').setAttribute('aria-expanded', 'false');
      this.element('agreement-page-viewport').classList.remove('is-enlarged');
      this.element('agreement-zoom').textContent = 'Enlarge page';
      this.element('agreement-zoom').setAttribute('aria-pressed', 'false');
      this.loadPage(1);
    } catch (error) {
      if (error.name === 'AbortError' || this.handleSession(error)) return;
      this.element('agreement-preview-filename').textContent = 'Agreement unavailable';
      this.element('agreement-processing-status').textContent = 'Could not load document status';
      this.showError('agreement-preview-error', error.message);
    }
  }

  async loadPage(number) {
    if (!this.agreement || number < 1 || number > this.agreement.pageCount) return;
    this.pageController?.abort();
    const controller = new AbortController();
    this.pageController = controller;
    const attempt = ++this.previewAttempt;
    this.clearImage();
    this.pageNumber = number;
    this.stopFieldPointer?.();
    if (this.currentField()?.page !== number) this.selectedFieldId = null;
    this.element('agreement-page-number').value = number;
    this.element('previous-agreement-page').disabled = number <= 1;
    this.element('next-agreement-page').disabled = number >= this.agreement.pageCount;
    this.element('agreement-preview-error').hidden = true;
    this.element('agreement-page-status').textContent = `Loading page ${number} of ${this.agreement.pageCount}…`;
    this.element('agreement-zoom').disabled = true;
    this.element('retry-agreement-preview').disabled = true;
    try {
      const response = await fetch(this.url(`${this.agreement.pageUrl}${number}/`), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      if (!response.ok) {
        let result = {};
        try { result = await response.json(); } catch { /* A proxy may return a non-JSON failure. */ }
        throw new SessionError(result.error || 'This page could not be loaded. Try again.', response.status, {}, result.code);
      }
      if (!response.headers.get('Content-Type')?.startsWith('image/png')) throw new SessionError('The server did not return a page preview. Try again.');
      const blob = await response.blob();
      if (attempt !== this.previewAttempt || controller.signal.aborted) return;
      this.objectUrl = URL.createObjectURL(blob);
      const image = this.element('agreement-page-image');
      image.alt = `${this.agreement.filename}, page ${number} of ${this.agreement.pageCount}`;
      image.src = this.objectUrl;
      await image.decode();
      if (attempt !== this.previewAttempt || controller.signal.aborted) return;
      this.element('agreement-page-viewport').hidden = false;
      this.element('agreement-page-status').textContent = `Page ${number} of ${this.agreement.pageCount}. Enlarge the page to read smaller text.`;
      this.element('agreement-zoom').disabled = false;
      this.renderFields();
    } catch (error) {
      if (error.name === 'AbortError' || attempt !== this.previewAttempt || this.handleSession(error)) return;
      this.clearImage();
      this.element('agreement-page-status').textContent = `Page ${number} could not be displayed.`;
      this.showError('agreement-preview-error', error instanceof SessionError ? error.message : 'Could not display this page. Try again.');
    } finally {
      if (attempt === this.previewAttempt) this.element('retry-agreement-preview').disabled = false;
    }
  }

  clearImage() {
    this.element('agreement-page-viewport').hidden = true;
    this.element('agreement-page-image').removeAttribute('src');
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
    this.element('agreement-field-layer').replaceChildren();
  }

  currentField() {
    return this.fields.find(field => field.id === this.selectedFieldId);
  }

  markFieldsDirty(message = 'Unsaved field changes.') {
    this.fieldsDirty = true;
    this.fieldEditVersion++;
    this.element('agreement-fields-status').textContent = message;
    this.element('agreement-fields-status').classList.remove('has-error');
    this.element('agreement-fields-save').disabled = this.fieldsSaving;
  }

  addField(type) {
    if (!this.agreement || this.agreement.processingState !== 'ready') return;
    if (this.fields.length >= 200) {
      this.element('agreement-fields-status').textContent = 'A draft supports up to 200 fields. Delete a field before adding another.';
      return;
    }
    const labels = {
      name: 'Name', text: 'Text', date: 'Date', signature: 'Signature',
      initials: 'Initials', acknowledgement: 'Acknowledgement',
    };
    const field = {
      id: crypto.randomUUID(), type, label: labels[type] || 'Field',
      required: true, role: 'client', page: this.pageNumber,
      x: 0.1, y: 0.1, width: type === 'signature' ? 0.28 : 0.24, height: type === 'acknowledgement' ? 0.09 : 0.06,
    };
    this.fields.push(field);
    this.selectedFieldId = field.id;
    this.markFieldsDirty();
    this.renderFields();
  }

  deleteSelectedField() {
    if (!this.selectedFieldId) return;
    this.fields = this.fields.filter(field => field.id !== this.selectedFieldId);
    this.selectedFieldId = null;
    this.markFieldsDirty();
    this.renderFields();
  }

  updateSelectedField(event) {
    const field = this.currentField();
    if (!field) return;
    const key = event.target.id.replace('agreement-field-', '');
    if (key === 'label' || key === 'role' || key === 'required') {
      field[key] = key === 'required' ? event.target.checked : event.target.value;
    } else if (['x', 'y', 'width', 'height'].includes(key)) {
      if (event.type !== 'change') return;
      const value = event.target.valueAsNumber;
      if (!Number.isFinite(value)) return;
      const maximum = { x: 1 - field.width, y: 1 - field.height, width: 1 - field.x, height: 1 - field.y }[key];
      field[key] = Math.max(key === 'x' || key === 'y' ? 0 : 0.01, Math.min(maximum, value));
    } else {
      return;
    }
    this.markFieldsDirty();
    this.renderFields(event.type === 'change');
  }

  renderFields(syncProperties = true) {
    const layer = this.element('agreement-field-layer');
    const activeField = document.activeElement?.dataset.fieldId;
    layer.inert = this.element('agreement-field-editor').hidden;
    layer.replaceChildren();
    const pageFields = this.fields.filter(item => item.page === this.pageNumber);
    const picker = this.element('agreement-field-picker');
    picker.replaceChildren(new Option('Choose a field on this page', ''));
    for (const field of pageFields) {
      picker.add(new Option(`${field.label || field.type} · ${field.role}`, field.id));
      const element = document.createElement('button');
      element.type = 'button';
      element.className = 'agreement-field';
      element.dataset.fieldId = field.id;
      element.setAttribute('aria-pressed', String(field.id === this.selectedFieldId));
      element.title = `${field.label || field.type} · ${field.role} · placeholder`;
      element.textContent = `${field.label || field.type}${field.required ? ' *' : ''}`;
      Object.assign(element.style, {
        left: `${field.x * 100}%`, top: `${field.y * 100}%`,
        width: `${field.width * 100}%`, height: `${field.height * 100}%`,
      });
      const resize = document.createElement('span');
      resize.className = 'agreement-field-resize';
      resize.setAttribute('aria-hidden', 'true');
      element.append(resize);
      element.addEventListener('click', event => {
        event.stopPropagation();
        this.selectedFieldId = field.id;
        this.renderFields();
      });
      element.addEventListener('keydown', event => {
        if (event.key === 'Delete' || event.key === 'Backspace') {
          event.preventDefault();
          this.selectedFieldId = field.id;
          this.deleteSelectedField();
          picker.focus();
        }
      });
      element.addEventListener('pointerdown', event => this.startFieldPointer(event, field, event.target === resize));
      layer.append(element);
    }
    picker.value = this.selectedFieldId || '';
    if (activeField && !this.dragState) {
      [...layer.children].find(element => element.dataset.fieldId === activeField)?.focus({ preventScroll: true });
    }
    const selected = this.currentField();
    this.element('agreement-field-properties').hidden = !selected;
    if (selected && syncProperties) {
      this.element('agreement-field-label').value = selected.label;
      this.element('agreement-field-role').value = selected.role;
      this.element('agreement-field-required').checked = selected.required;
      for (const key of ['x', 'y', 'width', 'height']) this.element(`agreement-field-${key}`).value = Number(selected[key].toFixed(4));
    }
  }

  startFieldPointer(event, field, resizing) {
    if (event.button !== 0 || this.element('agreement-field-editor').hidden) return;
    this.stopFieldPointer?.();
    event.preventDefault();
    this.selectedFieldId = field.id;
    const canvas = this.element('agreement-page-canvas');
    const rect = canvas.getBoundingClientRect();
    this.dragState = {
      field, resizing, startX: event.clientX, startY: event.clientY,
      x: field.x, y: field.y, width: field.width, height: field.height,
      rect,
    };
    const move = moveEvent => {
      if (!this.dragState) return;
      const state = this.dragState;
      const dx = (moveEvent.clientX - state.startX) / state.rect.width;
      const dy = (moveEvent.clientY - state.startY) / state.rect.height;
      if (state.resizing) {
        state.field.width = Math.min(1 - state.field.x, Math.max(0.01, state.width + dx));
        state.field.height = Math.min(1 - state.field.y, Math.max(0.01, state.height + dy));
      } else {
        state.field.x = Math.min(1 - state.field.width, Math.max(0, state.x + dx));
        state.field.y = Math.min(1 - state.field.height, Math.max(0, state.y + dy));
      }
      this.markFieldsDirty();
      this.renderFields();
    };
    const stop = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', stop);
      document.removeEventListener('pointercancel', stop);
      window.removeEventListener('blur', stop);
      this.dragState = null;
      this.stopFieldPointer = null;
    };
    this.stopFieldPointer = stop;
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', stop, { once: true });
    document.addEventListener('pointercancel', stop, { once: true });
    window.addEventListener('blur', stop, { once: true });
    this.renderFields();
  }

  async saveFields() {
    if (!this.agreement || !this.fieldsDirty || this.fieldsSaving) return;
    const agreementId = this.agreement.id;
    const session = this.fieldSession;
    const editVersion = this.fieldEditVersion;
    const snapshot = JSON.parse(JSON.stringify(this.fields));
    this.fieldsSaving = true;
    const button = this.element('agreement-fields-save');
    button.disabled = true;
    this.element('agreement-fields-status').textContent = 'Saving field draft…';
    this.element('agreement-fields-status').classList.remove('has-error');
    try {
      const response = await fetch(this.url(`${this.agreement.id}/fields/`), {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
          'X-Workspace-ID': this.companyId,
        }, body: JSON.stringify({ fields: snapshot, revision: this.agreement.fieldsRevision, pdfSha256: this.agreement.pdfSha256 }),
      });
      let payload;
      try { payload = await response.json(); }
      catch { throw new SessionError('The server could not confirm this save. Retry to check and save the same draft.', response.status); }
      if (!response.ok) throw new SessionError(payload.error || 'Field draft could not be saved.', response.status, {}, payload.code);
      if (!payload?.agreement || payload.agreement.id !== agreementId || !Array.isArray(payload.agreement.fields)
        || !Number.isInteger(payload.agreement.fieldsRevision)) {
        throw new SessionError('The server could not confirm this save. Retry saving the field draft.');
      }
      const result = this.checkCompany(payload);
      if (session !== this.fieldSession || this.agreement?.id !== agreementId) return;
      this.agreement = result.agreement;
      this.fieldsDirty = this.fieldEditVersion !== editVersion;
      if (!this.fieldsDirty) {
        this.stopFieldPointer?.();
        this.fields = this.agreement.fields || [];
      }
      this.element('agreement-fields-status').textContent = this.fieldsDirty
        ? 'Earlier changes saved. You have newer unsaved field changes.'
        : 'Field draft saved. Agreement remains Unprepared.';
      this.renderFields();
    } catch (error) {
      if (session !== this.fieldSession || this.agreement?.id !== agreementId) return;
      if (this.handleSession(error)) return;
      this.element('agreement-fields-status').classList.add('has-error');
      this.element('agreement-fields-reload').hidden = error.code !== 'draft_conflict';
      this.element('agreement-fields-status').textContent = error instanceof TypeError
        ? 'Connection lost. Retry saving the field draft.'
        : error.message || 'Field draft could not be saved.';
    } finally {
      if (session === this.fieldSession) {
        this.fieldsSaving = false;
        button.disabled = !this.fieldsDirty;
      }
    }
  }

  suspend() {
    this.stopFieldPointer?.();
    this.listController?.abort();
    this.detailController?.abort();
    this.pageController?.abort();
    this.previewAttempt++;
    this.clearImage();
  }

  dispose() {
    this.suspend();
    this.xhr?.abort();
    this.active = false;
    this.route = null;
  }
}
