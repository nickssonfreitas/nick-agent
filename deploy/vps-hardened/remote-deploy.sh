#!/usr/bin/env bash
#
# Runs ON the VPS. Swaps the pinned image digest, backs the databases up first,
# and waits for both services to report healthy.
#
# The deploy workflow pipes this in over ssh from the repo checkout
# (`ssh ... bash -s -- <args> < remote-deploy.sh`) rather than calling a copy
# that already lives on the box, so the script that runs is always the one in
# the commit being deployed — a stale copy on the VPS can never silently win.
#
# Usage:
#   remote-deploy.sh deploy   sha256:<64hex>   # roll forward
#   remote-deploy.sh rollback sha256:<64hex>   # roll back, skips the backup
#   remote-deploy.sh current                   # print the running digest
#
# Neither state.db nor kanban.db has a schema-downgrade guard, so every image
# change is a database operation. That is why `deploy` always backs up before
# touching anything, and why the rollback path is "re-pin the old digest and
# restore that backup" rather than a VPS snapshot restore. A Hostinger snapshot
# reverts the WHOLE disk, which would throw away every conversation recorded
# since the snapshot was taken — see hostinger-api.sh.

set -euo pipefail

DEPLOY_DIR="${HERMES_DEPLOY_DIR:-/opt/hermes/vps-hardened}"
BACKUP_ROOT="${HERMES_BACKUP_DIR:-/opt/hermes/backups}"
IMAGE_REPO="${HERMES_IMAGE_REPO:-nousresearch/hermes-agent}"
HEALTH_TIMEOUT="${HERMES_HEALTH_TIMEOUT:-180}"

log()  { printf '[deploy] %s\n' "$*"; }
die()  { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------

require_digest() {
  case "$1" in
    sha256:[0-9a-f]*)
      [ "${#1}" -eq 71 ] || die "malformed digest (expected sha256: + 64 hex): $1" ;;
    *)
      die "expected a digest of the form sha256:<64 hex chars>, got: $1" ;;
  esac
}

# Prints the pinned digest, or nothing if the compose file is still on the
# REPLACE_WITH_PINNED_DIGEST placeholder. Must exit 0 either way: under
# `set -e` with `pipefail`, a no-match grep here would kill the script at the
# assignment and swallow the "not pinned yet" message the caller wants to show.
current_digest() {
  { grep -oE "${IMAGE_REPO}@sha256:[0-9a-f]{64}" "$DEPLOY_DIR/docker-compose.yml" || true; } \
    | head -n 1 | sed "s|^${IMAGE_REPO}@||"
}

# Compose prefixes volume names with the project name, so the volume is
# `vps-hardened_hermes-data`, not `hermes-data`. Resolving it from the running
# container's mounts is the only form that survives a rename of the directory
# or a COMPOSE_PROJECT_NAME override.
data_volume() {
  docker inspect hermes \
    --format '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Name}}{{end}}{{end}}' \
    2>/dev/null || true
}

backup_databases() {
  local stamp vol dest
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dest="${BACKUP_ROOT}/${stamp}"
  vol="$(data_volume)"
  [ -n "$vol" ] || die "cannot resolve the hermes-data volume — is the container up?"

  mkdir -p "$dest"
  chmod 700 "$BACKUP_ROOT" "$dest"

  # Containers are already stopped by the caller, so a plain copy is a
  # consistent SQLite snapshot. -a preserves the 0600 modes; the backup holds
  # full conversation history and must stay as protected as the live database.
  docker run --rm \
    -v "${vol}:/data:ro" \
    -v "${dest}:/backup" \
    alpine:3 sh -c 'cp -a /data/state.db* /data/kanban*.db* /backup/ 2>/dev/null || true'

  if [ -z "$(ls -A "$dest" 2>/dev/null)" ]; then
    rmdir "$dest"
    die "backup produced no files — refusing to deploy over an unbacked database"
  fi
  log "backed up $(ls -1 "$dest" | wc -l) file(s) to ${dest}"
  printf '%s\n' "$dest" > "${BACKUP_ROOT}/.latest"
}

apply_digest() {
  local digest="$1"
  # Both services (gateway and dashboard) must move together — they share the
  # same volume and a split-version pair is an unsupported state.
  sed -i -E "s|${IMAGE_REPO}@sha256:[0-9a-f]{64}|${IMAGE_REPO}@${digest}|g" \
    "$DEPLOY_DIR/docker-compose.yml"
  local applied
  applied="$(current_digest)"
  [ "$applied" = "$digest" ] || die "digest rewrite did not take (file now reads: ${applied:-none})"
}

wait_healthy() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  local svc state
  log "waiting up to ${HEALTH_TIMEOUT}s for healthchecks"
  while [ "$SECONDS" -lt "$deadline" ]; do
    local all_ok=1
    for svc in hermes hermes-dashboard; do
      state="$(docker inspect "$svc" --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)"
      [ "$state" = "healthy" ] || all_ok=0
    done
    if [ "$all_ok" -eq 1 ]; then
      log "gateway and dashboard are healthy"
      return 0
    fi
    sleep 5
  done
  for svc in hermes hermes-dashboard; do
    state="$(docker inspect "$svc" --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)"
    log "  ${svc}: ${state}"
  done
  die "services did not become healthy within ${HEALTH_TIMEOUT}s"
}

bring_up() {
  # Order matters: caddy last, so TLS only starts serving something that is
  # already healthy behind it.
  docker compose up -d gateway dashboard
  wait_healthy
  docker compose up -d caddy
}

# ---------------------------------------------------------------------------

main() {
  local action="${1:-}"
  cd "$DEPLOY_DIR" 2>/dev/null || die "deploy dir not found: ${DEPLOY_DIR} (see BOOTSTRAP.md)"

  case "$action" in
    current)
      local d
      d="$(current_digest)"
      [ -n "$d" ] || die "compose file is not pinned to a digest yet — run BOOTSTRAP.md step 2"
      printf '%s\n' "$d"
      ;;

    deploy)
      local target previous
      target="${2:-}"
      require_digest "$target"
      previous="$(current_digest)"
      [ -n "$previous" ] || die "compose file is not pinned to a digest yet — run BOOTSTRAP.md step 2"

      if [ "$previous" = "$target" ]; then
        log "already running ${target}; re-applying to pick up compose/Caddyfile changes"
      fi
      log "previous=${previous}"
      log "target=${target}"

      docker compose stop
      backup_databases
      apply_digest "$target"
      bring_up
      log "deploy complete"
      ;;

    rollback)
      local target
      target="${2:-}"
      require_digest "$target"
      log "rolling back to ${target}"
      docker compose stop
      apply_digest "$target"
      bring_up
      log "rollback complete — the database was NOT restored"
      log "if the failed version migrated the schema, restore by hand from:"
      log "  $(cat "${BACKUP_ROOT}/.latest" 2>/dev/null || echo "${BACKUP_ROOT}/<timestamp>")"
      ;;

    *)
      die "usage: $0 {deploy <digest>|rollback <digest>|current}"
      ;;
  esac
}

main "$@"
