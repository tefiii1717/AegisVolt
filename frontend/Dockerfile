# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# nginx sin privilegios (escucha en 8080, usuario 101). La misma imagen sirve dev/test/prod:
# config.js se genera al arrancar desde variables de entorno (build once, deploy many).
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --chown=101:101 docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/config.js.template /etc/nginx/templates-aegis/config.js.template
COPY --chmod=0755 docker/40-aegis-config.sh /docker-entrypoint.d/40-aegis-config.sh
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
