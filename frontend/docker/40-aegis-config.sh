#!/bin/sh
# Genera /config.js y la CSP a partir del entorno del contenedor (ConfigMap en K8s / Compose).
set -eu
: "${AEGIS_ENV:=local}" "${GRAPHQL_URL:=http://localhost:4000/graphql}" "${AUTH_MODE:=dev}"
: "${DEV_TOKEN_URL:=}" "${COGNITO_DOMAIN:=}" "${COGNITO_CLIENT_ID:=}" "${REDIRECT_URI:=}" "${POLL_MS:=2500}"
if [ "$AEGIS_ENV" = "prod" ] && [ "$AUTH_MODE" = "dev" ]; then
  echo "AUTH_MODE=dev no está permitido en prod" >&2
  exit 1
fi
export AEGIS_ENV GRAPHQL_URL AUTH_MODE DEV_TOKEN_URL COGNITO_DOMAIN COGNITO_CLIENT_ID REDIRECT_URI POLL_MS
export AEGIS_CONNECT_SRC="$(echo "$GRAPHQL_URL" | sed -E 's#(https?://[^/]+).*#\1#') $(echo "${DEV_TOKEN_URL:-}" | sed -E 's#(https?://[^/]+).*#\1#') ${COGNITO_DOMAIN}"
envsubst < /etc/nginx/templates-aegis/config.js.template > /usr/share/nginx/html/config.js
envsubst '${AEGIS_CONNECT_SRC}' < /etc/nginx/conf.d/default.conf > /tmp/default.conf && cat /tmp/default.conf > /etc/nginx/conf.d/default.conf
