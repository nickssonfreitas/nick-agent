#!/usr/bin/env bash
#
# Post-deploy verification gate for the hardened VPS bundle.
#
# This is the executable form of the "Verification checklist" in wiki/operations/vps-deployment.md, so
# the checklist has exactly one source of truth: change a check here, not in
# two places. Run it by hand before connecting personal data, and let CI run it
# as the gate that decides whether a deploy stands or gets rolled back
# (.github/workflows/deploy-vps.yml).
#
# Usage:
#   ./verify.sh public <domain>   # checks 1-3, must run from OUTSIDE the VPS
#   ./verify.sh local             # checks 4-8, must run ON the VPS
#   ./verify.sh all <domain>      # both, only meaningful on the VPS itself
#
# The public checks belong on an external host on purpose: checks 1 and 2 model
# an anonymous internet client, which is the actual threat. Running them from
# the VPS still catches a misconfigured Caddy, but it cannot catch a firewall
# that leaks a port to the internet.
#
# Exit status: 0 if every HARD check passed, 1 otherwise. Soft checks report
# WARN and never fail the run — see the classification note below.
#
#   HARD (deploy is unsafe if these fail)
#     1  auth gate engaged            5  image pinned by digest + limits set
#     2  session token not leaked     6  credential file is owner-only
#     4  no app port published
#
#   SOFT (deploy is defensible, but finish the setup)
#     3  security headers             7  retention configured
#     8  MCP sampling disabled
#
#   Checks 3, 7 and 8 are soft because each has a legitimate "not yet" state:
#   the dashboard CSP is deliberately Report-Only until a clean cycle
#   (wiki/operations/vps-deployment.md step 7 / HERMES_CSP_ENFORCE), and 7 and 8 depend on a
#   config-snippet.yaml merge whose mcp_servers block you are told to hand-edit
#   for your actual servers. Failing a deploy on those would train you to pass
#   --force, which is how the hard checks stop getting read.

set -euo pipefail

