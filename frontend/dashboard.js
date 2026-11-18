import { SessionClient } from './shared/ui.js';
import { AgreementWorkspace } from './agreements.js?v=ui22';
import { ClientDirectory } from './client_workspace.js?v=login1';

class WorkspaceDashboard {
  static views = new Map([
    ['overview', { title: 'Overview', description: 'Agreements that need your attention, from preparation to completion.', label: 'Overview' }],
    ['files', { title: 'Clients & agreements', description: 'Every client, job and agreement in one place.', label: 'Clients' }],
    ['templates', { title: 'Agreement templates', description: 'Prepare a document once. Reuse its ready version for the next client.', label: 'Agreement templates' }],
    ['help', { title: 'Help & guidance', description: 'How preparation, signing and demo delivery work.', label: 'Help & guidance' }],
  ]);

  static statuses = new Map([
    ['draft', { label: 'Draft', tone: 'neutral' }],
    ['issuing', { label: 'Preparing request', tone: 'info' }],
    ['awaiting_client', { label: 'Awaiting client', tone: 'waiting' }],
    ['awaiting_company', { label: 'Needs your signature', tone: 'waiting' }],
    ['finalizing', { label: 'Finalizing', tone: 'info' }],
    ['completed', { label: 'Completed', tone: 'success' }],
    ['cancelled', { label: 'Cancelled', tone: 'neutral' }],
    ['expired', { label: 'Expired', tone: 'error' }],
  ]);

  constructor(data) {
    this.client = new SessionClient();
    this.agreements = new AgreementWorkspace(route => this.navigate(route));
    this.directory = new ClientDirectory();
    this.files = new Map();
    this.dateFormatter = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    this.filter = 'all';
    this.grouping = new URLSearchParams(location.search).has('client') ? 'clients' : 'agreements';
    this.search = document.querySelector('#file-search');
    this.sort = document.querySelector('#file-sort');
    this.account = document.querySelector('.account-menu');
    this.menu = document.querySelector('.menu-toggle');
    this.mobile = window.matchMedia('(max-width: 800px)');
    this.error = document.querySelector('#workspace-error');
    this.page = 1;
    this.requestNumber = 0;
    this.searchTimer = null;
    this.requestController = null;
    this.previousPage = document.querySelector('#previous-file-page');
    this.nextPage = document.querySelector('#next-file-page');
    document.body.classList.add('enhanced');
    this.setMenu(false);
    this.bindEvents();
    this.statusTimer = setInterval(() => { if (!document.hidden && this.activeView === 'files' && !this.requestController) this.refreshWorkspace(); },15000);
    window.addEventListener('focus', () => { if(this.activeView === 'files') this.refreshWorkspace(); });
    window.addEventListener('pagehide', () => clearInterval(this.statusTimer));
    const initialRoute = window.location.hash.slice(1) || 'files';
    this.navigate(initialRoute, false, false);
    this.applySnapshot(data);
  }

