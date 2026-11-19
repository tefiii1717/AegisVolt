// Valores para desarrollo local. En contenedor, /docker-entrypoint.d/ regenera este archivo
// desde variables de entorno (ver frontend/docker/config.js.template).
window.__AEGIS_CONFIG__ = {
  environment: 'local',
  graphqlUrl: 'http://localhost:4000/graphql',
  authMode: 'dev',
  devTokenUrl: 'http://localhost:4000/auth/dev-token',
  cognitoDomain: '',
  cognitoClientId: '',
  redirectUri: 'http://localhost:5173/',
  pollMs: 2500,
};
