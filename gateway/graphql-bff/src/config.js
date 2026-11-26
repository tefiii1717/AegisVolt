const env = process.env;

export const config = {
  port: Number(env.PORT ?? 4000),
  environment: env.AEGIS_ENV ?? 'local',
  version: env.APP_VERSION ?? 'dev',
  region: env.AWS_REGION ?? 'us-east-1',
  authMode: env.AUTH_MODE ?? 'dev', // dev | cognito
  cognito: {
    issuer: env.COGNITO_ISSUER ?? '',
    clientIds: (env.COGNITO_CLIENT_IDS ?? '').split(',').filter(Boolean),
  },
  corsOrigins: (env.CORS_ORIGINS ?? 'http://localhost:5173').split(',').filter(Boolean),
  urls: {
    query: env.QUERY_SERVICE_URL ?? 'http://query-projection:8000',
    orchestrator: env.ORCHESTRATOR_URL ?? 'http://saga-orchestrator:8000',
    legacy: env.LEGACY_CSMS_URL ?? 'http://legacy-csms:8000',
    flags: env.FLAGS_API_URL ?? 'http://flags-admin:8000',
    reservation: env.RESERVATION_URL ?? 'http://station-reservation:8000',
    billing: env.BILLING_URL ?? 'http://billing-wallet:8000',
    dispatch: env.DISPATCH_URL ?? 'http://energy-dispatch:8000',
  },
  // Proveedor de identidad local (AUTH_MODE=dev): tabla de usuarios y llave de firma compartida
  // por todas las réplicas (Secret de Kubernetes). Sin llave, cada proceso genera una efímera.
  identityTable: env.IDENTITY_TABLE ?? `aegisvolt-identity-${env.AEGIS_ENV ?? 'local'}`,
  dynamodbEndpoint: env.DYNAMODB_ENDPOINT ?? '',
  jwtPrivateKeyPem: env.JWT_PRIVATE_KEY ?? '',
  bootstrapAdmin: { email: env.BOOTSTRAP_ADMIN_EMAIL ?? '', password: env.BOOTSTRAP_ADMIN_PASSWORD ?? '' },
  httpTimeoutMs: Number(env.HTTP_TIMEOUT_MS ?? 2500),
  maxQueryDepth: Number(env.MAX_QUERY_DEPTH ?? 8),
};

if (config.environment === 'prod' && config.authMode === 'dev') {
  // Defensa: el emisor de tokens de desarrollo jamás debe existir en producción.
  throw new Error('AUTH_MODE=dev está prohibido en prod');
}
