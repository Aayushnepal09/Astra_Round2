import { SessionClient } from './shared/ui.js';

class WorkspaceDashboard {
  static views = new Map([
    ['overview', { title: 'Your workspace, a little simpler.', description: 'A clear view of your client files, from first agreement to ready to start.', label: 'Overview' }],
    ['files', { title: 'Every client. Everything in view.', description: 'Keep each job, agreement and signing stage together.', label: 'Client files' }],
    ['templates', { title: 'Good agreements start here.', description: 'Your company’s documents, prepared for the next client.', label: 'Agreement templates' }],
    ['help', { title: 'Let’s make the next step simple.', description: 'A little help with your agreements and workspace.', label: 'Help & guidance' }],
  ]);

  static statuses = new Map([
    ['draft', { label: 'Draft · needs setup', tone: 'neutral' }],
    ['issuing', { label: 'Preparing request', tone: 'info' }],
    ['awaiting_client', { label: 'Awaiting client', tone: 'waiting' }],
    ['awaiting_company', { label: 'Awaiting company', tone: 'waiting' }],
    ['finalizing', { label: 'Finalizing', tone: 'info' }],
    ['completed', { label: 'Ready to start', tone: 'success' }],
    ['cancelled', { label: 'Cancelled', tone: 'neutral' }],
    ['expired', { label: 'Expired', tone: 'error' }],
  ]);

