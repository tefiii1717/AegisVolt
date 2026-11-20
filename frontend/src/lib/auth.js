/**
 * Autenticación OAuth2.
 *  - dev: el BFF emite un token firmado para el rol elegido (solo local/dev/test).
 *  - cognito: Authorization Code + PKCE contra el Hosted UI de Amazon Cognito (sin client secret en el navegador).
 * El token vive en sessionStorage (se borra al cerrar la pestaña) y nunca en la URL.
 */
const KEY = 'aegisvolt.session';
const cfg = () => window.__AEGIS_CONFIG__ ?? {};
const listeners = new Set();

function safeStorage(fn, fallback = null) {
  try {
    return fn(window.sessionStorage);
  } catch {
    return fallback;
  }
}

export function getSession() {
  const raw = safeStorage((s) => s.getItem(KEY));
  if (!raw) return null;
  try {
    const session = JSON.parse(raw);
    return session.expiresAt > Date.now() ? session : null;
  } catch {
    return null;
  }
}

export const getToken = () => getSession()?.token ?? null;

function save(session) {
  safeStorage((s) => s.setItem(KEY, JSON.stringify(session)));
  listeners.forEach((l) => l(session));
}

export function clearSession() {
  safeStorage((s) => s.removeItem(KEY));
  listeners.forEach((l) => l(null));
}

export function onSessionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function loginDev(role) {
  const res = await fetch(cfg().devTokenUrl, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ role }),
  });
  if (!res.ok) throw new Error('No se pudo emitir el token de desarrollo.');
  const body = await res.json();
  save({ token: body.access_token, role, scopes: body.scope.split(' '), expiresAt: Date.now() + body.expires_in * 1000 });
}

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

export async function loginCognito() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  safeStorage((s) => s.setItem('aegisvolt.pkce', JSON.stringify({ verifier, state })));
  const c = cfg();
  const params = new URLSearchParams({
    response_type: 'code', client_id: c.cognitoClientId, redirect_uri: c.redirectUri, state,
    code_challenge: challenge, code_challenge_method: 'S256',
    scope: 'openid aegisvolt/stations.read aegisvolt/transactions.read aegisvolt/reservations.write aegisvolt/flags.admin',
  });
  window.location.assign(`${c.cognitoDomain}/oauth2/authorize?${params}`);
}

export async function completeCognitoLogin() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  if (!code) return false;
  const pkce = JSON.parse(safeStorage((s) => s.getItem('aegisvolt.pkce')) ?? '{}');
  window.history.replaceState({}, '', window.location.pathname); // el code no queda en el historial
  if (!pkce.state || pkce.state !== params.get('state')) throw new Error('Estado OAuth inválido.');
  const c = cfg();
  const res = await fetch(`${c.cognitoDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: c.cognitoClientId, code,
      redirect_uri: c.redirectUri, code_verifier: pkce.verifier }),
  });
  if (!res.ok) throw new Error('Cognito rechazó el código de autorización.');
  const body = await res.json();
  const claims = JSON.parse(atob(body.access_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
  save({ token: body.access_token, role: claims['cognito:groups']?.[0] ?? 'fleet-operator',
    scopes: String(claims.scope ?? '').split(' '), expiresAt: Date.now() + body.expires_in * 1000 });
  return true;
}
