/**
 * OAuth2 / JWT con scopes.
 *  - cognito: valida access tokens de Amazon Cognito (firma RS256 vía JWKS, issuer, token_use, client_id).
 *    API Gateway ya validó el JWT en el borde; el BFF lo vuelve a validar (defensa en profundidad)
 *    y aplica autorización FINA por campo con scopes.
 *  - dev: emisor local de tokens (solo local/dev/test) para poder demostrar roles sin Cognito.
 */
import { SignJWT, createRemoteJWKSet, exportJWK, generateKeyPair, jwtVerify } from 'jose';
import { GraphQLError } from 'graphql';
import { config } from './config.js';

export const SCOPES = {
  STATIONS_READ: 'aegisvolt/stations.read',
  TRANSACTIONS_READ: 'aegisvolt/transactions.read',
  RESERVATIONS_WRITE: 'aegisvolt/reservations.write',
  FLAGS_ADMIN: 'aegisvolt/flags.admin',
};

export const ROLES = {
  'fleet-operator': [SCOPES.STATIONS_READ, SCOPES.TRANSACTIONS_READ, SCOPES.RESERVATIONS_WRITE],
  'platform-admin': Object.values(SCOPES),
  auditor: [SCOPES.STATIONS_READ, SCOPES.TRANSACTIONS_READ],
};

const DEV_ISSUER = 'https://auth.aegisvolt.local';
const AUDIENCE = 'aegisvolt-api';
let devKeys;
let remoteJwks;

async function devKeyPair() {
  devKeys ??= await generateKeyPair('RS256');
  return devKeys;
}

export async function issueDevToken(role) {
  if (config.authMode !== 'dev') throw new Error('emisor dev deshabilitado');
  const scopes = ROLES[role];
  if (!scopes) throw new Error('rol desconocido');
  const { privateKey } = await devKeyPair();
  return new SignJWT({ scope: scopes.join(' '), role, token_use: 'access', client_id: 'aegisvolt-web-dev' })
    .setProtectedHeader({ alg: 'RS256', kid: 'dev-1' })
    .setIssuer(DEV_ISSUER)
    .setAudience(AUDIENCE)
    .setSubject(`dev-${role}`)
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