  constructor(data) {
    this.client = new SessionClient();
    this.files = new Map((data.files || []).map(file => [file.id, {
      ...file,
      searchText: [file.clientName, file.title, file.reference].filter(Boolean).join(' ').toLocaleLowerCase(),
    }]));
    this.filter = 'all';
    this.search = document.querySelector('#file-search');
    this.sort = document.querySelector('#file-sort');
    this.account = document.querySelector('.account-menu');
    this.menu = document.querySelector('.menu-toggle');
    this.mobile = window.matchMedia('(max-width: 800px)');
    this.error = document.querySelector('#workspace-error');
    this.pendingSession = false;
    document.body.classList.add('enhanced');
    this.setMenu(false);
    this.bindEvents();
    this.navigate(window.location.hash.slice(1), false, false);
    this.renderSummary();
    this.renderFiles();
    this.refreshSession();
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
      if (!event.target.closest('.account-menu')) this.account.open = false;
    });
    window.addEventListener('popstate', () => this.navigate(window.location.hash.slice(1), false));
    window.addEventListener('hashchange', () => this.navigate(window.location.hash.slice(1), false));
    window.addEventListener('pageshow', event => { if (event.persisted) this.refreshSession(); });
    this.search.addEventListener('input', () => this.renderFiles());
    this.sort.addEventListener('change', () => this.renderFiles());
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
    document.querySelector('#retry-session').addEventListener('click', () => this.refreshSession());
    document.querySelector('.logout-button').addEventListener('click', () => this.logout());
  }

  navigate(view, updateHistory = true, focus = true) {
    if (!WorkspaceDashboard.views.has(view)) view = 'overview';
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
    if (updateHistory && window.location.hash !== `#${view}`) window.history.pushState(null, '', `#${view}`);
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

  matchesFilter(file, filter) {
    if (filter === 'waiting') return file.status === 'awaiting_client' || file.status === 'awaiting_company';
    if (filter === 'ready') return file.status === 'completed';
    if (filter === 'attention') return file.needsAttention === true || file.status === 'draft' || file.status === 'expired';
    return true;
  }

  setFilter(filter) {
    this.filter = filter;
    document.querySelectorAll('[data-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === filter)));
    this.renderFiles();
  }

  renderSummary() {
    const counts = { all: this.files.size, waiting: 0, ready: 0, attention: 0 };
    for (const file of this.files.values()) {
      for (const filter of ['waiting', 'ready', 'attention']) {
        if (this.matchesFilter(file, filter)) counts[filter]++;
      }
    }
    document.querySelectorAll('[data-count]').forEach(element => { element.textContent = counts[element.dataset.count]; });
    const recent = [...this.files.values()].sort((first, second) => this.timestamp(second) - this.timestamp(first)).slice(0, 4);
    this.renderRows(document.querySelector('#recent-file-rows'), recent);
    document.querySelector('#recent-empty').hidden = recent.length > 0;
  }

  timestamp(file) {
    return Date.parse(file.updatedAt) || 0;
  }

  renderFiles() {
    const query = this.search.value.trim().toLocaleLowerCase();
    const filtered = [...this.files.values()].filter(file => this.matchesFilter(file, this.filter) && file.searchText.includes(query));
    filtered.sort((first, second) => this.sort.value === 'name'
      ? String(first.clientName || '').localeCompare(String(second.clientName || ''))
      : (this.timestamp(first) - this.timestamp(second)) * (this.sort.value === 'oldest' ? 1 : -1));
    this.renderRows(document.querySelector('#all-file-rows'), filtered);
    const noMatches = Boolean(query) || this.filter !== 'all';
    document.querySelector('#files-empty').hidden = filtered.length > 0;
    document.querySelector('#files-empty-title').textContent = noMatches ? 'No matching client files.' : 'Your next client starts here.';
    document.querySelector('#files-empty-copy').textContent = noMatches
      ? 'Try another client name or reference, or clear your filters to see every file.'
      : 'Once client files are created, you can search them and follow every signing stage here.';
    document.querySelector('#reset-filters').hidden = !noMatches;
    document.querySelector('#files-template-link').hidden = noMatches;
    document.querySelector('#workspace-status').textContent = `${filtered.length} client ${filtered.length === 1 ? 'file' : 'files'} shown.`;
  }

  renderRows(container, files) {
    const fragment = document.createDocumentFragment();
    for (const file of files) {
      const row = document.createElement('tr');
      const nameCell = row.insertCell();
      const name = document.createElement('strong');
      name.className = 'file-name';
      name.textContent = file.clientName || 'Client';
      const reference = document.createElement('span');
      reference.className = 'file-reference';
      reference.textContent = [file.reference, file.title].filter(Boolean).join(' · ');
      nameCell.append(name, reference);
      const state = WorkspaceDashboard.statuses.get(file.status) || { label: 'Status unavailable', tone: 'neutral' };
      const badge = document.createElement('span');
      badge.className = `status-badge ${file.needsAttention ? 'error' : state.tone}`;
      badge.textContent = file.needsAttention ? `${state.label} · needs attention` : state.label;
      row.insertCell().append(badge);
      const updated = row.insertCell();
      updated.textContent = this.timestamp(file) ? new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(file.updatedAt)) : '—';
      fragment.append(row);
    }
    container.replaceChildren(fragment);
  }

  showError(title, message) {
    this.error.querySelector('strong').textContent = title;
    document.querySelector('#workspace-error-message').textContent = message;
    this.error.hidden = false;
  }

  async refreshSession() {
    if (this.pendingSession) return;
    this.pendingSession = true;
    this.error.hidden = true;
    document.body.classList.add('is-loading');
    document.querySelectorAll('.table-container').forEach(table => table.setAttribute('aria-busy', 'true'));
    const retry = document.querySelector('#retry-session');
    retry.disabled = true;
    try {
      await this.client.request(document.body.dataset.sessionUrl);
      document.querySelector('#workspace-status').textContent = 'Your workspace is ready.';
    } catch (error) {
      if (error.status === 401) return window.location.replace(document.body.dataset.loginUrl);
      this.showError('We couldn’t refresh your workspace.', error.message);
    } finally {
      this.pendingSession = false;
      retry.disabled = false;
      document.body.classList.remove('is-loading');
      document.querySelectorAll('.table-container').forEach(table => table.setAttribute('aria-busy', 'false'));
    }
  }

  async logout() {
    const button = document.querySelector('.logout-button');
    if (button.disabled) return;
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
      this.error.scrollIntoView({ block: 'nearest' });
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.querySelector('span').textContent = 'Log out';
    }
  }
}

new WorkspaceDashboard(JSON.parse(document.querySelector('#workspace-data').textContent));
