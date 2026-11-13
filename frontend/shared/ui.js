export class SessionError extends Error {
  constructor(message, status = 0, fieldErrors = {}, code = null) {
    super(message);
    this.name = 'SessionError';
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.code = code;
  }
}

export class SessionClient {
  constructor(token = document.querySelector('meta[name="csrf-token"]')) {
    this.token = token;
  }

  async request(url, data, { signal } = {}) {
    const endpoint = new URL(url, window.location.origin);
    if (endpoint.origin !== window.location.origin) throw new SessionError('This action must stay within your workspace.');
    let response;
    try {
      response = await fetch(endpoint, {
        method: data === undefined ? 'GET' : 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
        headers: data === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRFToken': this.token.content },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new SessionError('Could not reach the server. Check your connection and try again.');
    }
    if (response.status === 403) throw new SessionError('Your session has changed. Reload this page and try again.', 403);
    let result;
    try {
      result = await response.json();
    } catch {
      throw new SessionError('The server could not complete this action. Please try again.', response.status);
    }
    if (!response.ok) throw new SessionError(result.error || 'Unable to complete this action. Please try again.', response.status, result.fieldErrors || {}, result.code || null);
    if (result.csrfToken) this.token.content = result.csrfToken;
    return result;
  }
}
