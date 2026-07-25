#!/usr/bin/env bash
#
# Thin wrapper over the Hostinger VPS REST API for disaster recovery.
#
# Why a script and not the MCP server: the Hostinger MCP server is an *agent*
# surface (stdio/HTTP transport for Claude, Cursor, and friends). It is not
# something a CI job can call. Both talk to the same REST API underneath, so
# this script is the CI-side twin of the MCP's VPS_* tools:
#
#   snapshot-status   <- VPS_getSnapshotV1        GET  .../snapshot
#   snapshot-create   <- VPS_createSnapshotV1     POST .../snapshot
#   snapshot-restore  <- VPS_restoreSnapshotV1    POST .../snapshot/restore
#   vms               <- VPS_getVirtualMachinesV1 GET  /api/vps/v1/virtual-machines
#   actions           <- VPS_getActionsV1         GET  .../actions
#
# ─── Read this before wiring snapshots into a pipeline ───────────────────────
#
# Hostinger keeps exactly ONE snapshot per VM. The API documents it plainly:
# "Creating new snapshot will overwrite the existing snapshot!" So a snapshot
# taken automatically on every deploy destroys the previous one, which means
# the safety net only ever covers the most recent deploy — and it silently
# stops covering the one you actually wanted.
#
# Worse, a restore reverts the entire disk. Hermes stores conversations in
# plaintext SQLite on that disk, so restoring a snapshot taken before a deploy
# throws away every conversation recorded since. That is data loss, not
# rollback.
#
# Hence the split the deploy workflow uses:
#
#   routine rollback   -> re-pin the previous digest + the pre-deploy DB backup
#                         (remote-deploy.sh rollback). Surgical, no data loss.
#   snapshot restore   -> disaster recovery only, run by a human who has
#                         accepted losing everything since the snapshot.
#
# Auth: export HOSTINGER_API_TOKEN. The token is account-wide — it also reaches
# DNS, domains, billing and the delete endpoints — so treat it as a root
# credential, keep it out of the repo, and prefer a short-lived CI secret over
# leaving it on a developer laptop.

set -euo pipefail

API_BASE="${HOSTINGER_API_BASE:-https://developers.hostinger.com}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_token() {
  [ -n "${HOSTINGER_API_TOKEN:-}" ] || die "HOSTINGER_API_TOKEN is not set"
}

require_vm() {
  [ -n "${1:-}" ] || die "virtual machine id is required (find it with: $0 vms)"
  case "$1" in
    ''|*[!0-9]*) die "virtual machine id must be numeric, got: $1" ;;
  esac
}

# The token goes through a header file rather than -H on the command line so it
# cannot surface in `ps`, shell traces, or curl's own error output.
api() {
  local method="$1" path="$2"
  local hdr
  hdr="$(mktemp)"
  # shellcheck disable=SC2064  # expand $hdr now, at trap-set time
  trap "rm -f '$hdr'" RETURN
  printf 'Authorization: Bearer %s\n' "$HOSTINGER_API_TOKEN" > "$hdr"
  chmod 600 "$hdr"

  curl -fsS --max-time 60 \
    -X "$method" \
    -H @"$hdr" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    "${API_BASE}${path}"
}

pretty() {
  if command -v jq >/dev/null 2>&1; then jq .; else cat; fi
}

confirm() {
  local prompt="$1" word="$2" answer
  [ -t 0 ] || die "${prompt} — refusing to run non-interactively"
  printf '%s\nType %s to proceed: ' "$prompt" "$word"
  read -r answer
  [ "$answer" = "$word" ] || die "aborted"
}

# ---------------------------------------------------------------------------

main() {
  require_token
  local cmd="${1:-}"

  case "$cmd" in
    vms)
      api GET "/api/vps/v1/virtual-machines" | pretty
      ;;

    details)
      require_vm "${2:-}"
      api GET "/api/vps/v1/virtual-machines/$2" | pretty
      ;;

    actions)
      require_vm "${2:-}"
      api GET "/api/vps/v1/virtual-machines/$2/actions" | pretty
      ;;

    snapshot-status)
      require_vm "${2:-}"
      api GET "/api/vps/v1/virtual-machines/$2/snapshot" | pretty
      ;;

    snapshot-create)
      require_vm "${2:-}"
      confirm "This OVERWRITES the single existing snapshot for VM $2. The snapshot it replaces is gone." "OVERWRITE"
      api POST "/api/vps/v1/virtual-machines/$2/snapshot" | pretty
      ;;

    snapshot-restore)
      require_vm "${2:-}"
      confirm "This reverts the ENTIRE DISK of VM $2. Every conversation recorded since the snapshot is lost. For a routine bad deploy use 'remote-deploy.sh rollback' instead." "RESTORE"
      api POST "/api/vps/v1/virtual-machines/$2/snapshot/restore" | pretty
      ;;

    *)
      cat >&2 <<'USAGE'
usage: hostinger-api.sh <command> [vm_id]

  vms                          list virtual machines (to find the id)
  details          <vm_id>     full configuration and status
  actions          <vm_id>     operation history, for troubleshooting
  snapshot-status  <vm_id>     show the current snapshot, if any
  snapshot-create  <vm_id>     take a snapshot   (OVERWRITES the existing one)
  snapshot-restore <vm_id>     restore snapshot  (REVERTS THE WHOLE DISK)

Requires HOSTINGER_API_TOKEN. Both mutating commands ask for confirmation and
refuse to run non-interactively — that is deliberate, see the header comment.
USAGE
      exit 2
      ;;
  esac
}

main "$@"
