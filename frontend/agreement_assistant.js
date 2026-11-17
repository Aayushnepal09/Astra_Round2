export class DocumentAssistant {
  constructor(review) {
    this.review = review;
    this.root = document.querySelector('#agreement-assistant');
    if (!this.root) return;
    const mobile = window.matchMedia?.('(max-width: 1399px)');
    if (mobile?.matches) this.root.open = false;
    mobile?.addEventListener?.('change', event => { this.root.open = !event.matches; });
    this.endpoint = document.body.dataset.assistantUrl;
    this.form = document.querySelector('#assistant-form');
    this.question = document.querySelector('#assistant-question');
    this.summary = document.querySelector('#assistant-summary');
    this.ask = document.querySelector('#assistant-ask');
    this.status = document.querySelector('#assistant-status');
    this.error = document.querySelector('#assistant-error');
    this.answer = document.querySelector('#assistant-answer');
    this.recheck = document.querySelector('#assistant-recheck');
    this.form.addEventListener('submit', event => { event.preventDefault(); this.send('question'); });
    this.summary.addEventListener('click', () => this.send('summary'));
    this.recheck.addEventListener('click', () => this.check());
    this.root.addEventListener('toggle', () => {
      if (this.root.open) this.check();
      else this.controller?.abort();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.clear();
      else if (this.root.open) this.check();
    });
    this.check();
  }

  locked() {
    return this.review.signing.expired || this.review.signing.uncertain || !!this.review.signing.state.receipt;
  }

  update() {
    if (!this.root) return;
    const locked = this.locked();
    const coolingDown = Date.now() < (this.cooldownUntil || 0);
    this.question.disabled = this.summary.disabled = this.ask.disabled = locked || this.busy || !this.available || coolingDown;
    this.recheck.disabled = !!this.busy || locked || coolingDown;
    this.root.setAttribute('aria-busy', String(!!this.busy));
    if (locked) {
      this.clear();
      this.status.textContent = 'Assistance is unavailable while access or submission is unresolved, and after your signature is recorded. Document access remains separate.';
    }
  }

  clear() {
    this.controller?.abort();
    this.answer?.replaceChildren();
    if (this.answer) this.answer.hidden = true;
  }

  async request(payload) {
    const response = await fetch(this.endpoint, {
      method: payload ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      signal: this.controller.signal,
      headers: payload ? { 'Content-Type': 'application/json', 'X-CSRFToken': this.review.client.token.content } : {},
      ...(payload ? { body: JSON.stringify(payload) } : {}),
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error(response.status === 403 ? 'Your session changed. Use Check submission status to refresh access, then try again.' : 'The assistant could not complete this request. You can still review and sign.'); }
    if (!response.ok) {
      const error = new Error(result.error || 'The assistant is unavailable. You can still review and sign.');
      error.status = response.status;
      const retry = result.retryAfter || Number(response.headers.get('Retry-After'));
      if (Number.isFinite(retry) && retry > 0 && retry <= 86400) error.retryAfter = retry;
      throw error;
    }
    if (result.csrfToken) this.review.client.token.content = result.csrfToken;
    return result;
  }

  async check() {
    if (!this.root || !this.root.open || this.busy || this.locked() || document.hidden) { this.update(); return; }
    this.busy = true;
    this.controller = new AbortController();
    this.error.hidden = true;
    this.update();
    try {
      const result = await this.request();
      this.available = result.available;
      this.recheck.hidden = result.available;
      this.question.maxLength = result.questionMaxChars;
      this.status.textContent = result.available ? 'Ask a question or request a cited summary. Review and signing work independently.' : 'The assistant is not configured. You can still review and sign.';
    } catch (error) {
      this.available = false;
      if (error.name !== 'AbortError') { this.error.textContent = error.message; this.error.hidden = false; }
    } finally { this.busy = false; this.update(); }
  }

  async send(action) {
    if (this.busy || this.locked() || !this.available || Date.now() < (this.cooldownUntil || 0)) return;
    if (action === 'question' && !this.form.reportValidity()) return;
    this.clear();
    this.controller = new AbortController();
    this.busy = true;
    this.error.hidden = true;
    this.status.textContent = 'Reading the issued agreement and checking supporting passages… You can continue reviewing or signing.';
    this.update();
    try {
      const result = await this.request({ action, ...(action === 'question' ? { question: this.question.value.trim() } : {}) });
      if (document.hidden || this.locked()) return;
      this.render(result.answer);
      this.status.textContent = 'Check the cited pages before relying on an explanation.';
    } catch (error) {
      if (error.name !== 'AbortError') {
        this.error.textContent = error.message + (error.retryAfter ? ` Try again in ${error.retryAfter} seconds.` : '');
        this.error.hidden = false;
        if (error.retryAfter) {
          this.cooldownUntil = Date.now() + error.retryAfter * 1000;
          clearTimeout(this.retryTimer);
          this.retryTimer = setTimeout(() => { this.update(); }, error.retryAfter * 1000);
        }
        this.status.textContent = 'No answer was generated. Review and signing remain available.';
        if ([401, 403, 410].includes(error.status)) this.available = false;
      }
    } finally { this.busy = false; this.update(); }
  }

  render(answer) {
    this.answer.replaceChildren();
    const heading = document.createElement('h3');
    heading.textContent = answer.category === 'ambiguous' ? 'Ambiguous wording'
      : answer.coverage?.summary ? answer.coverage.complete ? 'Agreement summary'
      : answer.coverage.selectionLimited ? 'Summary of selected readable passages' : 'Readable-text summary' : 'Agreement explanation';
    this.answer.append(heading);
    const paragraph = text => {
      const element = document.createElement('p');
      element.textContent = text;
      this.answer.append(element);
    };
    if (answer.message) paragraph(answer.message);
    if (answer.coverage?.limitedPages.length) paragraph(`Coverage limitation on pages ${answer.coverage.limitedPages.join(', ')}: images, scanned content, or other unreadable content have not been interpreted. Readable text is retained; this is not a complete agreement summary.`);
    if (answer.coverage?.selectionLimited && answer.claims.length) paragraph(`This answer uses selected readable passages from pages ${answer.coverage.selectedPages.join(', ')} of ${answer.coverage.pageCount}. Other text was outside this answer's allowance. It cannot establish that information is absent elsewhere in the agreement.`);
    for (const claim of answer.claims) {
      paragraph(claim.text);
      for (const citation of claim.citations) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = `${citation.label} · Page ${citation.page}`;
        const quote = document.createElement('blockquote');
        quote.textContent = citation.text;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button secondary';
        button.textContent = `Open page ${citation.page}`;
        button.addEventListener('click', () => {
          this.review.load(citation.page);
        });
        details.append(summary, quote, button);
        this.answer.append(details);
      }
    }
    this.answer.hidden = false;
    this.answer.focus({ preventScroll: true });
  }
}
