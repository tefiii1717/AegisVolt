process.env.NODE_ENV = 'test';
process.env.AUTH_MODE = 'dev';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const { issueDevToken, verifyToken, requireScope, SCOPES } = await import('../src/auth.js');
const { translateLegacyStation, projectToMap } = await import('../src/legacyAcl.js');
const { toTransaction } = await import('../src/resolvers.js');
const { yoga, allowedOrigin } = await import('../src/index.js');

test('el token de operador no tiene flags.admin', async () => {
  const claims = await verifyToken(await issueDevToken('fleet-operator'));
  const ctx = { user: { subject: claims.sub, scopes: claims.scope.split(' ') } };
  assert.doesNotThrow(() => requireScope(ctx, SCOPES.RESERVATIONS_WRITE));
  assert.throws(() => requireScope(ctx, SCOPES.FLAGS_ADMIN), /Falta el scope/);
});

test('un token alterado se rechaza', async () => {
  const token = await issueDevToken('auditor');
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
