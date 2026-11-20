#!/usr/bin/env bash
# Tag de imagen DIRECCIONADO POR CONTENIDO: hash de los árboles git que forman la imagen.
# Como dev → test → prod se promueve con merge commits, el SHA del commit cambia, pero el contenido
# de cada servicio no: el tag es idéntico y la MISMA imagen (mismo digest) llega a prod sin recompilar.
#   scripts/ci/image-tag.sh services/billing-wallet libs/aegis-common deploy/docker
set -euo pipefail
hashes=""
for path in "$@"; do
  hashes+="$(git rev-parse "HEAD:${path}")"
done
printf '%s' "$hashes" | sha256sum | cut -c1-16
