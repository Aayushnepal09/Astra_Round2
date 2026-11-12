const form = document.querySelector('#access-form');
const isClient = new URLSearchParams(window.location.search).get('role') === 'client';
const fields = ['email', 'password', 'code'];
const status = document.querySelector('#form-status');
const password = document.querySelector('#password');
const passwordToggle = document.querySelector('.password-toggle');

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
  document.querySelector('#preview-note').textContent = 'UI preview · Code verification is not connected yet.';
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

form.addEventListener('submit', event => {
  event.preventDefault();
  let firstInvalid;
  fields.forEach(name => {
    const input = document.querySelector(`#${name}`);
    if (input.disabled) return;
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
  status.textContent = isClient ? 'Code verification is not connected in this UI preview. Your code has not been submitted.' : 'Sign-in is not connected in this UI preview. Your details have not been submitted.';
  status.hidden = false;
});
