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
    window.addEventListener('pagehide', () => this.dispose());
    window.addEventListener('pageshow', event => {
      if (event.persisted) {
        this.route = null;
        this.navigate(window.location.hash.slice(1));
      }
    });
  }

  plainClick(event) { return !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey; }

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
      this.element('agreement-format').textContent = `PDF only · up to ${result.limits.maxBytes / (1024 * 1024)} MiB · ${result.limits.maxPages} pages. DOCX conversion comes later.`;
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
    if (!this.file.size) return this.showError('agreement-upload-error', 'This file is empty. Choose a PDF with at least one page.');
    if (this.limits && this.file.size > this.limits.maxBytes) return this.showError('agreement-upload-error', `This file is too large. Choose a PDF up to ${this.limits.maxBytes / (1024 * 1024)} MiB.`);
    this.setUploadBusy(true);
    this.element('agreement-upload-progress').value = 0;
    this.element('agreement-upload-status').textContent = 'Uploading PDF…';
    try {
      const result = this.checkCompany(await this.sendFile());
      this.element('agreement-upload-status').textContent = 'PDF validated and saved privately. Agreement is Unprepared.';
      this.listPage = 1;
      this.file = null;
      this.creationKey = null;
      if (this.active) this.navigateWorkspace(`templates/${result.agreement.id}`);
    } catch (error) {
      if (error.name === 'AbortError') {
        this.element('agreement-upload-status').textContent = 'Upload interrupted.';
        this.showError('agreement-upload-error', 'Retry this upload to check whether it was saved, or choose another PDF.');
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
      xhr.setRequestHeader('Content-Type', 'application/pdf');
      xhr.setRequestHeader('X-CSRFToken', document.querySelector('meta[name="csrf-token"]').content);
      xhr.setRequestHeader('X-Workspace-ID', this.companyId);
      xhr.setRequestHeader('X-Upload-Key', this.creationKey);
      xhr.setRequestHeader('X-Upload-Filename', encodeURIComponent(this.file.name));
      xhr.upload.addEventListener('progress', event => {
        if (event.lengthComputable) {
          const percent = Math.round(event.loaded / event.total * 100);
          this.element('agreement-upload-progress').value = percent;
          this.element('agreement-upload-status').textContent = percent < 100 ? `Uploading PDF… ${percent}%` : 'Checking every page and saving privately…';
        }
      });
      xhr.upload.addEventListener('load', () => {
        this.element('agreement-upload-progress').removeAttribute('value');
        this.element('agreement-upload-status').textContent = 'Checking every page and saving privately…';
      });
      xhr.addEventListener('load', () => {
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
    this.agreement = null;
    this.element('agreement-preview-error').hidden = true;
    this.element('retry-agreement-preview').disabled = false;
    this.element('agreement-preview-toolbar').hidden = true;
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
      this.element('agreement-preview-filename').textContent = this.agreement.filename;
      this.element('agreement-processing-status').textContent = this.agreement.processingLabel;
      if (this.agreement.processingState !== 'ready') {
        this.element('agreement-page-status').textContent = this.agreement.processingError || 'Preview is available after document validation succeeds. Upload a PDF to try again.';
        return;
      }
      this.pageNumber = 1;
      this.element('agreement-page-number').max = this.agreement.pageCount;
      this.element('agreement-page-total').textContent = `of ${this.agreement.pageCount}`;
      this.element('agreement-preview-toolbar').hidden = false;
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
  }

  suspend() {
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
