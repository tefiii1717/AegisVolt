import { GraphQLError } from 'graphql';
import { SCOPES, requireScope, requireUser } from './auth.js';
import { breakerState, breakers, healthOf, services } from './clients.js';
import { config } from './config.js';
import { translateCoreStation, translateLegacyStation } from './legacyAcl.js';
import { log } from './logger.js';

const TERMINAL = new Set(['COMPLETED', 'COMPENSATED', 'FAILED']);
const FLAG_NAME = /^[A-Z][A-Z0-9_]{2,63}$/;

// ---- flags (caché corta: un cambio se refleja en ≤ 3 s sin redesplegar) ----
let flagCache = { at: 0, flags: [] };
export async function loadFlags({ force = false } = {}) {
  if (!force && Date.now() - flagCache.at < 3000) return flagCache.flags;
  try {
    const body = await services.flags.get('/flags');
    flagCache = { at: Date.now(), flags: body?.flags ?? [] };
  } catch (err) {
    log.warn('flags-admin no disponible; se usa el último snapshot', { error: String(err.message ?? err) });
  }
  return flagCache.flags;
}
const flagOn = (flags, name) => flags.find((f) => f.name === name)?.enabled === true;

function toFlag(f) {
  return {
    name: f.name, enabled: !!f.enabled, description: f.description ?? null, environment: f.environment ?? config.environment,
    prodLocked: !!f.prod_locked, updatedAt: f.updated_at ?? null, updatedBy: f.updated_by ?? null,
  };
}

export function toTransaction(s) {
  if (!s) return null;
  const outcome = s.state === 'COMPLETED' ? 'SUCCESS'
    : s.state === 'COMPENSATED' ? 'ROLLED_BACK'
      : s.state === 'FAILED' ? 'FAILED' : 'IN_PROGRESS';
  return {
    id: s.saga_id,
    state: s.state,
    isTerminal: TERMINAL.has(s.state),
    outcome,
    fleetId: s.fleet_id,
    vehicleId: s.vehicle_id ?? null,
    stationId: s.station_id,
    connectorId: s.connector_id,
    estimatedKwh: s.estimated_kwh,
    powerKw: s.power_kw ?? null,
    amountCop: s.amount_cop ?? null,
    surgeApplied: s.surge_applied ?? null,
    failureReason: s.failure_reason ?? null,
    stuck: !!s.stuck,
    createdAt: s.created_at,
    updatedAt: s.updated_at,
    steps: (s.steps ?? []).map((st) => ({ step: st.step, status: st.status, at: st.at ?? null,
      detail: st.detail ? JSON.stringify(st.detail) : null })),
    compensations: (s.compensations ?? []).map((c) => ({ action: c.action, status: c.status, at: c.at ?? null,
      refundedCop: c.refunded_cop ?? null })),
  };
}

async function stationsFromStrangler() {
  const flags = await loadFlags();
  if (flagOn(flags, 'STRANGLER_ROUTE_STATIONS_TO_CORE')) {
    return (await services.query.get('/stations') ?? []).map(translateCoreStation);
  }
  const legacy = await services.legacy.get('/api/v1/estaciones');
  return (legacy?.estaciones ?? []).map(translateLegacyStation);
}

async function transactionById(id) {
  const projected = await services.query.get(`/sagas/${encodeURIComponent(id)}`);
  if (projected) return toTransaction(projected);
  // Consistencia eventual: la proyección aún no llega → read-your-writes contra el log de SAGA.
  return toTransaction(await services.orchestrator.get(`/sagas/${encodeURIComponent(id)}`));
}

