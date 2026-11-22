/**
 * OAuth2 / JWT con scopes.
 *  - cognito: valida access tokens de Amazon Cognito (firma RS256 vía JWKS, issuer, token_use, client_id).
 *    API Gateway ya validó el JWT en el borde; el BFF lo vuelve a validar (defensa en profundidad)
 *    y aplica autorización FINA por campo con scopes.
 *  - dev: proveedor de identidad local (identity.js): correo + contraseña y token firmado por el BFF
 *    (solo local/dev/test). La llave privada llega por un Secret, así que todas las réplicas firman y
 *    verifican con la misma llave.
 *
 * Separación de responsabilidades: cada rol recibe SOLO los scopes de su trabajo.
 *  - Operador de flota: reserva, administra sus medios de pago y ve SUS cargas y facturas.
 *  - Administrador: feature flags, usuarios y salud de la plataforma; no ve cargas ni facturas.
 *  - Auditor: lectura de TODAS las transacciones y facturas; no puede reservar ni cambiar nada.
 */
import { createPrivateKey, createPublicKey } from 'node:crypto';
import { SignJWT, createRemoteJWKSet, exportJWK, generateKeyPair, jwtVerify } from 'jose';
import { GraphQLError } from 'graphql';
import { config } from './config.js';

export const SCOPES = {
  STATIONS_READ: 'aegisvolt/stations.read',
  TRANSACTIONS_READ: 'aegisvolt/transactions.read', // solo las cargas propias
  RESERVATIONS_WRITE: 'aegisvolt/reservations.write',
  PAYMENTS_MANAGE: 'aegisvolt/payments.manage',
  AUDIT_READ: 'aegisvolt/audit.read', // todas las transacciones y facturas, solo lectura
  FLAGS_ADMIN: 'aegisvolt/flags.admin',
  USERS_ADMIN: 'aegisvolt/users.admin',
  PLATFORM_READ: 'aegisvolt/platform.read',
};

export const ROLES = {
  'fleet-operator': [SCOPES.STATIONS_READ, SCOPES.TRANSACTIONS_READ, SCOPES.RESERVATIONS_WRITE, SCOPES.PAYMENTS_MANAGE],
  'platform-admin': [SCOPES.STATIONS_READ, SCOPES.FLAGS_ADMIN, SCOPES.USERS_ADMIN, SCOPES.PLATFORM_READ],
  auditor: [SCOPES.STATIONS_READ, SCOPES.AUDIT_READ],
};

const DEV_ISSUER = 'https://auth.aegisvolt.local';
const AUDIENCE = 'aegisvolt-api';
let devKeys;
let remoteJwks;

async function devKeyPair() {
  if (!devKeys && config.jwtPrivateKeyPem) {
    const privateKey = createPrivateKey(config.jwtPrivateKeyPem);
    devKeys = { privateKey, publicKey: createPublicKey(privateKey) };
  }
  devKeys ??= await generateKeyPair('RS256');
  return devKeys;
}

/** Access token del proveedor local para un usuario autenticado (sub = id del usuario). */
export async function issueUserToken(user) {
  if (config.authMode !== 'dev') throw new Error('proveedor de identidad local deshabilitado');
  const scopes = ROLES[user.role];
  if (!scopes) throw new Error('rol desconocido');
  const { privateKey } = await devKeyPair();
  return new SignJWT({ scope: scopes.join(' '), role: user.role, email: user.email, name: user.name,
    token_use: 'access', client_id: 'aegisvolt-web-dev' })
    .setProtectedHeader({ alg: 'RS256', kid: 'dev-1' })
    .setIssuer(DEV_ISSUER)
    .setAudience(AUDIENCE)
    .setSubject(user.user_id)
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(privateKey);
}

export async function devJwks() {
  const { publicKey } = await devKeyPair();
  return { keys: [{ ...(await exportJWK(publicKey)), kid: 'dev-1', alg: 'RS256', use: 'sig' }] };
}

export async function verifyToken(token) {
  if (config.authMode === 'dev') {
    const { publicKey } = await devKeyPair();
    const { payload } = await jwtVerify(token, publicKey, { issuer: DEV_ISSUER, audience: AUDIENCE, algorithms: ['RS256'] });
    return payload;
  }
  remoteJwks ??= createRemoteJWKSet(new URL(`${config.cognito.issuer}/.well-known/jwks.json`));
  // Los access tokens de Cognito no traen `aud`; se valida client_id explícitamente.
  const { payload } = await jwtVerify(token, remoteJwks, { issuer: config.cognito.issuer, algorithms: ['RS256'] });
  if (payload.token_use !== 'access') throw new Error('se requiere un access token');
  if (config.cognito.clientIds.length && !config.cognito.clientIds.includes(payload.client_id)) {
    throw new Error('client_id no permitido');
  }
  return payload;
}

export async function authenticate(request) {
  const header = request.headers.get('authorization') ?? '';
  const [scheme, token] = header.split(' ');
  if (scheme?.toLowerCase() !== 'bearer' || !token) return null;
  try {
    const claims = await verifyToken(token);
    return {
      subject: String(claims.sub),
      email: claims.email ?? null,
      name: claims.name ?? null,
      role: claims.role ?? claims['cognito:groups']?.[0] ?? null,
      scopes: String(claims.scope ?? '').split(' ').filter(Boolean),
    };
  } catch {
    return null;
  }
}

export function requireUser(ctx) {
  if (!ctx.user) {
    throw new GraphQLError('No autenticado', { extensions: { code: 'UNAUTHENTICATED', http: { status: 401 } } });
  }
  return ctx.user;
}

export function requireScope(ctx, scope) {
  const user = requireUser(ctx);
  if (!user.scopes.includes(scope)) {
    throw new GraphQLError(`Falta el scope ${scope}`, { extensions: { code: 'FORBIDDEN', http: { status: 403 } } });
  }
  return user;
}

export const hasScope = (user, scope) => !!user?.scopes.includes(scope);

/** Exige al menos uno de los scopes (p. ej. transacciones propias o auditoría). */
export function requireAnyScope(ctx, scopes) {
  const user = requireUser(ctx);
  if (!scopes.some((s) => user.scopes.includes(s))) {
    throw new GraphQLError(`Falta uno de los scopes ${scopes.join(', ')}`, { extensions: { code: 'FORBIDDEN', http: { status: 403 } } });
  }
  return user;
}
