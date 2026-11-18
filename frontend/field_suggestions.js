export class FieldSuggestions {
  constructor(workspace) {
    this.workspace = workspace;
    this.button = document.querySelector('#agreement-suggest-fields');
    this.status = document.querySelector('#agreement-suggestion-status');
    this.list = document.querySelector('#agreement-suggestion-list');
    this.layer = document.querySelector('#agreement-suggestion-layer') || document.createElement('div');
    this.proposals = [];
    this.rows = new Map();
    this.accepted = new Map();
    this.addedOrder = new Map();
    this.pendingRemovals = new Map();
    this.orderCounter = 0;
    this.button.addEventListener('click', () => this.request());
    document.querySelector('#agreement-suggestions-dismiss').addEventListener('click', () => this.reset());
    document.querySelector('#agreement-suggestions-add-all')?.addEventListener('click', () => this.addAll());
  }

  reset() {
    this.controller?.abort();
    this.proposals = [];
    this.rows.clear();
    this.accepted.clear();
    this.addedOrder.clear();
    this.pendingRemovals.clear();
    this.orderCounter = 0;
    this.selected = null;
    this.busy = false;
    this.status.textContent = '';
    this.list.replaceChildren();
    this.layer.replaceChildren();
    this.workspace.viewer?.pages.forEach(page => page.proposals.replaceChildren());
    this.update();
  }

  update() {
    const w = this.workspace;
    this.button.disabled = this.busy || !w.agreement || w.publishedView || w.fieldsSaving || w.publishing || Date.now() < (this.retryAt || 0);
    this.button.textContent = this.busy ? 'Finding fillable fields…' : 'Find fields with AI';
    this.button.setAttribute('aria-busy', String(!!this.busy));
    document.querySelector('#agreement-suggestions-dismiss').hidden = !this.proposals.length;
    const addAll = document.querySelector('#agreement-suggestions-add-all');
    if (addAll) {
      const pending = this.proposals.filter(proposal => !this.matchingField(proposal)).length;
      addAll.hidden = pending < 2;
      addAll.textContent = `Add all ${pending} suggestions`;
      addAll.disabled = w.publishedView || w.fieldsSaving || w.publishing;
    }
    for (const proposal of this.proposals) {
      const row = this.rows.get(proposal);
      if (!row) continue;
      const existing = this.matchingField(proposal);
      const added = existing && this.accepted.get(proposal) === existing.id;
      const saved = existing && w.fieldsDirty === false && w.agreement.fields?.some(field => field.id === existing.id);
      row.add.textContent = existing ? 'Remove' : 'Add field';
      row.add.classList.toggle('danger', !!existing);
      row.add.classList.toggle('secondary', !!existing);
      row.row.dataset.added = String(!!existing);
      row.add.disabled = w.publishedView || w.fieldsSaving || w.publishing || (!existing && w.fields.length >= 200);
      row.confirm.hidden = !existing || this.pendingRemovals.get(proposal) !== existing.id;
      row.confirmButton.disabled = w.publishedView || w.fieldsSaving || w.publishing;
      if (existing && !this.addedOrder.has(proposal)) this.addedOrder.set(proposal, ++this.orderCounter);
      if (!existing) { this.addedOrder.delete(proposal); this.pendingRemovals.delete(proposal); }
      row.feedback.textContent = existing ? saved ? 'Saved in draft' : added ? 'Added to draft · save to keep it' : 'Already in draft'
        : w.fields.length >= 200 ? 'Draft full · 200 fields' : w.fieldsSaving || w.publishing ? 'Wait for the current save to finish' : 'Not added';
    }
    this.orderRows();
    if (this.accepted.size && w.fieldsDirty === false && [...this.accepted.values()].every(id => w.agreement.fields?.some(field => field.id === id))) {
      this.status.textContent = 'Added fields are saved in the field draft. Publish separately when your preparation is ready.';
    }
  }

  orderRows() {
    const waiting = this.proposals.filter(proposal => !this.matchingField(proposal));
    const added = this.proposals.filter(proposal => this.matchingField(proposal))
      .sort((a, b) => this.addedOrder.get(a) - this.addedOrder.get(b));
    const rows = [...waiting, ...added].map(proposal => this.rows.get(proposal)?.row).filter(Boolean);
    if (rows.some((row, index) => this.list.children[index] !== row)) this.list.append(...rows);
  }

  async request() {
    if (this.button.disabled) return;
    this.reset();
    const w = this.workspace;
    const agreement = w.agreement;
    const session = w.fieldSession;
    const revision = agreement.fieldsRevision;
    this.controller = new AbortController();
    this.busy = true;
    this.status.textContent = 'Reading possible field labels. You can continue placing fields manually.';
    this.update();
    try {
      const response = await fetch(w.url(`${agreement.id}/field-suggestions/`), {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: this.controller.signal,
        headers: { 'Content-Type': 'application/json', 'X-Workspace-ID': w.companyId,
          'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content },
        body: JSON.stringify({ pdfSha256: agreement.pdfSha256, revision }),
      });
      if (response.status === 403) throw await w.recoverPreparationSession();
      const result = await response.json();
      if (!response.ok) {
        const retry = result.retryAfter || Number(response.headers.get('Retry-After'));
        if (Number.isFinite(retry) && retry > 0 && retry <= 86400) {
          this.retryAt = Date.now() + retry * 1000;
          clearTimeout(this.retryTimer);
          this.retryTimer = setTimeout(() => this.update(), retry * 1000);
        }
        throw new Error(result.error || 'Suggestions are unavailable. Place fields manually or retry later.');
      }
      w.checkCompany(result);
      if (session !== w.fieldSession) return;
      if (w.agreement.id !== result.agreementId || w.agreement.pdfSha256 !== result.pdfSha256 || w.agreement.fieldsRevision !== result.revision) {
        throw new Error('The saved draft changed. Nothing was added; request suggestions again.');
      }
      this.proposals = result.fields;
      this.status.textContent = `${this.proposals.length ? `${this.proposals.length} ${this.proposals.length === 1 ? 'suggestion' : 'suggestions'}. ` : 'No fields suggested. '}${result.message}`
        + (result.coverage.limitedPages.length ? ` Images or unreadable content on pages ${result.coverage.limitedPages.join(', ')} were not interpreted.` : '')
        + (result.coverage.selectionLimited ? ` AI reviewed a selection of field anchors on pages ${result.coverage.selectedPages.join(', ')}.` : '');
      this.render();
      (this.list.children[0] || this.status).scrollIntoView({ block: 'nearest' });
    } catch (error) {
      if (session === w.fieldSession && error.name !== 'AbortError') {
        this.status.textContent = error.message + ' Your existing fields are unchanged.';
        this.status.scrollIntoView({ block: 'nearest' });
      }
    } finally {
      if (session === w.fieldSession) { this.busy = false; this.update(); }
    }
  }

  render() {
    this.list.replaceChildren();
    this.rows.clear();
    for (const proposal of this.proposals) {
      const row = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = proposal.label;
      const detail = document.createElement('p');
      detail.textContent = `Page ${proposal.page} · ${proposal.type} · ${{client:'Client fills / signs',company:'Company fills / signs',preparation:'Company prepares before sending'}[proposal.role]} · ${proposal.required ? 'Required' : 'Optional'}`;
      const evidence = document.createElement('p');
      evidence.textContent = `Found near: ${proposal.evidence}`;
      const actions = document.createElement('div');
      actions.className = 'agreement-suggestion-actions';
      let add;
      for (const [label, act] of [[`Go to page ${proposal.page}`, () => this.preview(proposal)],
        ['Add field', () => this.matchingField(proposal) ? this.askRemove(proposal) : this.add(proposal)]]) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = label === 'Add field' ? 'button' : 'button secondary'; button.textContent = label;
        button.addEventListener('click', act); actions.append(button);
        if (label === 'Add field') add = button;
      }
      const feedback = document.createElement('p');
      feedback.className = 'agreement-suggestion-feedback';
      feedback.setAttribute('role', 'status');
      const confirm = document.createElement('div');
      confirm.className = 'agreement-suggestion-confirm'; confirm.hidden = true;
      const prompt = document.createElement('p');
      prompt.textContent = `Remove “${proposal.label}” from this draft?`;
      const confirmButton = document.createElement('button');
      confirmButton.type = 'button'; confirmButton.className = 'button danger'; confirmButton.textContent = 'Remove field';
      confirmButton.addEventListener('click', () => this.remove(proposal));
      const cancel = document.createElement('button');
      cancel.type = 'button'; cancel.className = 'button secondary'; cancel.textContent = 'Keep field';
      cancel.addEventListener('click', () => { this.pendingRemovals.delete(proposal); this.update(); add.focus({ preventScroll: true }); });
      confirm.append(prompt, confirmButton, cancel);
      row.append(title, detail, evidence, actions, feedback, confirm); this.list.append(row);
      this.rows.set(proposal, { row, add, feedback, confirm, confirmButton });
    }
    this.renderPreview(); this.update();
  }

  preview(proposal) {
    const existing = this.matchingField(proposal);
    if (existing) { this.selected = null; this.reveal(existing); this.renderPreview(); return; }
    this.selected = proposal;
    this.workspace.loadPage(proposal.page);
    this.renderPreview();
  }

  matchingField(proposal) {
    const acceptedId = this.accepted.get(proposal);
    const accepted = acceptedId && this.workspace.fields.find(field => field.id === acceptedId);
    if (accepted) return accepted;
    return this.workspace.fields.find(field => field.page === proposal.page && field.type === proposal.type
      && field.role === proposal.role && Math.abs(field.x-proposal.x) < .025 && Math.abs(field.y-proposal.y) < .025
      && Math.abs(field.width-proposal.width) < .025 && Math.abs(field.height-proposal.height) < .025);
  }

  askRemove(proposal) {
    const w = this.workspace, field = this.matchingField(proposal);
    if (!field || !this.proposals.includes(proposal) || w.publishedView || w.fieldsSaving || w.publishing) return;
    this.pendingRemovals.set(proposal, field.id);
    this.update();
    this.rows.get(proposal)?.confirmButton.focus({ preventScroll: true });
  }

  remove(proposal) {
    const w = this.workspace, field = this.matchingField(proposal);
    if (!field || !this.proposals.includes(proposal) || this.pendingRemovals.get(proposal) !== field.id
        || w.publishedView || w.fieldsSaving || w.publishing) return;
    w.fields = w.fields.filter(item => item.id !== field.id);
    if (w.selectedFieldId === field.id) w.selectedFieldId = null;
    this.accepted.delete(proposal); this.pendingRemovals.delete(proposal); this.addedOrder.delete(proposal);
    this.selected = null;
    w.markFieldsDirty('Field removed from the preparation draft. Save field draft to keep this change.');
    this.status.textContent = `Removed “${field.label}” from this draft. Save field draft to keep the change.`;
    this.update(); this.renderPreview(); w.renderFields();
    this.rows.get(proposal)?.add.focus({ preventScroll: true });
  }

  reveal(field) {
    const w = this.workspace;
    if (w.revealField) w.revealField(field);
    else { w.loadPage(field.page); w.renderFields(); }
  }

  renderPreview() {
    this.layer.replaceChildren();
    this.workspace.viewer?.pages.forEach(page => page.proposals.replaceChildren());
    const field = this.selected;
    if (!field) return;
    const marker = document.createElement('span');
    marker.className = 'agreement-proposed-field'; marker.textContent = `Suggested: ${field.label}`;
    Object.assign(marker.style, { left: `${field.x*100}%`, top: `${field.y*100}%`, width: `${field.width*100}%`, height: `${field.height*100}%` });
    const layer = this.workspace.viewer?.pages.get(field.page)?.proposals || this.layer;
    layer.append(marker);
  }

  // One click for the common case; every added box stays editable and removable before saving.
  addAll() {
    const w = this.workspace;
    if (w.publishedView || w.fieldsSaving || w.publishing) return;
    const pending = this.proposals.filter(proposal => !this.matchingField(proposal)).slice(0, Math.max(0, 200 - w.fields.length));
    if (!pending.length) return;
    for (const proposal of pending) {
      const { evidence, ...field } = proposal;
      field.id = crypto.randomUUID();
      w.fields.push(field);
      this.accepted.set(proposal, field.id);
    }
    w.markFieldsDirty(`${pending.length} suggested fields added. Check each role and position, then save.`);
    this.selected = null;
    this.status.textContent = `Added ${pending.length} suggested fields. Check each box's position and who completes it, then save.`;
    this.renderPreview(); this.update();
  }

  add(proposal) {
    const w = this.workspace;
    if (w.publishedView || w.fieldsSaving || w.publishing || !this.proposals.includes(proposal)) return;
    if (w.fields.length >= 200) { this.status.textContent = 'The draft already has 200 fields. Remove one before adding another.'; return; }
    const duplicate = this.matchingField(proposal);
    if (duplicate) {
      w.selectedFieldId = duplicate.id;
      this.reveal(duplicate);
      this.status.textContent = 'This field is already in the draft. Its position is selected on the document.';
      this.update();
      return;
    }
    const { evidence, ...field } = proposal;
    field.id = crypto.randomUUID();
    w.fields.push(field); w.selectedFieldId = field.id;
    w.markFieldsDirty('Suggested field added to your local draft. Review its position and signer, then save.');
    this.accepted.set(proposal, field.id);
    this.selected = null;
    const remaining = this.proposals.filter(item => !this.matchingField(item)).length;
    this.status.textContent = `Added “${field.label}” on page ${field.page}. Save field draft to keep it. ${remaining} suggestions remain to add.`;
    this.renderPreview(); this.update();
    this.reveal(field);
  }
}
