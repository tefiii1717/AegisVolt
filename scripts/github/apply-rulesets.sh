#!/usr/bin/env bash
# Crea (o actualiza) los rulesets de protección de ramas del repositorio.
#   bash scripts/github/apply-rulesets.sh tefiii1717/AegisVolt
# Requiere `gh auth login` con permisos de administración del repositorio.
#
# Qué bloquean (pregunta 6.2 de la sustentación):
#   non_fast_forward → `git push --force` a dev/test/prod es rechazado
#   pull_request      → `git push` directo a dev/test/prod es rechazado (todo entra por PR)
#   required_status_checks → el botón Merge se habilita solo con los 7 checks en verde
#   bypass_actors: [] → ni siquiera la administradora puede saltarse las reglas
#   merge-methods-promotion → dev→test y test→prod solo con merge commit (sin squash: historial lineal entre ramas)
set -euo pipefail
REPO="${1:-tefiii1717/AegisVolt}"
DIR="$(cd "$(dirname "$0")/../../.github/rulesets" && pwd)"

for file in "$DIR"/*.json; do
  name=$(jq -r .name "$file")
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$name\") | .id" || true)
  if [[ -n "$id" ]]; then
    gh api -X PUT "repos/$REPO/rulesets/$id" --input "$file" >/dev/null && echo "actualizado: $name"
  else
    gh api -X POST "repos/$REPO/rulesets" --input "$file" >/dev/null && echo "creado: $name"
  fi
done

# Squash para feature/* → dev y merge commit para promociones: se habilitan ambos a nivel de repo.
gh api -X PATCH "repos/$REPO" -F allow_squash_merge=true -F allow_merge_commit=true -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true >/dev/null
gh api -X PATCH "repos/$REPO" -f default_branch=dev >/dev/null
echo "Listo. Prueba: git push --force origin test  →  debe ser rechazado por el ruleset."
