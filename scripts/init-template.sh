#!/usr/bin/env bash
#
# init-template.sh — rebrand this starter template into a new product.
#
# Replaces every occurrence of the template's brand tokens (realm "saas", the
# Keycloak clients saas-backend / saas-backend-admin, the session cookie name,
# the "SaaS" display name, package names, and the docker-compose project name)
# across all git-tracked files — INCLUDING tests, so the suite stays green.
#
# Usage:
#   scripts/init-template.sh --name <slug> --display "<Display Name>" [--dry-run]
#
#   --name      machine slug: lowercase letters/digits, 2-31 chars, starts with a
#               letter. Becomes the Keycloak realm, the client-id prefix
#               (<slug>-backend / <slug>-backend-admin), the session-cookie prefix,
#               the compose project name, and the python/npm package names.
#   --display   human-facing name (e.g. "Beeline CRM"); replaces "SaaS" in titles.
#   --dry-run   show what WOULD change (files + match counts) and exit.
#
# Run this ONCE, right after cloning, from the repo root. Commit the result.
# After it runs: `make env` (regenerates SESSION_ENCRYPTION_KEYS), then `make up`.
set -euo pipefail

NAME="" ; DISPLAY="" ; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)    NAME="${2:-}"; shift 2 ;;
    --display) DISPLAY="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$NAME" ]]    || { echo "ERROR: --name is required (see --help)" >&2; exit 2; }
[[ -n "$DISPLAY" ]] || { echo "ERROR: --display is required (see --help)" >&2; exit 2; }
[[ "$NAME" =~ ^[a-z][a-z0-9]{1,30}$ ]] || {
  echo "ERROR: --name must match ^[a-z][a-z0-9]{1,30}\$ (lowercase, no spaces/underscores)" >&2
  exit 2
}
command -v git >/dev/null || { echo "ERROR: git is required (run from the cloned repo)" >&2; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "ERROR: not inside a git repo — run from the repo root after cloning" >&2; exit 2; }
cd "$(git rev-parse --show-toplevel)"

# Ordered, specific replacements (longest brand token first so prefixes don't
# clobber). Pipe-delimited "search|replace"; we use a sed delimiter that can't
# appear in the tokens.
REPLACEMENTS=(
  "saas-backend-admin|${NAME}-backend-admin"   # admin client + its dev secret + service-account user
  "saas-backend|${NAME}-backend"               # OIDC client, audience, application_name, py package
  "saas-frontend|${NAME}-frontend"             # npm package
  "saas_session|${NAME}_session"               # session cookie name
  "realms/saas|realms/${NAME}"                 # KEYCLOAK_ISSUER / PUBLIC_ISSUER URLs
  "COMPOSE_PROJECT_NAME:-saas|COMPOSE_PROJECT_NAME:-${NAME}"  # compose project default
  "\"saas\"|\"${NAME}\""                       # realm field, keycloak_realm default, reserved slug
  "SaaS|${DISPLAY}"                            # human display name in titles
)

# Files we must NOT rewrite: this script and the cloning guide both legitimately
# reference the template's original "saas" token as documentation.
EXCLUDE_REGEX='^(scripts/init-template\.sh|docs/CLONING\.md)$'

# git-tracked files only (skips .venv, node_modules, .git, build output for free).
# (while-read loop instead of mapfile for bash 3.2 / macOS compatibility.)
FILES=()
while IFS= read -r _f; do FILES+=("$_f"); done < <(git ls-files | grep -vE "$EXCLUDE_REGEX")

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY RUN — no files will be modified."
  echo "Product slug : $NAME"
  echo "Display name : $DISPLAY"
  echo
  for pair in "${REPLACEMENTS[@]}"; do
    search="${pair%%|*}"; replace="${pair#*|}"
    # count matches across tracked files (fixed-string grep)
    n=$(printf '%s\n' "${FILES[@]}" | xargs grep -F -c -- "$search" 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
    printf '  %-34s -> %-34s  (%s matches)\n' "$search" "$replace" "$n"
  done
  echo
  echo "Files that would change:"
  for pair in "${REPLACEMENTS[@]}"; do
    search="${pair%%|*}"
    printf '%s\n' "${FILES[@]}" | xargs grep -Fl -- "$search" 2>/dev/null || true
  done | sort -u | sed 's/^/  /'
  exit 0
fi

echo "Rebranding template -> '$NAME' (display: '$DISPLAY') ..."
for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || continue
  for pair in "${REPLACEMENTS[@]}"; do
    search="${pair%%|*}"; replace="${pair#*|}"
    # BSD/GNU-portable in-place edit; escape sed metacharacters in both sides.
    s_esc=$(printf '%s' "$search"  | sed 's/[&/\]/\\&/g')
    r_esc=$(printf '%s' "$replace" | sed 's/[&/\]/\\&/g')
    sed -i.bak "s/${s_esc}/${r_esc}/g" "$f" && rm -f "$f.bak"
  done
done

echo "Done. Next steps:"
echo "  1. Review the diff:        git diff --stat"
echo "  2. Fresh encryption keys:  make env   (or 'make gen-key' and update .env)"
echo "  3. Start the stack:        make up && make migrate && make seed-demo"
echo "  4. Before any deployment:  remove the seeded demo user/tenant (see docs/CLONING.md)"
echo "  5. Replace the example 'tasks' module with your own (see docs/MODULES.md)"
