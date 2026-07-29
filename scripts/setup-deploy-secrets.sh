#!/usr/bin/env bash
#
# Reads .env.deploy and writes the VPS deploy credentials into the GitHub
# `production` environment.
#
# Why a script instead of pasting values somewhere: the credentials go from
# your disk straight to GitHub. They never enter a commit, never appear on a
# command line where `ps` could read them, and never pass through a chat
# transcript. The pipeline itself does not need them locally — GitHub Actions
# runs the deploy, so GitHub is the only place they have to exist.
#
# Usage:
#   cp deploy/vps-hardened/.env.deploy.example .env.deploy
#   $EDITOR .env.deploy && chmod 600 .env.deploy
#   scripts/setup-deploy-secrets.sh [--check]
#
#   --check   validate the file and report what would be set, without writing.

set -euo pipefail

ENV_FILE="${DEPLOY_ENV_FILE:-.env.deploy}"
ENVIRONMENT="${DEPLOY_ENVIRONMENT:-production}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

die() { printf '\033[31mERRO:\033[0m %s\n' "$*" >&2; exit 1; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
inf() { printf '  %s\n' "$*"; }

command -v gh >/dev/null 2>&1 || die "gh não encontrado — instale o GitHub CLI"
gh auth status >/dev/null 2>&1 || die "gh não autenticado — rode 'gh auth login'"
[ -f "$ENV_FILE" ] || die "$ENV_FILE não existe. Copie de deploy/vps-hardened/.env.deploy.example"

# The file holds a path to a root-equivalent key. Refuse to proceed if the
# whole world can read it.
perms="$(stat -c '%a' "$ENV_FILE")"
case "$perms" in
  600|400) ;;
  *) die "$ENV_FILE está com permissão $perms — rode: chmod 600 $ENV_FILE" ;;
esac

# Resolve the repo from git, not from a hardcoded name, and be explicit about
# it: `gh` defaults to whichever remote it likes, and on a fork that is often
# upstream. Writing deploy secrets into someone else's repository would be a
# quiet, expensive mistake.
REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
[ -n "$REPO" ] || die "não consegui resolver o repositório — rode 'gh repo set-default'"

# `.` searches PATH for a name with no slash, so a bare `.env.deploy` has to be
# qualified — but an absolute path must NOT be, or it becomes `.//tmp/...`.
case "$ENV_FILE" in
  /*|./*|../*) ENV_SRC="$ENV_FILE" ;;
  *)           ENV_SRC="./$ENV_FILE" ;;
esac
set -a
# The path is configurable (DEPLOY_ENV_FILE), so shellcheck cannot follow it.
# shellcheck disable=SC1090
. "$ENV_SRC"
set +a

missing=""
for v in VPS_SSH_HOST VPS_SSH_USER VPS_SSH_KEY_PATH VPS_SSH_KNOWN_HOSTS_PATH HERMES_DOMAIN; do
  [ -n "${!v:-}" ] || missing="${missing} ${v}"
done
[ -z "$missing" ] || die "faltando em $ENV_FILE:${missing}"

[ -f "$VPS_SSH_KEY_PATH" ] || die "chave privada não encontrada: $VPS_SSH_KEY_PATH"
[ -f "$VPS_SSH_KNOWN_HOSTS_PATH" ] || die "known_hosts não encontrado: $VPS_SSH_KNOWN_HOSTS_PATH"

grep -q 'PRIVATE KEY' "$VPS_SSH_KEY_PATH" \
  || die "$VPS_SSH_KEY_PATH não parece uma chave privada (falta o cabeçalho BEGIN ... PRIVATE KEY). Você apontou para o .pub?"
grep -q '.' "$VPS_SSH_KNOWN_HOSTS_PATH" \
  || die "$VPS_SSH_KNOWN_HOSTS_PATH está vazio — gere com: ssh-keyscan -H $VPS_SSH_HOST > $VPS_SSH_KNOWN_HOSTS_PATH"

printf '\nRepositório: \033[1m%s\033[0m\nEnvironment: \033[1m%s\033[0m\n\n' "$REPO" "$ENVIRONMENT"
inf "host=${VPS_SSH_HOST}  user=${VPS_SSH_USER}  port=${VPS_SSH_PORT:-22}"
inf "domínio=${HERMES_DOMAIN}"
inf "chave=${VPS_SSH_KEY_PATH} ($(wc -l < "$VPS_SSH_KEY_PATH") linhas, conteúdo não exibido)"
printf '\n'

if [ "$CHECK_ONLY" -eq 1 ]; then
  ok "validação passou — rode sem --check para gravar"
  exit 0
fi

gh api --method PUT "repos/${REPO}/environments/${ENVIRONMENT}" --silent >/dev/null 2>&1 \
  && ok "environment ${ENVIRONMENT} pronto" \
  || die "não consegui criar o environment ${ENVIRONMENT} (permissão de admin no repo?)"

# Values go in over stdin, never as an argv. `gh secret set NAME` with no
# --body reads the value from standard input, which keeps it out of `ps`.
set_secret() {
  local name="$1"
  gh secret set "$name" --repo "$REPO" --env "$ENVIRONMENT" >/dev/null
  ok "$name"
}

printf '%s' "$VPS_SSH_HOST"   | set_secret VPS_SSH_HOST
printf '%s' "$VPS_SSH_USER"   | set_secret VPS_SSH_USER
printf '%s' "$HERMES_DOMAIN"  | set_secret HERMES_DOMAIN
set_secret VPS_SSH_KEY         < "$VPS_SSH_KEY_PATH"
set_secret VPS_SSH_KNOWN_HOSTS < "$VPS_SSH_KNOWN_HOSTS_PATH"
if [ -n "${VPS_SSH_PORT:-}" ]; then
  printf '%s' "$VPS_SSH_PORT" | set_secret VPS_SSH_PORT
else
  inf "VPS_SSH_PORT vazio — o workflow usa 22"
fi

cat <<EOF

Pronto. Os segredos estão no environment ${ENVIRONMENT} de ${REPO}.

Eles são write-only: não dá para lê-los de volta, só sobrescrever. Por isso
${ENV_FILE} não precisa mais existir:

  shred -u ${ENV_FILE} ${VPS_SSH_KEY_PATH} 2>/dev/null || rm -f ${ENV_FILE}

(guarde a chave privada num gerenciador de segredos antes de apagar, ou gere
outra com ssh-keygen se precisar; o par no VPS continua válido)

Valide de ponta a ponta com um dry run:

  gh workflow run deploy-vps.yml --ref dev -f image_tag=sha-XXXXXXX -f dry_run=true
EOF
