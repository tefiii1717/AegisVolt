# syntax=docker/dockerfile:1.7
# Imagen única parametrizada para los microservicios Python de AegisVolt.
#   docker build -f deploy/docker/python-service.Dockerfile --build-arg SERVICE_DIR=services/billing-wallet .
# Buenas prácticas: multi-stage (sin compiladores en runtime), venv aislado, usuario no-root,
# sistema de archivos de solo lectura compatible, HEALTHCHECK sin curl, capas cacheables.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# 1) dependencias (capa que cambia poco)
COPY libs/aegis-common/pyproject.toml libs/aegis-common/pyproject.toml
COPY libs/aegis-common/aegis_common libs/aegis-common/aegis_common
ARG SERVICE_DIR
COPY ${SERVICE_DIR}/requirements.txt requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ./libs/aegis-common -r requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ARG SERVICE_DIR
ARG APP_VERSION=dev
ARG APP_MODULE=app.main:app
LABEL org.opencontainers.image.source="https://github.com/tefiii1717/AegisVolt" \
      org.opencontainers.image.version="${APP_VERSION}"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    APP_MODULE=${APP_MODULE}
RUN groupadd --gid 10001 aegis && useradd --uid 10001 --gid aegis --no-create-home --shell /usr/sbin/nologin aegis
COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
COPY --chown=10001:10001 ${SERVICE_DIR}/ ./
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=25s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"]
CMD ["sh", "-c", "exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000 --no-access-log --proxy-headers --timeout-graceful-shutdown 20"]
