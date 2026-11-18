import { SessionClient } from './shared/ui.js';

class LoginController {
  constructor(form) {
    this.form = form;
    this.client = new SessionClient();
    this.isClient = document.body.dataset.role === 'client';
    // A code only works with its invitation link; the bare client login page cannot verify one.
    this.noInvitation = this.isClient && !/\/client\/login\/[0-9a-f-]{36}\/?$/i.test(window.location.pathname);
    this.authenticated = document.body.dataset.authenticated === 'true';
    this.fields = ['email', 'password', 'code'].map(name => document.getElementById(name));
    this.status = document.querySelector('#form-status');
    this.button = form.querySelector('.submit-button');
    this.label = document.querySelector('#submit-label');
    this.password = document.querySelector('#password');
    this.toggle = document.querySelector('.password-toggle');
    this.configure();
    form.addEventListener('input', event => {
      if (this.fields.includes(event.target)) this.clearError(event.target);
      this.status.hidden = true;
      this.status.classList.remove('is-error');
    });
    form.addEventListener('submit', event => this.submit(event));
    this.toggle.addEventListener('click', () => this.setPasswordVisible(this.password.type === 'password'));
    if (this.authenticated) this.showSignedIn();
    else if (this.noInvitation) this.showLinkNeeded();
  }

  configure() {
    for (const input of this.fields) {
      const active = this.isClient === (input.id === 'code');
      document.getElementById(`${input.id}-field`).hidden = !active;
      input.disabled = !active;
      input.required = active;
    }
    if (!this.isClient) return;
    document.querySelector('#form-heading').textContent = 'Hello, let’s begin.';
    document.querySelector('#form-description').textContent = 'Enter your code to access your agreement.';
    this.label.textContent = 'Continue to agreement';
    document.querySelector('#access-note').textContent = 'No account needed. Just your invitation code.';
    document.querySelector('summary').textContent = 'Where can I find my code?';
    document.querySelector('#help-copy').textContent = 'Use the access code supplied with your signing invitation. If it is missing or expired, contact the company that invited you.';
    document.title = 'Client access · Astra';
  }

  showLinkNeeded() {
    document.querySelector('#code-field').hidden = true;
    document.querySelector('#code').disabled = true;
    this.button.hidden = true;
    document.querySelector('#form-heading').textContent = 'Open your invitation link';
    document.querySelector('#form-description').textContent = 'Your access code works together with the signing link the company sent you. Open that link, then enter your code.';
    document.querySelector('#access-help').open = true;
    document.querySelector('summary').textContent = 'Can’t find the link?';
    document.querySelector('#help-copy').textContent = 'Contact the company that invited you. They can share the link and code again or send a new one.';
    document.querySelector('#access-note').textContent = 'No account needed. Just your invitation link and code.';
  }

  showSignedIn() {
    if (!this.isClient && document.body.dataset.dashboardUrl) {
      window.location.replace(document.body.dataset.dashboardUrl);
      return;
    }
    this.authenticated = true;
    for (const input of this.fields) document.getElementById(`${input.id}-field`).hidden = true;
    document.querySelector('.help-row').hidden = true;
    document.querySelector('#form-heading').textContent = 'You’re logged in.';
    document.querySelector('#form-description').textContent = this.isClient
      ? 'To open an agreement, use the signing link from your invitation.'
      : 'Your company workspace is ready.';
    document.querySelector('#access-note').textContent = 'You can safely log out here.';
    this.label.textContent = 'Log out';
  }

  setPasswordVisible(visible) {
    this.password.type = visible ? 'text' : 'password';
    this.toggle.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
    this.toggle.setAttribute('aria-pressed', String(visible));
    document.querySelector('.eye-slash').toggleAttribute('hidden', !visible);
  }

  clearError(input) {
    input.removeAttribute('aria-invalid');
    document.getElementById(`${input.id}-error`).hidden = true;
  }

  validate() {
    let firstInvalid;
    for (const input of this.fields) {
      if (this.authenticated || input.disabled) continue;
      this.clearError(input);
      const missing = input.id === 'password' ? !input.value : !input.value.trim();
      if (!missing && input.validity.valid) continue;
      const error = document.getElementById(`${input.id}-error`);
      error.textContent = input.id === 'email' ? 'Enter a valid work email address.' : input.id === 'password' ? 'Enter your password.' : 'Enter the access code from your invitation.';
      error.hidden = false;
      input.setAttribute('aria-invalid', 'true');
      firstInvalid ??= input;
    }
    firstInvalid?.focus();
    return !firstInvalid;
  }

  async submit(event) {
    event.preventDefault();
    if (this.button.disabled) return;
    this.status.hidden = true;
    if (!this.validate()) return;
    const signingOut = this.authenticated;
    this.button.disabled = true;
    this.button.setAttribute('aria-busy', 'true');
    this.label.textContent = signingOut ? 'Logging out…' : this.isClient ? 'Verifying…' : 'Logging in…';
    try {
      const data = signingOut ? {} : this.isClient
        ? { code: document.querySelector('#code').value.trim() }
        : { email: document.querySelector('#email').value.trim(), password: this.password.value };
      const result = await this.client.request(signingOut ? document.body.dataset.logoutUrl : document.body.dataset.loginUrl, data);
      if (!result.authenticated) return window.location.reload();
      if (result.dashboardUrl) return window.location.replace(result.dashboardUrl);
      this.form.reset();
      this.setPasswordVisible(false);
      this.showSignedIn();
    } catch (error) {
      if (signingOut && error.status === 401) return window.location.reload();
      this.status.textContent = error.message;
      this.status.classList.add('is-error');
      this.status.hidden = false;
      if (this.isClient) {
        const code = document.querySelector('#code');
        code.setAttribute('aria-invalid', 'true');
        code.select();
      }
    } finally {
      this.button.disabled = false;
      this.button.removeAttribute('aria-busy');
      this.label.textContent = this.authenticated ? 'Log out' : this.isClient ? 'Continue to agreement' : 'Log in';
    }
  }
}

new LoginController(document.querySelector('#access-form'));
