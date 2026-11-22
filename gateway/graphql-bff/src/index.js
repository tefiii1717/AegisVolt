import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { GraphQLError } from 'graphql';
import { createSchema, createYoga } from 'graphql-yoga';
import { ROLES, authenticate, devJwks, issueUserToken } from './auth.js';
import { config } from './config.js';
import { depthLimit } from './depthLimit.js';
import { IdentityError, authenticateUser, createUser, ensureBootstrapAdmin, publicUser } from './identity.js';
import { errorsLastMinute, log, requestContext } from './logger.js';
import { resolvers } from './resolvers.js';

const typeDefs = readFileSync(new URL('./schema.graphql', import.meta.url), 'utf8');

export const yoga = createYoga({
  schema: createSchema({ typeDefs, resolvers }),
  graphqlEndpoint: '/graphql',
  // Introspección y GraphiQL deshabilitados en prod (reduce superficie de reconocimiento).
  graphiql: config.environment !== 'prod',
  maskedErrors: true,
  cors: { origin: config.corsOrigins, credentials: false, methods: ['POST', 'GET', 'OPTIONS'] },
  plugins: [
    {
      onValidate({ addValidationRule }) {
        addValidationRule(depthLimit(config.maxQueryDepth));
        if (config.environment === 'prod') {
          addValidationRule((ctx) => ({
            Field(node) {
              if (node.name.value === '__schema' || node.name.value === '__type') {
                ctx.reportError(new GraphQLError('Introspección deshabilitada'));
              }
            },
          }));
        }
      },
    },
  ],
  context: async ({ request }) => ({ user: await authenticate(request) }),
  logging: false,
});

async function readJson(req, limit = 4096) {
  let body = '';
  for await (const chunk of req) {
    body += chunk;
    if (body.length > limit) throw new Error('payload demasiado grande');
  }
  return body ? JSON.parse(body) : {};
}

// Origen permitido para las rutas propias (fuera de Yoga): refleja el Origin si está en la
// lista CORS_ORIGINS; sin lista configurada se permite cualquiera ('*').
export function allowedOrigin(origin, origins = config.corsOrigins) {
  if (origins.length === 0) return '*';
  return origin && origins.includes(origin) ? origin : null;
}

function corsHeaders(req) {
  const allow = allowedOrigin(req.headers.origin);
  return allow ? { 'access-control-allow-origin': allow, vary: 'Origin' } : { vary: 'Origin' };
}

function send(req, res, status, payload) {
  res.writeHead(status, { 'content-type': 'application/json', ...corsHeaders(req) });
  res.end(JSON.stringify(payload));
}

const AUTH_ROUTES = new Set(['/auth/register', '/auth/login']);

// Freno a la fuerza bruta: 5 fallos por IP + correo en 5 minutos bloquean el login temporalmente.
const LOGIN_WINDOW_MS = 5 * 60_000;
const failures = new Map();
function loginThrottled(key) {
  const f = failures.get(key);
  if (f && Date.now() - f.first > LOGIN_WINDOW_MS) failures.delete(key);
  return (failures.get(key)?.count ?? 0) >= 5;
}
function registerLoginFailure(key) {
  const f = failures.get(key) ?? { first: Date.now(), count: 0 };
  f.count += 1;
  failures.set(key, f);
}

const server = createServer((req, res) => {
  const correlationId = req.headers['x-correlation-id'] ?? randomUUID();
  res.setHeader('x-correlation-id', correlationId);
  requestContext.run({ correlationId }, async () => {
    const url = new URL(req.url, 'http://bff');
    if (url.pathname === '/health/live' || url.pathname === '/health/ready') return send(req, res, 200, { status: 'UP' });
    if (url.pathname === '/health/stats') {
      return send(req, res, 200, { service: 'graphql-bff', version: config.version, errors_last_minute: errorsLastMinute() });
    }
    if (config.authMode === 'dev' && AUTH_ROUTES.has(url.pathname)) {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, { ...corsHeaders(req),
          'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'POST' });
        return res.end();
      }
      if (req.method !== 'POST') return send(req, res, 405, { error: 'método no permitido' });
      try {
        const body = await readJson(req);
        if (url.pathname === '/auth/register') {
          // Autorregistro: solo cuentas @operador (lo valida identity.createUser sin actor).
          return send(req, res, 201, { user: await createUser(body) });
        }
        const throttleKey = `${req.socket.remoteAddress}|${String(body.email ?? '').toLowerCase()}`;
        if (loginThrottled(throttleKey)) return send(req, res, 429, { error: 'Demasiados intentos. Espera unos minutos.' });
        let user;
        try {
          user = await authenticateUser(body.email, body.password);
        } catch (err) {
          registerLoginFailure(throttleKey);
          throw err;
        }
        const token = await issueUserToken(user);
        log.info('inicio de sesión', { user: user.user_id, role: user.role });
        return send(req, res, 200, { access_token: token, token_type: 'Bearer', expires_in: 3600,
          scope: ROLES[user.role].join(' '), user: publicUser(user) });
      } catch (err) {
        if (err instanceof IdentityError) return send(req, res, err.status, { error: err.message });
        if (err instanceof SyntaxError || err.message === 'payload demasiado grande') {
          return send(req, res, 400, { error: 'Solicitud inválida.' });
        }
        log.error('fallo en autenticación', { error: String(err.message ?? err) });
        return send(req, res, 500, { error: 'No se pudo procesar la solicitud.' });
      }
    }
    if (config.authMode === 'dev' && url.pathname === '/.well-known/jwks.json') return send(req, res, 200, await devJwks());
    return yoga(req, res);
  });
});

if (process.env.NODE_ENV !== 'test') {
  server.listen(config.port, () => log.info('graphql-bff escuchando', { port: config.port, auth: config.authMode }));
  if (config.authMode === 'dev') {
    // En segundo plano: si DynamoDB tarda, el BFF igual arranca y reintenta.
    const bootstrap = (attempt = 1) => ensureBootstrapAdmin()
      .then((created) => created && log.info('administrador inicial creado', { email: config.bootstrapAdmin.email }))
      .catch((err) => {
        log.warn('no se pudo asegurar el administrador inicial', { attempt, error: String(err.message ?? err) });
        if (attempt < 20) setTimeout(() => bootstrap(attempt + 1), 3000);
      });
    bootstrap();
  }
}

export default server;
