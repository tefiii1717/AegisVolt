process.env.NODE_ENV = 'test';
process.env.AUTH_MODE = 'dev';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const { issueUserToken, verifyToken, requireScope, SCOPES, ROLES } = await import('../src/auth.js');
const { roleForEmail, hashPassword, verifyPassword, validatePassword } = await import('../src/identity.js');

const fakeUser = (role) => ({ user_id: `u-${role}`, email: `x@${role}`, name: 'Prueba', role });
const tokenFor = (role) => issueUserToken(fakeUser(role));
const { translateLegacyStation, projectToMap } = await import('../src/legacyAcl.js');
const { toTransaction, canSee } = await import('../src/resolvers.js');
const { yoga, allowedOrigin } = await import('../src/index.js');

test('el token de operador no tiene flags.admin', async () => {
  const claims = await verifyToken(await tokenFor('fleet-operator'));
  assert.equal(claims.sub, 'u-fleet-operator');
  const ctx = { user: { subject: claims.sub, scopes: claims.scope.split(' ') } };
  assert.doesNotThrow(() => requireScope(ctx, SCOPES.RESERVATIONS_WRITE));
  assert.throws(() => requireScope(ctx, SCOPES.FLAGS_ADMIN), /Falta el scope/);
});

test('un token alterado se rechaza', async () => {
  const token = await tokenFor('auditor');
  const [h, p, s] = token.split('.');
  const forged = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(p, 'base64url')), scope: SCOPES.FLAGS_ADMIN }))
    .toString('base64url');
  await assert.rejects(verifyToken(`${h}.${forged}.${s}`));
});

test('ACL traduce el modelo heredado al lenguaje del core', () => {
  const st = translateLegacyStation({
    cod: 'EST-USA-005', nom: 'USAQUEN ULT MILLA', mpio: 'BOGOTA D.C.', dir: 'CL 140', coord: '4.7206,-74.0336',
    conectores: [{ num: 2, tipo: 'CCS-2', kw: '350', estado: 'Cargando' }, { num: 1, tipo: 'T2', kw: '22', estado: 'Libre' }],
  });
  assert.equal(st.source, 'LEGACY_ACL');
  assert.equal(st.city, 'Bogota');
  assert.deepEqual(st.connectors.map((c) => [c.id, c.type, c.tier, c.status]),
    [['C2', 'CCS2', 'ULTRA', 'CHARGING'], ['C1', 'TYPE2', 'FAST', 'AVAILABLE']]);
  assert.equal(st.availableConnectors, 1);
  const { mapX, mapY } = projectToMap(4.7206, -74.0336);
  assert.ok(mapX > 50 && mapY < 70);
});

test('toTransaction marca el rollback', () => {
  const tx = toTransaction({ saga_id: 's', state: 'COMPENSATED', fleet_id: 'f', station_id: 'E', connector_id: 'C1',
    estimated_kwh: 10, created_at: 'a', updated_at: 'b', compensations: [{ action: 'REFUND_FUNDS', status: 'DONE', refunded_cop: 5 }] });
  assert.equal(tx.outcome, 'ROLLED_BACK');
  assert.equal(tx.isTerminal, true);
  assert.equal(tx.compensations[0].refundedCop, 5);
});

test('GraphQL sin token responde UNAUTHENTICATED y limita profundidad', async () => {
  const res = await yoga.fetch('http://x/graphql', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: '{ viewer { subject } }' }),
  });
  const body = await res.json();
  assert.equal(body.errors[0].extensions.code, 'UNAUTHENTICATED');

  const deep = '{ transactions { steps { step } compensations { action } } }';
  const ok = await (await yoga.fetch('http://x/graphql', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: deep }) })).json();
  assert.ok(!String(ok.errors?.[0]?.message).includes('Profundidad'));
});

test('CORS: refleja solo orígenes de la lista y permite cualquiera sin lista', () => {
  const list = ['http://127.0.0.1:5173', 'http://localhost:5173'];
  assert.equal(allowedOrigin('http://127.0.0.1:5173', list), 'http://127.0.0.1:5173');
  assert.equal(allowedOrigin('http://localhost:5173', list), 'http://localhost:5173');
  assert.equal(allowedOrigin('http://evil.example', list), null);
  assert.equal(allowedOrigin(undefined, list), null);
  assert.equal(allowedOrigin('http://x', []), '*');
});

test('los roles no comparten responsabilidades', () => {
  const op = ROLES['fleet-operator'];
  const admin = ROLES['platform-admin'];
  const auditor = ROLES.auditor;
  assert.ok(op.includes(SCOPES.RESERVATIONS_WRITE) && op.includes(SCOPES.PAYMENTS_MANAGE));
  assert.ok(!op.includes(SCOPES.AUDIT_READ) && !op.includes(SCOPES.FLAGS_ADMIN) && !op.includes(SCOPES.USERS_ADMIN));
  assert.ok(admin.includes(SCOPES.FLAGS_ADMIN) && admin.includes(SCOPES.USERS_ADMIN));
  assert.ok(!admin.includes(SCOPES.RESERVATIONS_WRITE) && !admin.includes(SCOPES.TRANSACTIONS_READ) && !admin.includes(SCOPES.AUDIT_READ));
  assert.deepEqual(auditor, [SCOPES.STATIONS_READ, SCOPES.AUDIT_READ]);
});

test('el rol sale del dominio del correo', () => {
  assert.equal(roleForEmail('sofia@operador.aegisvolt.co'), 'fleet-operator');
  assert.equal(roleForEmail('Ana@Admin'), 'platform-admin');
  assert.equal(roleForEmail('luis@auditor.aegisvolt.co'), 'auditor');
  assert.equal(roleForEmail('x@gmail.com'), null);
  assert.equal(roleForEmail('x@operadorfalso.co'), null);
  assert.equal(roleForEmail('sin-arroba'), null);
});

test('contraseñas con scrypt: verificación correcta y política mínima', async () => {
  const stored = await hashPassword('Carga2026');
  assert.ok(stored.startsWith('scrypt$') && !stored.includes('Carga2026'));
  assert.equal(await verifyPassword('Carga2026', stored), true);
  assert.equal(await verifyPassword('carga2026', stored), false);
  assert.equal(await verifyPassword('Carga2026', 'texto-plano'), false);
  assert.ok(validatePassword('corta1'));
  assert.ok(validatePassword('sololetras'));
  assert.equal(validatePassword('Carga2026'), null);
});

test('un operador solo ve lo suyo; el auditor ve todo', () => {
  const op = { subject: 'u-1', scopes: ROLES['fleet-operator'] };
  const auditor = { subject: 'u-2', scopes: ROLES.auditor };
  assert.equal(canSee(op, 'u-1'), true);
  assert.equal(canSee(op, 'u-9'), false);
  assert.equal(canSee(op, null), false);
  assert.equal(canSee(auditor, 'u-9'), true);
});
