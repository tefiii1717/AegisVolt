/**
 * Clientes HTTP hacia los servicios internos, cada uno protegido por un Circuit Breaker (opossum):
 * CLOSED → OPEN (tras 50 % de errores en la ventana) → HALF_OPEN (tras resetTimeout) → CLOSED.
 */
import CircuitBreaker from 'opossum';
import { config } from './config.js';
import { log, requestContext } from './logger.js';

export class UpstreamError extends Error {
  constructor(service, status, body) {
    super(`${service} respondió ${status}`);
    this.service = service;
    this.status = status;
    this.body = body;
  }
}

async function rawFetch(service, baseUrl, path, init = {}) {
  const correlationId = requestContext.getStore()?.correlationId;
  const res = await fetch(`${baseUrl}${path}`, {
    ...init,
    signal: AbortSignal.timeout(config.httpTimeoutMs),
    headers: {
      'content-type': 'application/json',
      ...(correlationId ? { 'x-correlation-id': correlationId } : {}),
      ...(init.headers ?? {}),
    },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (res.status === 404) return null; // 404 es una respuesta válida, no un fallo del servicio
  if (!res.ok) throw new UpstreamError(service, res.status, body);
  return body;
}

export const breakers = {};

function client(service, baseUrl) {
  const breaker = new CircuitBreaker((path, init) => rawFetch(service, baseUrl, path, init), {
    name: service,
    timeout: config.httpTimeoutMs + 500,
    errorThresholdPercentage: 50,
    volumeThreshold: 4,
    resetTimeout: 15_000,
    // Errores 4xx del cliente no deben abrir el circuito.
    errorFilter: (err) => err instanceof UpstreamError && err.status < 500,
  });
  breaker.on('open', () => log.warn('circuit breaker ABIERTO', { breaker: service }));
  breaker.on('halfOpen', () => log.warn('circuit breaker SEMI-ABIERTO', { breaker: service }));
  breaker.on('close', () => log.info('circuit breaker CERRADO', { breaker: service }));
  breakers[service] = breaker;
  return {
    get: (path) => breaker.fire(path, { method: 'GET' }),
    post: (path, body, headers = {}) => breaker.fire(path, { method: 'POST', body: JSON.stringify(body), headers }),
    put: (path, body, headers = {}) => breaker.fire(path, { method: 'PUT', body: JSON.stringify(body), headers }),
  };
}

export const services = {
  query: client('query-projection', config.urls.query),
  orchestrator: client('saga-orchestrator', config.urls.orchestrator),
  legacy: client('legacy-csms', config.urls.legacy),
  flags: client('flags-admin', config.urls.flags),
};

export function breakerState(b) {
  const state = b.opened ? 'OPEN' : b.halfOpen ? 'HALF_OPEN' : 'CLOSED';
  return { name: `bff→${b.name}`, state, failures: b.stats.failures, rejected: b.stats.rejects };
}

export async function healthOf(service, baseUrl) {
  try {
    const res = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(1500) });
    if (!res.ok) return { service, status: 'DOWN', circuitBreakers: [] };
    const body = await res.json().catch(() => ({}));
    return {
      service,
      status: 'UP',
      version: body.version ?? null,
      circuitBreakers: (body.circuit_breakers ?? []).map((b) => ({
        name: b.name, state: b.state, failures: b.consecutive_failures ?? 0, rejected: b.rejected ?? 0,
      })),
    };
  } catch {
    return { service, status: 'DOWN', circuitBreakers: [] };
  }
}