HARD_FAILURES=0
SOFT_FAILURES=0

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; SOFT_FAILURES=$((SOFT_FAILURES + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; HARD_FAILURES=$((HARD_FAILURES + 1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# Public checks — what an anonymous internet client sees.
# ---------------------------------------------------------------------------

check_public() {
  local domain="$1"
  local base="https://${domain}"

  head_ "Public surface (${base})"

  # 1. The auth gate is ENGAGED. /api/status is auth-exempt by design
  #    (public_paths.py), which is exactly why it can report this safely.
  local status auth_required
  if ! status="$(curl -fsS --max-time 15 "${base}/api/status" 2>/dev/null)"; then
    fail "1. /api/status is unreachable — cannot confirm the auth gate"
  else
    auth_required="$(printf '%s' "$status" | grep -oE '"auth_required"[[:space:]]*:[[:space:]]*(true|false)' | grep -oE '(true|false)' || true)"
    case "$auth_required" in
      true)  pass "1. auth_required = true" ;;
      false) fail "1. auth_required = FALSE — the dashboard is open to the internet" ;;
      *)     fail "1. auth_required missing from /api/status response" ;;
    esac
  fi

  # 2. The session token must never be embedded in the anonymous index.html.
  #    This is finding R1: a rewritten Host header drops the app into loopback
  #    mode and it hands the token to whoever asked.
  local leaked
  leaked="$(curl -fsS --max-time 15 "${base}/" 2>/dev/null | grep -c '__HERMES_SESSION_TOKEN__' || true)"
  if [ "${leaked:-0}" -eq 0 ]; then
    pass "2. no session token in the anonymous response"
  else
    fail "2. SESSION TOKEN LEAKED to anonymous clients (${leaked} occurrence(s))"
  fi

  # 3. Transport and framing headers.
  local headers
  headers="$(curl -fsSI --max-time 15 "${base}/api/status" 2>/dev/null || true)"
  local missing=""
  printf '%s' "$headers" | grep -qi 'strict-transport-security' || missing="${missing} HSTS"
  printf '%s' "$headers" | grep -qi 'content-security-policy'   || missing="${missing} CSP"
  printf '%s' "$headers" | grep -qi 'x-frame-options'           || missing="${missing} X-Frame-Options"
  if [ -z "$missing" ]; then
    pass "3. security headers present"
  else
    warn "3. missing security header(s):${missing}"
  fi
}

# ---------------------------------------------------------------------------
# Local checks — container posture, requires docker on this host.
# ---------------------------------------------------------------------------

check_local() {
  local deploy_dir="${HERMES_DEPLOY_DIR:-/opt/hermes/vps-hardened}"

  head_ "Container posture (${deploy_dir})"

  if ! command -v docker >/dev/null 2>&1; then
    fail "docker not found — run the local checks ON the VPS"
    return
  fi
  if ! cd "$deploy_dir" 2>/dev/null; then
    fail "deploy dir not found: ${deploy_dir} (set HERMES_DEPLOY_DIR, or see wiki/operations/vps-bootstrap.md step 1)"
    return
  fi

  # 4. Caddy is the ONLY service allowed to publish a host port.
  local offenders=""
  local svc ports
  while read -r svc ports; do
    [ -n "$svc" ] || continue
    [ "$svc" = "caddy" ] && continue
    case "$ports" in
      *'->'*) offenders="${offenders} ${svc}(${ports})" ;;
    esac
  done < <(docker compose ps --format '{{.Service}} {{.Ports}}' 2>/dev/null)
  if [ -z "$offenders" ]; then
    pass "4. no app port published to the host"
  else
    fail "4. service(s) publishing host ports:${offenders}"
  fi

  # 5. Immutable image reference and a memory ceiling on the agent container.
  local image mem
  image="$(docker inspect hermes --format '{{.Config.Image}}' 2>/dev/null || true)"
  mem="$(docker inspect hermes --format '{{.HostConfig.Memory}}' 2>/dev/null || echo 0)"
  case "$image" in
    *@sha256:*) pass "5a. image pinned by digest (${image##*@})" ;;
    "")         fail "5a. container 'hermes' not running" ;;
    *)          fail "5a. image is NOT pinned by digest: ${image}" ;;
  esac
  if [ "${mem:-0}" -gt 0 ] 2>/dev/null; then
    pass "5b. memory limit set ($((mem / 1024 / 1024))MiB)"
  else
    fail "5b. no memory limit on the hermes container"
  fi

  # 6. Provider credentials must be owner-only. `hermes login` writes them and
  #    the clamp keeps them 0600 across rewrites — this proves the volume is a
  #    POSIX filesystem that actually honours chmod.
  local perms
  perms="$(docker exec hermes stat -c '%a %U' /opt/data/.env 2>/dev/null || true)"
  case "$perms" in
    "600 hermes") pass "6. /opt/data/.env is 600 hermes" ;;
    "")           fail "6. /opt/data/.env missing — run 'hermes login' (wiki/operations/vps-bootstrap.md step 5)" ;;
    *)            fail "6. /opt/data/.env has wrong ownership/permissions: ${perms}" ;;
  esac

  # 7. Retention keeps conversation history from growing without bound.
  if docker exec hermes sh -lc 'grep -q "^sessions:" /opt/data/config.yaml' 2>/dev/null; then
    pass "7. retention block present in config.yaml"
  else
    warn "7. no 'sessions:' retention block — merge config-snippet.yaml (wiki/operations/vps-bootstrap.md step 6)"
  fi

  # 8. An MCP server with sampling enabled can drive the model on its own.
  local mcp_block
  mcp_block="$(docker exec hermes sh -lc 'sed -n "/^mcp_servers:/,/^[a-z]/p" /opt/data/config.yaml' 2>/dev/null || true)"
  if [ -z "$mcp_block" ]; then
    warn "8. no mcp_servers block configured (nothing to check)"
  elif printf '%s' "$mcp_block" | grep -qE 'sampling:[[:space:]]*$|enabled:[[:space:]]*true'; then
    warn "8. review mcp_servers — sampling may be enabled on a server"
  else
    pass "8. no MCP server has sampling enabled"
  fi
}

# ---------------------------------------------------------------------------

main() {
  local mode="${1:-all}"
  case "$mode" in
    public)
      [ $# -ge 2 ] || { echo "usage: $0 public <domain>" >&2; exit 2; }
      check_public "$2"
      ;;
    local)
      check_local
      ;;
    all)
      [ $# -ge 2 ] || { echo "usage: $0 all <domain>" >&2; exit 2; }
      check_public "$2"
      check_local
      ;;
    *)
      echo "usage: $0 {public <domain>|local|all <domain>}" >&2
      exit 2
      ;;
  esac

  head_ "Result"
  if [ "$HARD_FAILURES" -gt 0 ]; then
    printf '  \033[31m%d hard failure(s)\033[0m, %d warning(s) — DO NOT connect personal data.\n' \
      "$HARD_FAILURES" "$SOFT_FAILURES"
    printf '  A failing check 1 or 2 means the dashboard is reachable without auth.\n'
    printf '  Usual cause: a `header_up Host` line in the Caddyfile, or the dashboard\n'
    printf '  bound to 127.0.0.1. Fix that before going further.\n'
    exit 1
  fi
  printf '  \033[32mall hard checks passed\033[0m, %d warning(s).\n' "$SOFT_FAILURES"
}

main "$@"
