import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { GraphQLError } from 'graphql';
import { createSchema, createYoga } from 'graphql-yoga';
import { ROLES, authenticate, devJwks, issueDevToken } from './auth.js';
import { config } from './config.js';
import { depthLimit } from './depthLimit.js';
import { log, requestContext } from './logger.js';
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

function send(res, status, payload) {
  res.writeHead(status, { 'content-type': 'application/json', 'access-control-allow-origin': config.corsOrigins[0] ?? '*' });
  res.end(JSON.stringify(payload));
}

const server = createServer((req, res) => {
  const correlationId = req.headers['x-correlation-id'] ?? randomUUID();
  res.setHeader('x-correlation-id', correlationId);
  requestContext.run({ correlationId }, async () => {
    const url = new URL(req.url, 'http://bff');
    if (url.pathname === '/health/live' || url.pathname === '/health/ready') return send(res, 200, { status: 'UP' });
    if (config.authMode === 'dev' && url.pathname === '/auth/dev-token') {
      if (req.method === 'OPTIONS') {
        res.writeHead(204, { 'access-control-allow-origin': config.corsOrigins[0] ?? '*',
          'access-control-allow-headers': 'content-type', 'access-control-allow-methods': 'POST' });
        return res.end();
      }
      try {
        const { role } = await readJson(req);
        const token = await issueDevToken(role);
        return send(res, 200, { access_token: token, token_type: 'Bearer', expires_in: 3600, scope: ROLES[role].join(' ') });
      } catch (err) {
        return send(res, 400, { error: String(err.message) });
      }
    }
    if (config.authMode === 'dev' && url.pathname === '/.well-known/jwks.json') return send(res, 200, await devJwks());
    return yoga(req, res);
  });
});

if (process.env.NODE_ENV !== 'test') {
  server.listen(config.port, () => log.info('graphql-bff escuchando', { port: config.port, auth: config.authMode }));
}

export default server;
