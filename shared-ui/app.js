const form = document.querySelector('#access-form');
const isClient = document.body.dataset.role === 'client';
const fields = ['email', 'password', 'code'];
const status = document.querySelector('#form-status');
const password = document.querySelector('#password');
const passwordToggle = document.querySelector('.password-toggle');
const submitButton = document.querySelector('.submit-button');
const submitLabel = document.querySelector('#submit-label');
const csrfToken = document.querySelector('meta[name="csrf-token"]');
let authenticated = document.body.dataset.authenticated === 'true';

function showSignedIn() {
  authenticated = true;
  fields.forEach(name => { document.querySelector(`#${name}-field`).hidden = true; });
  document.querySelector('.help-row').hidden = true;
  document.querySelector('#form-heading').textContent = 'You’re logged in.';
  document.querySelector('#form-description').textContent = `Your ${isClient ? 'client' : 'company'} dashboard will be added next.`;
  document.querySelector('#access-note').textContent = 'You can safely log out here.';
  submitLabel.textContent = 'Log out';
}

function clearError(input) {
  input.removeAttribute('aria-invalid');
  document.querySelector(`#${input.id}-error`).hidden = true;
}

fields.forEach(name => {
  const input = document.querySelector(`#${name}`);
  const active = isClient === (name === 'code');
  document.querySelector(`#${name}-field`).hidden = !active;
  input.disabled = !active;
  input.required = active;
});

if (isClient) {
  document.querySelector('#form-heading').textContent = 'Hello, let’s begin.';
  document.querySelector('#form-description').textContent = 'Enter your code to access your agreement.';
  document.querySelector('#submit-label').textContent = 'Continue to agreement';
  document.querySelector('#access-note').textContent = 'No account needed. Just your invitation code.';
  document.querySelector('summary').textContent = 'Where can I find my code?';
  document.querySelector('#help-copy').textContent = 'Use the access code supplied with your signing invitation. If it is missing or expired, contact the company that invited you.';
  document.title = 'Client access · Astra';
}

passwordToggle.addEventListener('click', () => {
  const show = password.type === 'password';
  password.type = show ? 'text' : 'password';
  passwordToggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  passwordToggle.setAttribute('aria-pressed', String(show));
  document.querySelector('.eye-slash').toggleAttribute('hidden', !show);
});

form.addEventListener('input', event => {
  if (fields.includes(event.target.id)) clearError(event.target);
  status.hidden = true;
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  status.hidden = true;
  let firstInvalid;
  fields.forEach(name => {
    const input = document.querySelector(`#${name}`);
    if (authenticated || input.disabled) return;
    clearError(input);
    const missing = name === 'password' ? !input.value : !input.value.trim();
    if (missing || !input.validity.valid) {
      const error = document.querySelector(`#${name}-error`);
      error.textContent = name === 'email' ? 'Enter a valid work email address.' : name === 'password' ? 'Enter your password.' : 'Enter the access code from your invitation.';
      error.hidden = false;
      input.setAttribute('aria-invalid', 'true');
      firstInvalid ??= input;
    }
  });
  if (firstInvalid) return firstInvalid.focus();
  const url = authenticated ? document.body.dataset.logoutUrl : document.body.dataset.loginUrl;
  submitButton.disabled = true;
  submitLabel.textContent = authenticated ? 'Logging out…' : isClient ? 'Verifying…' : 'Logging in…';
  try {
    const data = authenticated ? {} : isClient
      ? { code: document.querySelector('#code').value.trim() }
      : { email: document.querySelector('#email').value.trim(), password: password.value };
    const response = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken.content },
      body: JSON.stringify(data),
    });
    if (response.status === 403) throw new Error('Your session has changed. Reload this page and try again.');
    const result = await response.json();
    if (!response.ok) {
      if (authenticated && response.status === 401) return window.location.reload();
      throw new Error(result.error || 'Unable to sign in. Please try again.');
    }
    csrfToken.content = result.csrfToken;
    if (!result.authenticated) return window.location.reload();
    form.reset();
    password.type = 'password';
    showSignedIn();
  } catch (error) {
    status.textContent = error instanceof TypeError ? 'Could not reach the server. Check your connection and try again.' : error.message;
    status.hidden = false;
  } finally {
    submitButton.disabled = false;
    submitLabel.textContent = authenticated ? 'Log out' : isClient ? 'Continue to agreement' : 'Log in';
  }
});

if (authenticated) showSignedIn();