export const resolvers = {
  Query: {
    viewer: (_, __, ctx) => requireUser(ctx),
    environment: (_, __, ctx) => {
      requireUser(ctx);
      return { name: config.environment, version: config.version, region: config.region, authMode: config.authMode };
    },
    stations: async (_, { city }, ctx) => {
      requireScope(ctx, SCOPES.STATIONS_READ);
      const all = await stationsFromStrangler();
      return city ? all.filter((s) => s.city.toLowerCase() === city.toLowerCase()) : all;
    },
    station: async (_, { id }, ctx) => {
      requireScope(ctx, SCOPES.STATIONS_READ);
      return (await stationsFromStrangler()).find((s) => s.id === id) ?? null;
    },
    fleets: async (_, __, ctx) => {
      requireScope(ctx, SCOPES.TRANSACTIONS_READ);
      return (await services.query.get('/fleets') ?? []).map((f) => ({
        id: f.sk?.replace('FLEET#', '') ?? f.fleet_id, name: f.name ?? null,
        availableCop: f.available_cop ?? 0, heldCop: f.held_cop ?? 0, capturedCop: f.captured_cop ?? 0,
      }));
    },
    transactions: async (_, { limit }, ctx) => {
      requireScope(ctx, SCOPES.TRANSACTIONS_READ);
      const n = Math.min(Math.max(limit ?? 20, 1), 100);
      return (await services.query.get(`/sagas?limit=${n}`) ?? []).map(toTransaction);
    },
    transaction: async (_, { id }, ctx) => {
      requireScope(ctx, SCOPES.TRANSACTIONS_READ);
      return transactionById(id);
    },
    featureFlags: async (_, __, ctx) => {
      requireUser(ctx);
      return (await loadFlags()).map(toFlag);
    },
    platformHealth: async (_, __, ctx) => {
      requireScope(ctx, SCOPES.TRANSACTIONS_READ);
      const u = config.urls;
      const checks = await Promise.all([
        healthOf('station-reservation-service', u.reservation),
        healthOf('billing-wallet-service', u.billing),
        healthOf('energy-dispatch-service', u.dispatch),
        healthOf('saga-orchestrator', u.orchestrator),
        healthOf('query-projection-service', u.query),
        healthOf('legacy-csms', u.legacy),
      ]);
      return [{ service: 'graphql-bff', status: 'UP', version: config.version,
        circuitBreakers: Object.values(breakers).map(breakerState) }, ...checks];
    },
  },

  Mutation: {
    reserveAndCharge: async (_, { input }, ctx) => {
      const user = requireScope(ctx, SCOPES.RESERVATIONS_WRITE);
      if (!(input.estimatedKwh > 0 && input.estimatedKwh <= 400)) {
        throw new GraphQLError('estimatedKwh debe estar entre 0 y 400', { extensions: { code: 'BAD_USER_INPUT' } });
      }
      const headers = input.idempotencyKey ? { 'idempotency-key': input.idempotencyKey } : {};
      const saga = await services.orchestrator.post('/sagas/reserve-and-charge', {
        fleet_id: input.fleetId, station_id: input.stationId, connector_id: input.connectorId,
        vehicle_id: input.vehicleId, estimated_kwh: input.estimatedKwh, fast_charge: !!input.fastCharge,
        requested_by: user.subject,
      }, headers);
      log.info('SAGA solicitada desde GraphQL', { correlation_id: saga.saga_id, user: user.subject });
      return { accepted: true, transaction: toTransaction(saga) };
    },
    setFeatureFlag: async (_, { name, enabled }, ctx) => {
      const user = requireScope(ctx, SCOPES.FLAGS_ADMIN);
      if (!FLAG_NAME.test(name)) throw new GraphQLError('nombre de flag inválido', { extensions: { code: 'BAD_USER_INPUT' } });
      const updated = await services.flags.put(`/flags/${name}`, { enabled }, { 'x-aegis-actor': user.subject });
      if (!updated) throw new GraphQLError('flag no encontrada', { extensions: { code: 'NOT_FOUND' } });
      await loadFlags({ force: true });
      log.warn('feature flag modificada', { flag: name, enabled, user: user.subject });
      return toFlag(updated);
    },
  },

  Subscription: {
    transactionUpdated: {
      subscribe: async function* (_, { id }, ctx) {
        requireScope(ctx, SCOPES.TRANSACTIONS_READ);
        let last = '';
        for (let i = 0; i < 300; i += 1) {
          const tx = await transactionById(id);
          const fingerprint = tx ? `${tx.state}:${tx.updatedAt}` : '';
          if (tx && fingerprint !== last) {
            last = fingerprint;
            yield { transactionUpdated: tx };
            if (tx.isTerminal) return;
          }
          await new Promise((r) => setTimeout(r, 1000));
        }
      },
    },
  },
};
