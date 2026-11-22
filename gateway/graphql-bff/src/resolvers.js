import { GraphQLError } from 'graphql';
import { SCOPES, hasScope, requireAnyScope, requireScope, requireUser } from './auth.js';
import { UpstreamError, breakerState, breakers, healthOf, services } from './clients.js';
import { IdentityError, createUser, listUsers } from './identity.js';
import { config } from './config.js';
import { translateCoreStation, translateLegacyStation } from './legacyAcl.js';
import { log } from './logger.js';

const TERMINAL = new Set(['COMPLETED', 'COMPENSATED', 'FAILED']);
const FLAG_NAME = /^[A-Z][A-Z0-9_]{2,63}$/;
const ACCOUNT_NUMBER = /^[0-9]{6,20}$/;
const PAYMENT_METHOD_ID = /^pm-[a-z0-9]{6,40}$/;

const badInput = (message) => new GraphQLError(message, { extensions: { code: 'BAD_USER_INPUT' } });
const forbidden = (message) => new GraphQLError(message, { extensions: { code: 'FORBIDDEN', http: { status: 403 } } });
const enc = encodeURIComponent;

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
    companyName: s.company_name ?? null,
    requestedBy: s.requested_by ?? null,
    requestedByEmail: s.requested_by_email ?? null,
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

export function toPaymentMethod(p) {
  return {
    id: p.payment_method_id, companyName: p.company_name, accountLast4: p.account_last4,
    creditLimitCop: p.credit_limit_cop ?? 0, availableCop: p.available_cop ?? 0, heldCop: p.held_cop ?? 0,
    capturedCop: p.captured_cop ?? 0, createdAt: p.created_at ?? null,
  };
}

export function toInvoice(i) {
  return {
    id: i.invoice_id, sagaId: i.saga_id, paymentMethodId: i.payment_method_id, companyName: i.company_name,
    accountLast4: i.account_last4 ?? null, requestedByEmail: i.requested_by_email ?? null,
    stationId: i.station_id ?? null, connectorId: i.connector_id ?? null, vehicleId: i.vehicle_id ?? null,
    energyKwh: i.energy_kwh, tariffCopPerKwh: i.tariff_cop_per_kwh ?? null, multiplier: i.multiplier ?? null,
    surgeApplied: i.surge_applied ?? null, subtotalCop: i.subtotal_cop, ivaCop: i.iva_cop, totalCop: i.total_cop,
    issuedAt: i.issued_at,
  };
}