  bindEvents() {
    document.addEventListener('click', event => {
      if (event.target.closest('.skip-link')) {
        event.preventDefault();
        const main = document.querySelector('#workspace-main');
        main.focus();
        main.scrollIntoView({ block: 'start' });
        return;
      }
      const viewLink = event.target.closest('[data-view]');
      if (viewLink && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        this.navigate(viewLink.dataset.view);
      }
      const metric = event.target.closest('[data-filter-link]');
      if (metric) {
        
        this.search.value = '';
        this.setFilter(metric.dataset.filterLink);
        this.navigate('files');
      }
      const filter = event.target.closest('[data-filter]');
      if (filter) this.setFilter(filter.dataset.filter);
      const grouping = event.target.closest('[data-grouping]');
      if (grouping) this.setGrouping(grouping.dataset.grouping);
      if (!event.target.closest('.account-menu')) this.account.open = false;
    });
    window.addEventListener('popstate', () => this.navigate(window.location.hash.slice(1), false));
    window.addEventListener('hashchange', () => this.navigate(window.location.hash.slice(1), false));
    window.addEventListener('pageshow', event => { if (event.persisted) this.refreshWorkspace(); });
    window.addEventListener('pagehide', () => {
      window.clearTimeout(this.searchTimer);
      this.requestController?.abort();
    });
    this.search.addEventListener('input', () => {
      this.page = 1;
      this.requestNumber++;
      this.requestController?.abort();
      window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => {
        if (this.grouping === 'clients') { this.directory.search.value = this.search.value; this.directory.page = 1; this.directory.load(); }
        else this.refreshWorkspace();
      }, 250);
    });
    document.querySelector('#new-client-toggle').addEventListener('click', () => {
      this.setGrouping('clients');
      document.querySelector('#new-client-details').open = true;
      this.directory.newClientForm.elements.name.focus();
    });
    this.sort.addEventListener('change', () => {
      this.page = 1;
      this.refreshWorkspace();
    });
    this.menu.addEventListener('click', () => this.setMenu(this.menu.getAttribute('aria-expanded') !== 'true'));
    this.mobile.addEventListener('change', () => this.setMenu(false));
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      if (this.account.open) {
        this.account.open = false;
        this.account.querySelector('summary').focus();
      }
      if (this.menu.getAttribute('aria-expanded') === 'true') {
        this.setMenu(false);
        this.menu.focus();
      }
    });
    document.querySelector('#reset-filters').addEventListener('click', () => {
      this.search.value = '';
      this.setFilter('all');
      this.search.focus();
    });
    document.querySelector('#retry-session').addEventListener('click', () => this.refreshWorkspace());
    this.previousPage.addEventListener('click', () => {
      this.page--;
      this.refreshWorkspace();
    });
    this.nextPage.addEventListener('click', () => {
      this.page++;
      this.refreshWorkspace();
    });
    document.querySelector('.logout-button').addEventListener('click', () => this.logout());
  }

  navigate(view, updateHistory = true, focus = true) {
    const clientShortcut = view === 'clients';
    if (clientShortcut) view = 'files';
    let route = view;
    view = view.split('/')[0];
    if (!WorkspaceDashboard.views.has(view)) view = 'overview';
    if (view !== 'templates') route = view;
    if (!this.agreements.canNavigate(route)) {
      window.history.replaceState(null, '', `#${this.agreements.route || 'templates'}`);
      return;
    }
    this.activeView = view;
    const current = WorkspaceDashboard.views.get(view);
    document.querySelectorAll('.workspace-view').forEach(section => { section.hidden = section.id !== `view-${view}`; });
    document.querySelectorAll('.sidebar .nav-link').forEach(link => {
      if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    document.querySelector('#current-location').textContent = current.label;
    const heading = document.querySelector('#page-title');
    heading.textContent = current.title;
    document.querySelector('#page-description').textContent = current.description;
    document.querySelector('.heading-action').hidden = view !== 'overview';
    document.title = `${current.label} · Astra`;
    if (updateHistory && window.location.hash !== `#${route}`) window.history.pushState(null, '', `#${route}`);
    this.agreements.navigate(route);
    document.querySelector('.reference-create').hidden = view !== 'files';
    if (view === 'files') this.setGrouping(clientShortcut ? 'clients' : this.grouping);
    if (view === 'files' && this.pagination) this.refreshWorkspace();
    this.setMenu(false);
    if (focus) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: 'instant' });
    }
  }

  setMenu(open) {
    document.body.dataset.menuOpen = String(open);
    this.menu.setAttribute('aria-expanded', String(open));
    this.menu.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  }

  applySnapshot(data) {
    if (data.companyId !== document.body.dataset.companyId) {
      window.location.reload();
      return;
    }
    // Keep only the current page and four recent records in browser memory.
    this.files.clear();
    for (const file of data.files) this.files.set(file.id, file);
    this.recentFiles = data.recentFiles;
    this.counts = data.counts;
    document.querySelector('#client-nav-count').textContent = data.clientCount || 0;
    this.pagination = data.pagination;
    this.page = data.pagination.number;
    this.renderSummary();
    this.renderFiles();
  }

  setGrouping(grouping, refresh = true) {
    this.grouping = grouping === 'agreements' ? 'agreements' : 'clients';
    const clients = this.grouping === 'clients';
    this.directory.root.hidden = !clients;
    document.querySelector('#agreement-list-view').hidden = clients;
    this.sort.closest('label').hidden = clients;
    this.search.placeholder = clients ? 'Search by client, email or job reference…' : 'Search by agreement, client or reference…';
    document.querySelector('#grouping-hint').textContent = clients ? 'Open a client to see their jobs and agreements. Choose a status to search all agreements.' : 'Search agreements across every client and job.';
    document.querySelectorAll('[data-grouping]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.grouping === this.grouping)));
    if (clients) {
      this.filter = 'all';
      document.querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === 'all')));
      if (this.directory.search.value !== this.search.value) {
        this.directory.search.value = this.search.value;
        this.directory.page = 1;
        this.directory.load();
      } else this.directory.show();
    } else if (refresh && this.pagination) this.refreshWorkspace();
  }

  setFilter(filter) {
    this.setGrouping('agreements', false);
    this.filter = filter;
    this.page = 1;
    document.querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === filter)));
    this.refreshWorkspace();
  }

  renderSummary() {
    document.querySelectorAll('[data-count]').forEach(element => { element.textContent = this.counts[element.dataset.count]; });
    this.renderRows(document.querySelector('#recent-file-rows'), this.recentFiles);
    document.querySelector('#recent-empty').hidden = this.recentFiles.length > 0;
    this.renderProgress();
  }

  // Overview guidance follows the live counts: tick finished steps and point the welcome card at the next job.
  renderProgress() {
    const { all = 0, draft = 0, waiting = 0, ready = 0, attention = 0 } = this.counts || {};
    const done = { template: all > 0, file: all > 0, sent: all - draft > 0, signed: ready > 0 };
    let currentFound = false;
    document.querySelectorAll('[data-step]').forEach(step => {
      const finished = done[step.dataset.step];
      step.classList.toggle('done', finished);
      step.classList.toggle('current', !finished && !currentFound);
      if (!finished) currentFound = true;
    });
    const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;
    const card = !all ? null
      : attention ? [`${plural(attention, 'agreement')} need${attention === 1 ? 's' : ''} you.`, 'Company signatures, missing details or processing are waiting for your action.', 'Review them', 'attention']
      : waiting ? [`Waiting on ${plural(waiting, 'client signature')}.`, 'Share the signing link and code with the client, then check back here.', 'See who is waiting', 'waiting']
      : ready < all ? ['Final records still need processing.', 'All required signatures are recorded. Check final processing before starting work.', 'Check processing', 'all']
      : ['Everything is signed and saved.', 'Start the next client agreement whenever you are ready.', 'Start a new client agreement', null];
    if (!card) return;
    document.querySelector('.setup-footnote').hidden = true;
    const action = document.querySelector('#setup-action');
    document.querySelector('#setup-title').textContent = card[0];
    document.querySelector('#setup-copy').textContent = card[1];
    document.querySelector('#setup-action-label').textContent = card[2];
    if (card[3]) { action.href = '#files'; action.removeAttribute('data-view'); action.dataset.filterLink = card[3]; }
    else { action.href = '#clients'; action.dataset.view = 'clients'; delete action.dataset.filterLink; }
  }

  timestamp(file) {
    return Date.parse(file.updatedAt) || 0;
  }

  renderFiles() {
    const query = this.search.value.trim();
    this.renderRows(document.querySelector('#all-file-rows'), this.files.values());
    const noMatches = Boolean(query) || this.filter !== 'all';
    document.querySelector('#files-empty').hidden = this.files.size > 0;
    document.querySelector('#files-empty-title').textContent = noMatches ? 'No matching client files.' : 'Your next client starts here.';
    document.querySelector('#files-empty-copy').textContent = noMatches
      ? 'Try another client name or reference, or clear your filters to see every file.'
      : 'Create a client file to save client details and return to its setup later.';
    document.querySelector('#reset-filters').hidden = !noMatches;
    document.querySelector('#files-template-link').hidden = noMatches;
    const { number, pages, total, pageSize, hasPrevious, hasNext } = this.pagination;
    const start = total ? (number - 1) * pageSize + 1 : 0;
    const message = total ? `${start}–${start + this.files.size - 1} of ${total} client ${total === 1 ? 'file' : 'files'}` : 'No client files shown.';
    document.querySelector('#file-results-summary').textContent = message;
    document.querySelector('#file-pagination').hidden = pages <= 1;
    document.querySelector('#file-page-label').textContent = `Page ${number} of ${pages}`;
    this.previousPage.disabled = !hasPrevious;
    this.nextPage.disabled = !hasNext;
    document.querySelector('#workspace-status').textContent = message;
  }

  renderRows(container, files) {
    const fragment = document.createDocumentFragment();
    for (const file of files) {
      const row = document.createElement('tr');
      const cell = label => { const value = row.insertCell(); value.dataset.label = label; return value; };
      const nameCell = cell('Agreement / file');
      const name = document.createElement('a');
      name.className = 'file-name'; name.href = file.detailUrl;
      name.textContent = file.title || file.reference;
      const icon = document.createElement('span'); icon.className = 'reference-file-icon'; icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML = '<svg><use href="#icon-document"/></svg>';
      const copy = document.createElement('div');
      const reference = document.createElement('span'); reference.className = 'file-reference'; reference.textContent = file.reference;
      copy.append(name, reference); const identity = document.createElement('div'); identity.className = 'reference-file-name'; identity.append(icon, copy); nameCell.append(identity);
      if (container.id === 'all-file-rows') cell('Client').textContent = file.clientName;
      const state = WorkspaceDashboard.statuses.get(file.status) || { label: 'Status unavailable', tone: 'neutral' };
      const processingError = file.needsAttention && file.status === 'finalizing';
      const badge = document.createElement('span'); badge.className = `status-badge ${processingError ? 'error' : state.tone}`;
      badge.textContent = processingError ? 'Processing needs attention' : state.label;
      const detail = document.createElement('span'); detail.className = 'file-reference';
      detail.textContent = ({draft:'Not sent yet',awaiting_client:'Client signature pending',awaiting_company:'Client signed · your company signature is required',completed:'All signatures and final records saved',cancelled:'Further signing stopped',expired:'Signing deadline passed',finalizing:'Preparing final records'})[file.status] || '';
      cell('Agreement status').append(badge,detail);
      const updated = cell('Updated'); updated.textContent = this.timestamp(file) ? this.dateFormatter.format(new Date(file.updatedAt)) : '—';
      const time = document.createElement('span'); time.className = 'file-reference'; time.textContent = this.timestamp(file) ? new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'}).format(new Date(file.updatedAt)) : ''; updated.append(time);
      const action = document.createElement('a'); action.className = file.status === 'awaiting_company' ? 'button compact' : 'file-next-action'; action.href = file.countersignUrl || file.detailUrl;
      action.textContent = ({draft:'Continue setup',issuing:'Check request',awaiting_client:'View request',awaiting_company:'Sign for company',finalizing:file.needsAttention?'Resolve issue':'Finish processing',completed:'View signed file',cancelled:'Prepare replacement',expired:'Prepare replacement'})[file.status] || 'Open file';
      action.setAttribute('aria-label', `${action.textContent} · ${file.clientName || 'Client'}`); cell('Next step').append(action); fragment.append(row);
    }
    container.replaceChildren(fragment);
  }

  showError(title, message) {
    this.error.querySelector('strong').textContent = title;
    document.querySelector('#workspace-error-message').textContent = message;
    this.error.hidden = false;
  }

  async refreshWorkspace() {
    window.clearTimeout(this.searchTimer);
    this.requestController?.abort();
    const controller = new AbortController();
    this.requestController = controller;
    const requestNumber = ++this.requestNumber;
    this.error.hidden = true;
    document.body.classList.add('is-loading');
    document.querySelectorAll('.table-container').forEach(table => table.setAttribute('aria-busy', 'true'));
    const retry = document.querySelector('#retry-session');
    retry.disabled = true;
    this.previousPage.disabled = true;
    this.nextPage.disabled = true;
    document.querySelector('#file-results-summary').textContent = 'Updating client files…';
    try {
      const url = new URL(document.body.dataset.filesUrl, window.location.origin);
      url.search = new URLSearchParams({ q: this.search.value.trim(), filter: this.filter, sort: this.sort.value, page: this.page, workspace_id: document.body.dataset.companyId });
      const data = await this.client.request(url, undefined, { signal: controller.signal });
      if (requestNumber === this.requestNumber) this.applySnapshot(data);
    } catch (error) {
      if (error.name === 'AbortError' || requestNumber !== this.requestNumber) return;
      if (error.status === 401) return window.location.replace(document.body.dataset.loginUrl);
      if (error.code === 'workspace_changed') return window.location.reload();
      this.showError('We couldn’t load client files.', error.message);
      document.querySelector('#file-results-summary').textContent = 'Files could not be updated. Try again to load the current results.';
    } finally {
      if (requestNumber === this.requestNumber) {
        this.requestController = null;
        retry.disabled = false;
        document.body.classList.remove('is-loading');
        document.querySelectorAll('.table-container').forEach(table => table.setAttribute('aria-busy', 'false'));
      }
    }
  }

  async logout() {
    const button = document.querySelector('.logout-button');
    if (button.disabled) return;
    if (!this.agreements.confirmLeave()) return;
    this.agreements.fieldsDirty = false;
    this.agreements.dispose();
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.querySelector('span').textContent = 'Logging out…';
    try {
      await this.client.request(document.body.dataset.logoutUrl, {});
      window.location.replace(document.body.dataset.loginUrl);
    } catch (error) {
      if (error.status === 401) return window.location.replace(document.body.dataset.loginUrl);
      this.account.open = false;
      this.showError('We couldn’t log you out.', error.message + ' Open your account menu to try logging out again.');
      this.agreements.navigate(window.location.hash.slice(1));
      this.error.scrollIntoView({ block: 'nearest' });
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.querySelector('span').textContent = 'Log out';
    }
  }
}

new WorkspaceDashboard(JSON.parse(document.querySelector('#workspace-data').textContent));
