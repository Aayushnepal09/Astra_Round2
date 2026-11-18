// A continuous private document with bounded image loading and stable page sizes.
export class ScrollDocument {
  constructor({ root, count, geometry = [], url, onPage = () => {}, onLoad = () => {}, onError = () => {} }) {
    this.root = root;
    this.url = url;
    this.onPage = onPage;
    this.onLoad = onLoad;
    this.onError = onError;
    this.pages = new Map();
    this.queue = [];
    this.running = 0;
    this.current = 1;
    root.replaceChildren();
    for (let number = 1; number <= count; number++) {
      const section = document.createElement('section');
      section.className = 'document-page';
      section.dataset.page = number;
      section.setAttribute('aria-label', `Page ${number} of ${count}`);
      const heading = document.createElement('p');
      heading.className = 'document-page-label';
      heading.textContent = `Page ${number} of ${count}`;
      const canvas = document.createElement('div');
      canvas.className = 'document-page-canvas';
      const size = geometry[number - 1] || { width: 612, height: 792 };
      const rotated = size.rotation === 90 || size.rotation === 270;
      canvas.style.aspectRatio = rotated ? `${size.height} / ${size.width}` : `${size.width} / ${size.height}`;
      const image = document.createElement('img');
      image.alt = `Agreement page ${number} of ${count}`;
      image.draggable = false;
      image.hidden = true;
      const status = document.createElement('p');
      status.className = 'document-page-loading';
      status.textContent = 'Page loads as you scroll';
      const retry = document.createElement('button');
      retry.type = 'button'; retry.className = 'button secondary document-page-retry';
      retry.textContent = `Retry page ${number}`; retry.hidden = true;
      retry.addEventListener('click', () => this.ensure(number, true));
      const fields = document.createElement('div');
      fields.className = 'document-page-fields';
      const proposals = document.createElement('div');
      proposals.className = 'document-page-proposals';
      proposals.setAttribute('aria-hidden', 'true');
      canvas.append(image, status, retry, fields, proposals);
      section.append(heading, canvas); root.append(section);
      this.pages.set(number, { section, canvas, image, status, retry, fields, proposals });
    }
    this.observer = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) this.ensure(Number(entry.target.dataset.page));
    }, { rootMargin: '500px 0px' });
    this.pages.forEach(page => this.observer.observe(page.section));
    this.scroll = () => {
      if (this.frame || this.destroyed) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.activate(this.visiblePage());
      });
    };
    window.addEventListener('scroll', this.scroll, { passive: true });
    window.addEventListener('resize', this.scroll);
    this.ensure(1);
  }

  activate(number) {
    if (this.current === number) return;
    this.current = number;
    this.onPage(number);
    // Keep only nearby decoded images. Page placeholders and field state remain.
    for (const [index, page] of this.pages) if (Math.abs(index - number) > 2) this.release(page);
    this.queue = this.queue.filter(index => Math.abs(index - number) <= 2);
  }

  visiblePage() {
    const line = innerHeight / 2;
    const pages = [...this.pages].map(([number, page]) => [number, page.canvas.getBoundingClientRect()]);
    const containing = pages.find(([, box]) => box.top <= line && box.bottom > line);
    if (containing) return containing[0];
    const distance = box => Math.min(Math.abs(box.top-line), Math.abs(box.bottom-line));
    return pages.reduce((best, page) => distance(page[1]) < distance(best[1]) ? page : best)[0];
  }

  revealField(field) {
    const page = this.pages.get(field.page);
    if (!page || this.destroyed) return;
    this.activate(field.page);
    this.ensure(field.page, true);
    this.fieldTarget?.remove();
    const target = document.createElement('span');
    target.className = 'document-field-target';
    target.setAttribute('aria-hidden', 'true');
    Object.assign(target.style, { position: 'absolute', pointerEvents: 'none',
      left: `${field.x*100}%`, top: `${field.y*100}%`, width: `${field.width*100}%`, height: `${field.height*100}%` });
    page.canvas.append(target);
    this.fieldTarget = target;
    target.scrollIntoView({ block: 'center', inline: 'nearest' });
  }

  goTo(number) {
    const page = this.pages.get(number);
    if (!page || this.destroyed) return;
    this.activate(number);
    page.section.scrollIntoView({ block: 'start' });
    this.ensure(number, true);
  }

  ensure(number, retry = false) {
    const page = this.pages.get(number);
    if (!page || this.destroyed || page.objectUrl || page.controller || this.queue.includes(number) || (page.failed && !retry)) return;
    page.failed = false;
    retry ? this.queue.unshift(number) : this.queue.push(number);
    this.pump();
  }

  pump() {
    while (!this.destroyed && this.running < 2 && this.queue.length) this.load(this.queue.shift());
  }

  async load(number) {
    const page = this.pages.get(number);
    const controller = new AbortController();
    page.controller = controller; this.running++;
    page.status.hidden = false; page.status.textContent = 'Loading page…'; page.retry.hidden = true;
    try {
      const response = await fetch(this.url(number), { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      if (!response.ok) {
        let result = {};
        try { result = await response.json(); } catch { /* A proxy can return HTML. */ }
        const error = new Error(result.error || 'This page could not be loaded.');
        error.status = response.status; error.code = result.code; throw error;
      }
      const blob = await response.blob();
      if (blob.type !== 'image/png') throw new Error('The server did not return a document page.');
      if (controller.signal.aborted || this.destroyed) return;
      page.objectUrl = URL.createObjectURL(blob);
      page.image.src = page.objectUrl;
      await page.image.decode();
      if (controller.signal.aborted || this.destroyed) return;
      page.image.hidden = false; page.status.hidden = true;
      this.onLoad(number);
    } catch (error) {
      if (error.name !== 'AbortError' && !controller.signal.aborted && !this.destroyed) {
        this.release(page, false); page.failed = true;
        page.status.textContent = error.message; page.status.hidden = false; page.retry.hidden = false;
        this.onError(error, number);
      }
    } finally {
      if (page.controller === controller) page.controller = null;
      this.running--; this.pump();
      if (controller.signal.aborted && !this.destroyed && Math.abs(number - this.current) <= 1) this.ensure(number);
    }
  }

  release(page, abort = true) {
    if (abort) page.controller?.abort();
    page.image.hidden = true; page.image.removeAttribute('src');
    if (page.objectUrl) URL.revokeObjectURL(page.objectUrl);
    page.objectUrl = null;
    page.status.hidden = false;
    if (!page.failed) page.status.textContent = 'Page loads as you scroll';
  }

  reload() {
    this.pages.forEach(page => { this.release(page); page.failed = false; });
    this.ensure(this.current, true);
  }

  destroy() {
    this.destroyed = true; this.observer.disconnect(); this.queue = [];
    window.removeEventListener('scroll', this.scroll);
    window.removeEventListener('resize', this.scroll);
    if (this.frame) cancelAnimationFrame(this.frame);
    this.pages.forEach(page => this.release(page));
    this.root.replaceChildren();
  }
}