/** Un operador solo ve lo suyo; el auditor (audit.read) ve todo. */
export function canSee(user, ownerId) {
  return hasScope(user, SCOPES.AUDIT_READ) || (!!ownerId && ownerId === user.subject);
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
      requireScope(ctx, SCOPES.AUDIT_READ);
      return (await services.query.get('/fleets') ?? []).map((f) => ({
        id: f.sk?.replace('FLEET#', '') ?? f.fleet_id, name: f.name ?? null,
        availableCop: f.available_cop ?? 0, heldCop: f.held_cop ?? 0, capturedCop: f.captured_cop ?? 0,
      }));
    },
    transactions: async (_, { limit }, ctx) => {
      const user = requireAnyScope(ctx, [SCOPES.TRANSACTIONS_READ, SCOPES.AUDIT_READ]);
      const n = Math.min(Math.max(limit ?? 20, 1), 100);
      const path = hasScope(user, SCOPES.AUDIT_READ) ? `/sagas?limit=${n}` : `/owners/${enc(user.subject)}/sagas?limit=${n}`;
      return (await services.query.get(path) ?? []).map(toTransaction);
    },
    transaction: async (_, { id }, ctx) => {
      const user = requireAnyScope(ctx, [SCOPES.TRANSACTIONS_READ, SCOPES.AUDIT_READ]);
      const tx = await transactionById(id);
      // Una transacción ajena se responde como inexistente: no se revela que existe.
      return tx && canSee(user, tx.requestedBy) ? tx : null;
    },
    paymentMethods: async (_, __, ctx) => {
      const user = requireScope(ctx, SCOPES.PAYMENTS_MANAGE);
      return (await services.query.get(`/owners/${enc(user.subject)}/payment-methods`) ?? []).map(toPaymentMethod);
    },
    invoices: async (_, { limit }, ctx) => {
      const user = requireAnyScope(ctx, [SCOPES.TRANSACTIONS_READ, SCOPES.AUDIT_READ]);
      const n = Math.min(Math.max(limit ?? 50, 1), 200);
      const path = hasScope(user, SCOPES.AUDIT_READ) ? `/invoices?limit=${n}` : `/owners/${enc(user.subject)}/invoices?limit=${n}`;
      return (await services.query.get(path) ?? []).map(toInvoice);
    },
    users: async (_, __, ctx) => {
      requireScope(ctx, SCOPES.USERS_ADMIN);
      return listUsers();
    },
    featureFlags: async (_, __, ctx) => {
      requireUser(ctx);
      return (await loadFlags()).map(toFlag);
    },
    platformHealth: async (_, __, ctx) => {
      requireScope(ctx, SCOPES.PLATFORM_READ);
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
      if (!(input.estimatedKwh > 0 && input.estimatedKwh <= 400)) throw badInput('estimatedKwh debe estar entre 0 y 400');
      const vehicleId = String(input.vehicleId ?? '').trim().toUpperCase();
      if (!/^[A-Z0-9-]{3,32}$/.test(vehicleId)) throw badInput('La placa o código del vehículo debe tener de 3 a 32 letras, números o guiones.');
      if (!PAYMENT_METHOD_ID.test(input.paymentMethodId)) throw badInput('Medio de pago inválido.');
      // Fuente de verdad (billing-wallet), no la proyección: la verificación de propiedad no puede ser eventual.
      const pm = await services.billing.get(`/internal/payment-methods/${enc(input.paymentMethodId)}`);
      if (!pm || pm.owner !== user.subject) throw forbidden('Ese medio de pago no es tuyo.');
      const headers = input.idempotencyKey ? { 'idempotency-key': input.idempotencyKey } : {};
      const saga = await services.orchestrator.post('/sagas/reserve-and-charge', {
        fleet_id: pm.payment_method_id, station_id: input.stationId, connector_id: input.connectorId,
        vehicle_id: vehicleId, estimated_kwh: input.estimatedKwh, fast_charge: !!input.fastCharge,
        requested_by: user.subject, requested_by_email: user.email ?? undefined, company_name: pm.company_name,
      }, headers);
      log.info('SAGA solicitada desde GraphQL', { correlation_id: saga.saga_id, user: user.subject });
      return { accepted: true, transaction: toTransaction(saga) };
    },
    registerPaymentMethod: async (_, { input }, ctx) => {
      const user = requireScope(ctx, SCOPES.PAYMENTS_MANAGE);
      const companyName = String(input.companyName ?? '').trim().replace(/\s+/g, ' ');
      const accountNumber = String(input.accountNumber ?? '').replace(/[\s-]/g, '');
      if (companyName.length < 2 || companyName.length > 80) throw badInput('El nombre de la empresa debe tener entre 2 y 80 caracteres.');
      if (!ACCOUNT_NUMBER.test(accountNumber)) throw badInput('El número de cuenta debe tener entre 6 y 20 dígitos.');
      try {
        const pm = await services.billing.post('/internal/payment-methods', {
          owner: user.subject, owner_email: user.email ?? undefined, company_name: companyName, account_number: accountNumber,
        });
        log.info('medio de pago registrado', { user: user.subject, payment_method: pm.payment_method_id });
        return toPaymentMethod(pm);
      } catch (err) {
        if (err instanceof UpstreamError && err.status < 500) throw badInput(err.body?.detail ?? 'Medio de pago rechazado.');
        throw err;
      }
    },
    createUser: async (_, { input }, ctx) => {
      const admin = requireScope(ctx, SCOPES.USERS_ADMIN);
      try {
        const created = await createUser(input, admin.email ?? admin.subject);
        log.warn('cuenta creada por un administrador', { admin: admin.subject, role: created.role });
        return created;
      } catch (err) {
        if (err instanceof IdentityError) throw badInput(err.message);
        throw err;
      }
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
        const user = requireAnyScope(ctx, [SCOPES.TRANSACTIONS_READ, SCOPES.AUDIT_READ]);
        let last = '';
        for (let i = 0; i < 300; i += 1) {
          const found = await transactionById(id);
          const tx = found && canSee(user, found.requestedBy) ? found : null;
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
