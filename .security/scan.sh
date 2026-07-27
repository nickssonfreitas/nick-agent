#!/usr/bin/env bash
# Executa uma auditoria de segurança no repositório atual.
# Este script NÃO instala ferramentas e NÃO altera/corrige o código.
#
# Pré-requisito:
#   ./.security/install.sh
#
# Uso básico:
#   ./.security/scan.sh
#
# Saída:
#   .security/reports/<data-hora>/
#   .security/reports/latest -> relatório mais recente
#
# Variáveis opcionais:
#   FAIL_ON_FINDINGS=1          Retorna exit code 1 se houver findings (padrão: 1)
#   RUN_CODEQL=1                Executa CodeQL quando instalado (padrão: 1)
#   RUN_SHELLCHECK=1            Analisa scripts shell (padrão: 1)
#   SEMGREP_CONFIG=auto         Ruleset Semgrep (padrão: auto)
#   CONTAINER_IMAGE=nome:tag    Também analisa uma imagem já construída com Trivy
#   ZAP_TARGET_URL=https://...  Executa apenas o ZAP Baseline/passivo contra essa URL
#
# Códigos de saída:
#   0 = execução válida e nenhum finding, ou FAIL_ON_FINDINGS=0
#   1 = um ou mais findings foram encontrados
#   2 = uma ou mais ferramentas falharam ou produziram evidência inválida

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly ENV_FILE="${SCRIPT_DIR}/env.sh"
readonly BIN_DIR="${SCRIPT_DIR}/bin"
readonly LOG_DIR="${SCRIPT_DIR}/logs"
readonly REPORTS_ROOT="${SCRIPT_DIR}/reports"
readonly RUN_ID="$(date -u +'%Y%m%dT%H%M%SZ')"
readonly REPORT_DIR="${REPORTS_ROOT}/${RUN_ID}"
readonly LOG_FILE="${LOG_DIR}/scan-${RUN_ID}.log"
readonly RESULTS_JSONL="${REPORT_DIR}/results.jsonl"
readonly SUMMARY_JSON="${REPORT_DIR}/summary.json"
readonly SUMMARY_MD="${REPORT_DIR}/SUMMARY.md"
readonly TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/nick-agent-security-scan.XXXXXX")"
readonly SOURCE_SNAPSHOT="${TEMP_ROOT}/source"

readonly FAIL_ON_FINDINGS="${FAIL_ON_FINDINGS:-1}"
readonly RUN_CODEQL="${RUN_CODEQL:-1}"
readonly RUN_SHELLCHECK="${RUN_SHELLCHECK:-1}"
# 'auto' exige --metrics=on (envia dados do projeto ao registry para escolher
# regras). Mantemos as metricas desligadas e fixamos um ruleset concreto.
readonly SEMGREP_CONFIG="${SEMGREP_CONFIG:-p/default}"
readonly ZAP_IMAGE="${ZAP_IMAGE:-ghcr.io/zaproxy/zaproxy:stable}"
readonly CONTAINER_IMAGE="${CONTAINER_IMAGE:-}"
readonly ZAP_TARGET_URL="${ZAP_TARGET_URL:-}"

# Cache local ao projeto. O default do Trivy (~/.cache/trivy) e compartilhado
# com qualquer outra invocacao da maquina: um unico `sudo trivy` deixa
# db/trivy.db como root:root e todo scan seguinte morre com
# "permission denied", marcando a auditoria inteira como invalida. Manter o
# cache sob .security/ (ja coberto por .gitignore) torna o scan hermetico.
readonly CACHE_DIR="${SCRIPT_DIR}/cache"
export TRIVY_CACHE_DIR="${TRIVY_CACHE_DIR:-${CACHE_DIR}/trivy}"

# Estende o ruleset padrao do Gitleaks com allowlists de caminho (docs de
# terceiros vendorados). Nenhuma regra e afrouxada — ver o proprio arquivo.
readonly GITLEAKS_CONFIG="${GITLEAKS_CONFIG:-${PROJECT_ROOT}/.gitleaks.toml}"

mkdir -p -- "${LOG_DIR}" "${REPORT_DIR}" "${SOURCE_SNAPSHOT}" "${TRIVY_CACHE_DIR}"
touch -- "${LOG_FILE}" "${RESULTS_JSONL}"
chmod 600 -- "${LOG_FILE}" "${RESULTS_JSONL}"
exec > >(tee -a "${LOG_FILE}") 2>&1

FINDING_TOOLS=0
ERROR_TOOLS=0
CLEAN_TOOLS=0
SKIPPED_TOOLS=0
TOTAL_FINDINGS=0

now() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '[%s] [INFO] %s\n' "$(now)" "$*"; }
warn() { printf '[%s] [WARN] %s\n' "$(now)" "$*" >&2; }
die() { printf '[%s] [ERRO] %s\n' "$(now)" "$*" >&2; exit 2; }
section() { printf '\n[%s] ===== %s =====\n' "$(now)" "$*"; }
command_exists() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
  local rc=$?
  rm -rf -- "${TEMP_ROOT}"
  if (( rc == 2 )); then
    printf '[%s] [ERRO] Scan encerrado com erro. Consulte %s\n' "$(now)" "${LOG_FILE}" >&2
  fi
  exit "${rc}"
}
trap cleanup EXIT
trap 'die "Sinal recebido; scan interrompido."' INT TERM

require_bool() {
  local name="$1" value="$2"
  [[ "${value}" == "0" || "${value}" == "1" ]] || die "${name} deve ser 0 ou 1."
}

record_result() {
  local tool="$1" status="$2" count="$3" rc="$4" report="$5" note="$6"

  case "${status}" in
    findings)
      ((FINDING_TOOLS+=1))
      ((TOTAL_FINDINGS+=count))
      ;;
    error) ((ERROR_TOOLS+=1)) ;;
    clean) ((CLEAN_TOOLS+=1)) ;;
    skipped) ((SKIPPED_TOOLS+=1)) ;;
    *) die "Status interno inválido: ${status}." ;;
  esac

  jq -nc \
    --arg tool "${tool}" \
    --arg status "${status}" \
    --argjson count "${count}" \
    --argjson exit_code "${rc}" \
    --arg report "${report}" \
    --arg note "${note}" \
    '{tool:$tool,status:$status,count:$count,exit_code:$exit_code,report:$report,note:$note}' \
    >> "${RESULTS_JSONL}"

  printf '[%s] [%s] %s — findings=%s, exit=%s%s\n' \
    "$(now)" "${status^^}" "${tool}" "${count}" "${rc}" \
    "${note:+ — ${note}}"
}

run_command() {
  local stdout_file="$1" stderr_file="$2"
  shift 2
  local rc
  set +e
  "$@" >"${stdout_file}" 2>"${stderr_file}"
  rc=$?
  set -e
  return "${rc}"
}

classify_json_report() {
  local tool="$1" report="$2" rc="$3" jq_expression="$4" note="${5:-}"
  local count

  if [[ ! -s "${report}" ]]; then
    record_result "${tool}" error 0 "${rc}" "${report#${PROJECT_ROOT}/}" \
      "Relatório ausente ou vazio. ${note}"
    return
  fi

  if ! jq -e . "${report}" >/dev/null 2>&1; then
    record_result "${tool}" error 0 "${rc}" "${report#${PROJECT_ROOT}/}" \
      "Relatório JSON inválido. ${note}"
    return
  fi

  if ! count="$(jq -er "${jq_expression}" "${report}" 2>/dev/null)"; then
    record_result "${tool}" error 0 "${rc}" "${report#${PROJECT_ROOT}/}" \
      "Não foi possível interpretar o relatório. ${note}"
    return
  fi

  [[ "${count}" =~ ^[0-9]+$ ]] || {
    record_result "${tool}" error 0 "${rc}" "${report#${PROJECT_ROOT}/}" \
      "Contagem inválida no relatório. ${note}"
    return
  }

  if (( count > 0 )); then
    record_result "${tool}" findings "${count}" "${rc}" "${report#${PROJECT_ROOT}/}" "${note}"
  elif (( rc == 0 )); then
    record_result "${tool}" clean 0 "${rc}" "${report#${PROJECT_ROOT}/}" "${note}"
  else
    record_result "${tool}" error 0 "${rc}" "${report#${PROJECT_ROOT}/}" \
      "A ferramenta falhou sem registrar findings. ${note}"
  fi
}

require_tools() {
  section "Validação do ambiente"

  [[ -f "${ENV_FILE}" ]] || die "${ENV_FILE} não existe. Execute primeiro ./.security/install.sh."
  # shellcheck disable=SC1090
  source "${ENV_FILE}"

  local required=(jq git semgrep bandit pip-audit checkov gitleaks osv-scanner trivy syft npm)
  local missing=() cmd
  for cmd in "${required[@]}"; do
    command_exists "${cmd}" || missing+=("${cmd}")
  done
  ((${#missing[@]} == 0)) || die "Ferramentas ausentes: ${missing[*]}. Execute novamente ./.security/install.sh."

  [[ -d "${PROJECT_ROOT}/.git" ]] || die "Execute este script dentro da raiz de um clone Git."
  [[ -f "${PROJECT_ROOT}/pyproject.toml" ]] || warn "pyproject.toml não encontrado; pip-audit poderá ser ignorado."

  log "Projeto: ${PROJECT_ROOT}"
  log "Commit: $(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
  log "Branch: $(git -C "${PROJECT_ROOT}" branch --show-current || true)"
  log "Relatórios: ${REPORT_DIR}"
}

write_metadata() {
  local dirty=false
  git -C "${PROJECT_ROOT}" diff --quiet --ignore-submodules -- && \
    git -C "${PROJECT_ROOT}" diff --cached --quiet --ignore-submodules -- || dirty=true

  jq -n \
    --arg generated_at "$(now)" \
    --arg project_root "${PROJECT_ROOT}" \
    --arg commit "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)" \
    --arg branch "$(git -C "${PROJECT_ROOT}" branch --show-current || true)" \
    --arg remote "$(git -C "${PROJECT_ROOT}" remote get-url origin 2>/dev/null || true)" \
    --argjson dirty "${dirty}" \
    '{generated_at:$generated_at,project_root:$project_root,commit:$commit,branch:$branch,remote:$remote,dirty:$dirty}' \
    > "${REPORT_DIR}/metadata.json"
}

snapshot_source() {
  section "Snapshot seguro do código"
  local file source target copied=0

  while IFS= read -r -d '' file; do
    case "${file}" in
      .git/*|.security/bin/*|.security/tools/*|.security/logs/*|.security/reports/*|\
      node_modules/*|*/node_modules/*|.venv/*|*/.venv/*|venv/*|*/venv/*|\
      dist/*|*/dist/*|build/*|*/build/*|__pycache__/*|*/__pycache__/*)
        continue
        ;;
    esac

    source="${PROJECT_ROOT}/${file}"
    target="${SOURCE_SNAPSHOT}/${file}"
    [[ -f "${source}" && ! -L "${source}" ]] || continue
    mkdir -p -- "$(dirname -- "${target}")"
    cp -p -- "${source}" "${target}"
    ((copied+=1))
  done < <(git -C "${PROJECT_ROOT}" ls-files -co --exclude-standard -z)

  (( copied > 0 )) || die "Nenhum arquivo foi incluído no snapshot."
  log "Snapshot criado com ${copied} arquivos rastreados/não ignorados."
}

scan_gitleaks_git() {
  section "Gitleaks — histórico Git"
  local report="${REPORT_DIR}/gitleaks-git.json" stderr="${REPORT_DIR}/gitleaks-git.stderr.log" rc
  set +e
  (cd "${PROJECT_ROOT}" && gitleaks git --redact=100 --config "${GITLEAKS_CONFIG}" \
    --report-format json --report-path "${report}" .) \
    >"${REPORT_DIR}/gitleaks-git.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report gitleaks-git "${report}" "${rc}" 'length' "Histórico completo do Git."
}

scan_gitleaks_worktree() {
  section "Gitleaks — arquivos atuais"
  local report="${REPORT_DIR}/gitleaks-worktree.json" stderr="${REPORT_DIR}/gitleaks-worktree.stderr.log" rc
  set +e
  gitleaks dir --redact=100 --config "${GITLEAKS_CONFIG}" \
    --report-format json --report-path "${report}" "${SOURCE_SNAPSHOT}" \
    >"${REPORT_DIR}/gitleaks-worktree.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report gitleaks-worktree "${report}" "${rc}" 'length' "Inclui mudanças locais não ignoradas."
}

scan_semgrep() {
  section "Semgrep — análise estática"
  local report="${REPORT_DIR}/semgrep.json" stderr="${REPORT_DIR}/semgrep.stderr.log" rc
  # Mesmo modelo de ameaca do Bandit: codigo de teste nao e distribuido, entao
  # fica fora do escopo. Sem estas exclusoes o semgrep divergia do bandit e
  # media coisa diferente no mesmo repo: 38 dos 71 detect-insecure-websocket
  # eram ws://localhost em arquivo de teste, mais 20 findings so em
  # ui-tui/src/__tests__. Os globs cobrem as suites JS/TS que nao vivem em um
  # diretorio tests/ (ex.: apps/desktop/src/lib/gateway-ws-url.test.ts).
  #
  # Exclusoes por REGRA (nao por caminho). As duas abaixo produziram 180
  # findings e zero verdadeiros positivos na triagem de 2026-07-24, e sao
  # estruturalmente incompativeis com este codebase, nao acidentalmente
  # ruidosas — ver SEMGREP-TRIAGE_2026_07_24.md secoes 4 e 5:
  #
  #  - sqlalchemy-execute-raw-query (97): interpolacao de IDENTIFICADOR, onde
  #    parametro SQL nao existe por definicao. PRAGMA, REINDEX com escape de
  #    aspas correto, e nomes de tabela vindos da constante _REBUILD_SPECS.
  #    Os valores sempre passam por binding.
  #  - dynamic-urllib-use-detected (83): todo cliente de API tem URL nao
  #    literal. Os caminhos que recebem input nao-confiavel ja sao cobertos
  #    por tools/url_safety.py (bloqueia faixas privadas e metadata de cloud),
  #    consumido por 19 modulos incluindo web_tools, browser_tool e os
  #    adapters de plataforma.
  #
  # python-logger-credential-disclosure (146, tambem 100% falso-positivo)
  # fica ATIVA de proposito: e a unica das tres cujo custo de falhar e alto,
  # e revisar strings de mensagem de log e barato.
  local semgrep_excluded_rules=(
    --exclude-rule python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    --exclude-rule python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
  )
  set +e
  (cd "${PROJECT_ROOT}" && semgrep scan \
    --config "${SEMGREP_CONFIG}" \
    --metrics=off \
    --disable-version-check \
    --json \
    --output "${report}" \
    --exclude .git \
    --exclude .security/bin \
    --exclude .security/tools \
    --exclude .security/logs \
    --exclude .security/reports \
    --exclude node_modules \
    --exclude .venv \
    --exclude venv \
    --exclude dist \
    --exclude build \
    --exclude tests \
    --exclude test \
    --exclude __tests__ \
    --exclude '*.test.ts' \
    --exclude '*.test.tsx' \
    --exclude '*.test.js' \
    --exclude '*.test.mjs' \
    --exclude 'test_*.py' \
    --exclude '*_test.py' \
    "${semgrep_excluded_rules[@]}" \
    .) >"${REPORT_DIR}/semgrep.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report semgrep "${report}" "${rc}" '(.results // []) | length' \
    "Ruleset: ${SEMGREP_CONFIG}. Código de produção; testes excluídos."
}

scan_bandit() {
  section "Bandit — segurança Python"
  local report="${REPORT_DIR}/bandit.json" stderr="${REPORT_DIR}/bandit.stderr.log" rc
  # Alvo e exclusoes em caminho absoluto. Com 'bandit -r .' os caminhos saem como
  # './venv/...' e um -x 'venv' nao casa pelo prefixo './', deixando venv/,
  # .security/ (venvs dos proprios scanners) e node_modules/ serem varridos.
  local skip="${PROJECT_ROOT}/.git,${PROJECT_ROOT}/.security,${PROJECT_ROOT}/node_modules"
  skip+=",${PROJECT_ROOT}/.venv,${PROJECT_ROOT}/venv,${PROJECT_ROOT}/dist,${PROJECT_ROOT}/build"
  # tests/ fica fora: o modelo de ameaca do Bandit e codigo que roda em
  # producao, e codigo de teste nao e distribuido. Sem esta exclusao, B101
  # (assert_used) sozinho gerava 85.442 dos 93.396 findings — 91% do relatorio
  # era `assert` em teste, que e exatamente o uso esperado. Achados em tests/
  # eram 90.126 no total, contra 3.270 em codigo de producao.
  # Os globs cobrem suites aninhadas (ex.: skills/creative/comfyui/**/tests/),
  # que o caminho absoluto de tests/ na raiz nao alcanca.
  skip+=",${PROJECT_ROOT}/tests,*/tests/*,*/test/*"
  set +e
  bandit -r "${PROJECT_ROOT}" -x "${skip}" \
    -f json -o "${report}" >"${REPORT_DIR}/bandit.stdout.log" 2>"${stderr}"
  rc=$?
  set -e

  # A contagem publicada cobre apenas MEDIUM+HIGH. O JSON continua completo
  # (LOW incluso) porque a evidencia bruta e o que sustenta a auditoria; o que
  # muda e o numero de manchete, que antes era dominado por heuristica LOW:
  # B110 (try/except/pass) sozinho dava 1.613 dos 3.273 findings, e B105
  # (nome de constante parecendo senha) mais 303. Nenhum dos dois indica
  # vulnerabilidade sem confirmacao no codigo.
  local low=0
  if [[ -s "${report}" ]] && jq -e . "${report}" >/dev/null 2>&1; then
    low="$(jq -r '[(.results // [])[] | select(.issue_severity == "LOW")] | length' "${report}")"
  fi

  # Bandit sai 1 quando acha qualquer issue, inclusive LOW. Como a expressao de
  # contagem agora decide sozinha entre findings e clean, um rc=1 acompanhado de
  # zero MEDIUM+HIGH seria classificado como 'error' e invalidaria a auditoria.
  # Normalizamos so esse caso; rc >= 2 e falha real da ferramenta e permanece.
  (( rc == 1 )) && rc=0

  classify_json_report bandit "${report}" "${rc}" \
    '[(.results // [])[] | select(.issue_severity != "LOW")] | length' \
    "Código Python de produção, severidade MEDIUM+. ${low} finding(s) LOW no relatório, fora da contagem."
}

scan_pip_audit() {
  section "pip-audit — dependências Python"
  local report="${REPORT_DIR}/pip-audit.json" stderr="${REPORT_DIR}/pip-audit.stderr.log" rc
  local args=(--progress-spinner off --format json --output "${report}")
  local mode

  # --locked exige um pylock.toml (PEP 751). Usa-lo sem esse arquivo faz a
  # ferramenta abortar e, via ERROR_TOOLS, invalidar a auditoria inteira.
  if [[ -f "${PROJECT_ROOT}/pylock.toml" ]]; then
    args+=(--locked --strict "${PROJECT_ROOT}")
    mode="pylock.toml via --locked"
  elif [[ -f "${PROJECT_ROOT}/uv.lock" ]] && command_exists uv; then
    # Auditar `pyproject.toml` direto resolve so as dependencias base e ignora
    # os extras, que e onde mora quase tudo: o caminho de instalacao que a doc
    # manda usar e `.[all,dev]`, com 41 extras. Na medicao de 2026-07-25 isso
    # significava auditar 59 pacotes e reportar 'clean', enquanto o conjunto
    # travado com todos os extras tem 227 pacotes e 3 vulnerabilidades
    # (pynacl 1.5.0, setuptools 81.0.0) — que so apareciam porque o
    # osv-scanner le o uv.lock por conta propria. Um 'clean' cobrindo um
    # quarto da superficie instalada e pior que nenhum relatorio.
    local lock_req="${TEMP_ROOT}/uv-lock-requirements.txt"
    if uv export --directory "${PROJECT_ROOT}" --format requirements-txt \
         --all-extras --no-emit-project -o "${lock_req}" >/dev/null 2>>"${stderr}"; then
      args+=(--requirement "${lock_req}")
      mode="uv.lock via uv export --all-extras"
    else
      # Fallback silencioso invalidaria o relatorio: o modo fica registrado na
      # coluna Observacao para que o recorte reduzido seja visivel.
      args+=("${PROJECT_ROOT}")
      mode="pyproject.toml (uv export falhou; extras fora do escopo)"
    fi
  elif [[ -f "${PROJECT_ROOT}/requirements.txt" ]]; then
    args+=(--requirement "${PROJECT_ROOT}/requirements.txt")
    mode="requirements.txt"
  elif [[ -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
    # Sem --strict: dependencia pulada nao deve derrubar a auditoria toda.
    args+=("${PROJECT_ROOT}")
    mode="pyproject.toml com resolucao de dependencias"
  else
    record_result pip-audit skipped 0 0 "" "Nenhum manifesto Python reconhecido."
    return
  fi

  set +e
  pip-audit "${args[@]}" >"${REPORT_DIR}/pip-audit.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report pip-audit "${report}" "${rc}" \
    'if type == "array" then ([.[].vulns[]?] | length) else ([.dependencies[]?.vulns[]?] | length) end' \
    "Fonte: ${mode}."
}

scan_npm_audit() {
  section "npm audit — dependências JavaScript"
  local report="${REPORT_DIR}/npm-audit.json" stderr="${REPORT_DIR}/npm-audit.stderr.log" rc
  local locks=() lock dir part parts_dir sources worst_rc=0 idx=0

  # Audita TODO package-lock.json rastreado, nao so o da raiz. O repo tem 4
  # (raiz, website/, plugins/platforms/photon/sidecar/, scripts/whatsapp-bridge/)
  # e o OSV encontrou vulnerabilidade em todos. Auditar apenas a raiz deixava o
  # website/ — que e publicado — sem cobertura de npm audit: as 16
  # vulnerabilidades dele, incluindo uma critica (websocket-driver), so
  # apareciam via OSV.
  while IFS= read -r -d '' lock; do
    [[ "${lock}" == *node_modules/* ]] && continue
    locks+=("${lock}")
  done < <(git -C "${PROJECT_ROOT}" ls-files -z '*package-lock.json')

  if ((${#locks[@]} == 0)); then
    record_result npm-audit skipped 0 0 "" "Nenhum package-lock.json rastreado."
    return
  fi

  parts_dir="${TEMP_ROOT}/npm-audit"
  sources="${parts_dir}/sources.jsonl"
  mkdir -p -- "${parts_dir}"
  : >"${sources}"
  : >"${stderr}"

  for lock in "${locks[@]}"; do
    dir="$(dirname -- "${PROJECT_ROOT}/${lock}")"
    part="${parts_dir}/part-${idx}.json"
    idx=$((idx + 1))
    printf '=== %s ===\n' "${lock}" >>"${stderr}"
    set +e
    (cd "${dir}" && npm audit --json --package-lock-only --audit-level=low) \
      >"${part}" 2>>"${stderr}"
    rc=$?
    set -e
    if (( rc > worst_rc )); then worst_rc="${rc}"; fi
    # Um lockfile ilegivel nao pode virar "0 findings": entra como report null
    # e forca rc!=0, para classify_json_report marcar erro em vez de clean.
    if jq -e . "${part}" >/dev/null 2>&1; then
      jq -c --arg path "${lock}" --argjson exit_code "${rc}" \
        '{path:$path, exit_code:$exit_code, report:.}' "${part}" >>"${sources}"
    else
      jq -nc --arg path "${lock}" --argjson exit_code "${rc}" \
        '{path:$path, exit_code:$exit_code, report:null}' >>"${sources}"
      worst_rc=1
    fi
  done

  jq -s '{sources: ., metadata: {vulnerabilities: {total:
      (map(.report.metadata.vulnerabilities.total // 0) | add // 0)}}}' \
    "${sources}" >"${report}"

  classify_json_report npm-audit "${report}" "${worst_rc}" \
    '.metadata.vulnerabilities.total' \
    "${#locks[@]} lockfile(s) auditado(s). Nenhuma correção automática foi aplicada."
}

scan_osv() {
  section "OSV-Scanner — dependências e lockfiles"
  local report="${REPORT_DIR}/osv-scanner.json" stderr="${REPORT_DIR}/osv-scanner.stderr.log" rc
  local exclude_args=()

  # Era o unico scanner sem exclusao de caminho, e isso tornava a contagem
  # nao reproduzivel: no baseline de 2026-07-25, 121 dos 126 findings vinham de
  # um `node_modules.bak/` transitorio (dois yarn.lock), contra 5 achados reais.
  # O osv-scanner ja respeita .gitignore, mas la o padrao e `node_modules` sem
  # barra — casa o diretorio e o symlink, nao variantes como `.bak`/`.old`.
  #
  # A flag e `--experimental-*`, entao pode sumir num upgrade. Em vez de fixar
  # e arriscar quebrar a auditoria inteira (o scan trata falha de ferramenta
  # como ERROR e invalida o run), so passamos a flag quando ela existe. Se o
  # osv-scanner remover ou renomear, o scan volta ao comportamento anterior em
  # vez de falhar — e o unico custo e um numero inflado, nao uma auditoria
  # perdida.
  if osv-scanner scan source --help 2>/dev/null | grep -q -- '--experimental-exclude'; then
    exclude_args=(--experimental-exclude 'g:**/node_modules*')
  else
    warn "osv-scanner sem --experimental-exclude; diretorios transitorios podem inflar a contagem."
  fi

  set +e
  osv-scanner scan source --recursive "${exclude_args[@]}" \
    --format json --output-file "${report}" "${PROJECT_ROOT}" \
    >"${REPORT_DIR}/osv-scanner.stdout.log" 2>"${stderr}"
  rc=$?
  set -e

  # O osv-scanner sai 127 quando TODOS os achados foram filtrados pelo
  # osv-scanner.toml, e 1 quando sobra algum. Como a contagem e zero nos dois
  # casos de "nada a reportar", um rc=127 caia no ramo de erro do
  # classify_json_report e marcava a ferramenta como `error`, o que invalida a
  # auditoria inteira — apesar de o scan ter rodado ate o fim e escrito um
  # relatorio JSON valido. Confirmado por teste controlado em 2026-07-26:
  # com o osv-scanner.toml presente (7 vulnerabilidades filtradas) o rc e 127;
  # movendo o mesmo arquivo de lado, o rc volta a ser 1.
  #
  # Normalizamos so esse caso. Qualquer outro rc diferente de 0/1/127 continua
  # sendo falha real e segue invalidando o run, que e o comportamento desejado.
  (( rc == 127 )) && rc=0

  classify_json_report osv-scanner "${report}" "${rc}" \
    '[.results[]?.packages[]?.vulnerabilities[]?] | length' "Scan recursivo de manifests e lockfiles."
}

scan_checkov() {
  section "Checkov — IaC, Docker e configurações"
  local report="${REPORT_DIR}/checkov.json" stderr="${REPORT_DIR}/checkov.stderr.log" rc
  set +e
  # Um `node_modules.bak` transitorio (o que sobra de um `mv node_modules ...`
  # durante depuracao de dependencia) nao casava nenhum dos padroes e entrava
  # no relatorio: no baseline de 2026-07-25 ele sozinho gerou os 6 findings de
  # CKV_DOCKER_* aqui, todos em `@codemirror/legacy-modes/mode/dockerfile.*`,
  # e 121 dos 126 do osv-scanner. Contagem que muda conforme o que o dev
  # deixou no disco nao e baseline.
  #
  # As variantes vao como LITERAIS, uma por padrao, e nao como
  # `node_modules([^/]*)/` ou `[^/]+\.(bak|old)/`. O `--skip-path` do checkov
  # decide se ele PODA o diretorio, e a poda so acontece quando o padrao casa
  # o caminho do diretorio de forma direta: com classe de caractere ele desce
  # nos 1.4G de node_modules e varre arquivo a arquivo. Medido em 2026-07-26:
  # 46s com literais contra >240s (timeout, sem terminar) com `([^/]*)`. Pior,
  # a versao com classe silenciosamente DESLIGA a exclusao que ja funcionava.
  # Ao adicionar uma variante nova aqui, escreva o nome inteiro.
  checkov -d "${PROJECT_ROOT}" -o json --quiet \
    --skip-path '^\.git/' \
    --skip-path '^\.security/(bin|tools|logs|reports|cache)/' \
    --skip-path '(^|/)node_modules/' \
    --skip-path '(^|/)node_modules\.bak/' \
    --skip-path '(^|/)node_modules\.old/' \
    --skip-path '(^|/)(\.venv|venv)/' \
    --skip-path '(^|/)(dist|build)/' \
    >"${report}" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report checkov "${report}" "${rc}" \
    'if type == "array" then ([.[]?.results?.failed_checks[]?] | length) else ([.results?.failed_checks[]?] | length) end' \
    "Infraestrutura e configurações."
}

scan_trivy_fs() {
  section "Trivy — filesystem, vulnerabilidades, secrets e misconfig"
  local report="${REPORT_DIR}/trivy-filesystem.json" stderr="${REPORT_DIR}/trivy-filesystem.stderr.log" rc
  set +e
  trivy fs \
    --scanners vuln,misconfig,secret \
    --format json \
    --output "${report}" \
    --exit-code 0 \
    --skip-dirs "${PROJECT_ROOT}/.git" \
    --skip-dirs "${PROJECT_ROOT}/.security/bin" \
    --skip-dirs "${PROJECT_ROOT}/.security/tools" \
    --skip-dirs "${PROJECT_ROOT}/.security/logs" \
    --skip-dirs "${PROJECT_ROOT}/.security/reports" \
    --skip-dirs "${PROJECT_ROOT}/.security/cache" \
    --skip-dirs "${PROJECT_ROOT}/node_modules" \
    --skip-dirs "${PROJECT_ROOT}/.venv" \
    --skip-dirs "${PROJECT_ROOT}/venv" \
    --skip-dirs '**/node_modules' \
    --skip-dirs '**/.venv' \
    --skip-dirs '**/venv' \
    --skip-dirs '**/node_modules.*' \
    --skip-dirs '**/*.bak' \
    --skip-dirs '**/*.old' \
    --skip-dirs '**/*.orig' \
    --skip-dirs '**/*.tmp' \
    "${PROJECT_ROOT}" >"${REPORT_DIR}/trivy-filesystem.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report trivy-filesystem "${report}" "${rc}" \
    '[.Results[]? | ((.Vulnerabilities // []) + (.Misconfigurations // []) + (.Secrets // []))[]] | length' \
    "Scan unificado do repositório."
}

scan_syft() {
  section "Syft — geração de SBOM"
  local report="${REPORT_DIR}/sbom.cyclonedx.json" stderr="${REPORT_DIR}/syft.stderr.log" rc
  set +e
  syft "dir:${SOURCE_SNAPSHOT}" -o "cyclonedx-json=${report}" \
    >"${REPORT_DIR}/syft.stdout.log" 2>"${stderr}"
  rc=$?
  set -e

  if [[ -s "${report}" ]] && jq -e . "${report}" >/dev/null 2>&1 && (( rc == 0 )); then
    local count
    count="$(jq '(.components // []) | length' "${report}")"
    record_result syft clean 0 "${rc}" "${report#${PROJECT_ROOT}/}" "SBOM gerado com ${count} componentes; não é um scanner de vulnerabilidades."
  else
    record_result syft error 0 "${rc}" "${report#${PROJECT_ROOT}/}" "Falha ao gerar SBOM válido."
  fi
}

scan_shellcheck() {
  section "ShellCheck — scripts shell"
  local report="${REPORT_DIR}/shellcheck.json" stderr="${REPORT_DIR}/shellcheck.stderr.log" rc
  local files=()
  local file

  if [[ "${RUN_SHELLCHECK}" == "0" ]]; then
    record_result shellcheck skipped 0 0 "" "Desativado por RUN_SHELLCHECK=0."
    return
  fi
  if ! command_exists shellcheck; then
    record_result shellcheck skipped 0 0 "" "ShellCheck não está disponível."
    return
  fi

  while IFS= read -r -d '' file; do
    [[ -f "${PROJECT_ROOT}/${file}" ]] && files+=("${PROJECT_ROOT}/${file}")
  done < <(git -C "${PROJECT_ROOT}" ls-files -z '*.sh')

  if ((${#files[@]} == 0)); then
    record_result shellcheck skipped 0 0 "" "Nenhum script .sh rastreado."
    return
  fi

  set +e
  shellcheck --format=json1 "${files[@]}" >"${report}" 2>"${stderr}"
  rc=$?
  set -e
  # --format=json1 emite um objeto {"comments":[...]}; 'length' contaria chaves, nao findings.
  classify_json_report shellcheck "${report}" "${rc}" '(.comments // []) | length' \
    "${#files[@]} script(s) analisado(s)."
}

scan_codeql_language() {
  local language="$1" suite="$2" label="$3"
  local db="${TEMP_ROOT}/codeql-${language}"
  local report="${REPORT_DIR}/codeql-${language}.sarif"
  local stderr="${REPORT_DIR}/codeql-${language}.stderr.log"
  local rc_create rc_analyze

  set +e
  codeql database create "${db}" \
    --language="${language}" \
    --source-root="${PROJECT_ROOT}" \
    --overwrite \
    >"${REPORT_DIR}/codeql-${language}-create.stdout.log" 2>"${stderr}"
  rc_create=$?
  set -e

  if (( rc_create != 0 )); then
    record_result "codeql-${language}" error 0 "${rc_create}" "${report#${PROJECT_ROOT}/}" \
      "Falha ao criar banco CodeQL para ${label}."
    return
  fi

  set +e
  codeql database analyze "${db}" "${suite}" \
    --format=sarif-latest \
    --sarif-category="${language}" \
    --output="${report}" \
    --threads=0 \
    >"${REPORT_DIR}/codeql-${language}-analyze.stdout.log" 2>>"${stderr}"
  rc_analyze=$?
  set -e

  classify_json_report "codeql-${language}" "${report}" "${rc_analyze}" \
    '[.runs[]?.results[]?] | length' "Suite security-and-quality para ${label}."
}

scan_codeql() {
  section "CodeQL — análise profunda"
  if [[ "${RUN_CODEQL}" == "0" ]]; then
    record_result codeql skipped 0 0 "" "Desativado por RUN_CODEQL=0."
    return
  fi
  if ! command_exists codeql; then
    # A nota anterior dizia "nesta arquitetura/configuração", e isso se lia como
    # limitação de plataforma. Não é: em 2026-07-24 o instalador registrou
    # "CodeQL desativado por configuração", ou seja, rodou com INSTALL_CODEQL=0.
    # O padrão é 1 em amd64 e esta máquina é x86_64, que o install.sh mapeia
    # para amd64 — a arquitetura sempre suportou. Distinguir os dois casos
    # importa porque um é impedimento e o outro é uma decisão reversível, e a
    # nota antiga fazia a lacuna de SAST parecer inevitável.
    local motivo="CodeQL não instalado."
    if [[ "${OS_ARCH_SUPPORTS_CODEQL:-1}" == "1" ]]; then
      motivo+=" A arquitetura é suportada; reinstale com INSTALL_CODEQL=1 ./.security/install.sh."
    fi
    record_result codeql skipped 0 0 "" "${motivo} Lacuna de SAST profundo, não coberta por semgrep/bandit."
    return
  fi

  scan_codeql_language python \
    'codeql/python-queries:codeql-suites/python-security-and-quality.qls' \
    'Python'

  if [[ -f "${PROJECT_ROOT}/package.json" ]]; then
    scan_codeql_language javascript-typescript \
      'codeql/javascript-queries:codeql-suites/javascript-security-and-quality.qls' \
      'JavaScript/TypeScript'
  else
    record_result codeql-javascript-typescript skipped 0 0 "" "package.json não encontrado."
  fi
}

scan_container_image() {
  section "Trivy — imagem de container"
  local report="${REPORT_DIR}/trivy-image.json" stderr="${REPORT_DIR}/trivy-image.stderr.log" rc
  if [[ -z "${CONTAINER_IMAGE}" ]]; then
    record_result trivy-image skipped 0 0 "" "Defina CONTAINER_IMAGE=nome:tag para habilitar."
    return
  fi

  set +e
  trivy image --scanners vuln,secret --format json --output "${report}" --exit-code 0 "${CONTAINER_IMAGE}" \
    >"${REPORT_DIR}/trivy-image.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report trivy-image "${report}" "${rc}" \
    '[.Results[]? | ((.Vulnerabilities // []) + (.Secrets // []))[]] | length' \
    "Imagem: ${CONTAINER_IMAGE}."
}

configure_docker() {
  if ! command_exists docker; then
    return 1
  fi
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
    return 0
  fi
  if command_exists sudo && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    return 0
  fi
  return 1
}

scan_zap_baseline() {
  section "OWASP ZAP — baseline passivo"
  local report_name="zap-baseline.json"
  local report="${REPORT_DIR}/${report_name}"
  local stderr="${REPORT_DIR}/zap-baseline.stderr.log" rc
  DOCKER=()

  if [[ -z "${ZAP_TARGET_URL}" ]]; then
    record_result zap-baseline skipped 0 0 "" "Defina ZAP_TARGET_URL apenas quando a aplicação estiver rodando."
    return
  fi
  if [[ ! "${ZAP_TARGET_URL}" =~ ^https?:// ]]; then
    record_result zap-baseline error 0 2 "" "ZAP_TARGET_URL deve começar com http:// ou https://."
    return
  fi
  if ! configure_docker; then
    record_result zap-baseline error 0 2 "" "Docker não está disponível para executar o ZAP."
    return
  fi

  set +e
  "${DOCKER[@]}" run --rm \
    -v "${REPORT_DIR}:/zap/wrk:rw" \
    "${ZAP_IMAGE}" \
    zap-baseline.py -t "${ZAP_TARGET_URL}" -J "${report_name}" -r zap-baseline.html -I \
    >"${REPORT_DIR}/zap-baseline.stdout.log" 2>"${stderr}"
  rc=$?
  set -e

  classify_json_report zap-baseline "${report}" "${rc}" \
    '[.site[]?.alerts[]?] | length' "Baseline passivo contra ${ZAP_TARGET_URL}; nenhum active scan foi executado."
}

generate_summary() {
  section "Resumo"

  jq -s \
    --arg generated_at "$(now)" \
    --arg report_dir "${REPORT_DIR#${PROJECT_ROOT}/}" \
    --argjson total_findings "${TOTAL_FINDINGS}" \
    --argjson finding_tools "${FINDING_TOOLS}" \
    --argjson clean_tools "${CLEAN_TOOLS}" \
    --argjson error_tools "${ERROR_TOOLS}" \
    --argjson skipped_tools "${SKIPPED_TOOLS}" \
    '{generated_at:$generated_at,report_dir:$report_dir,total_findings:$total_findings,tools:{findings:$finding_tools,clean:$clean_tools,error:$error_tools,skipped:$skipped_tools},results:.}' \
    "${RESULTS_JSONL}" > "${SUMMARY_JSON}"

  {
    printf '# Relatório de segurança\n\n'
    printf -- '- Gerado em: `%s`\n' "$(now)"
    printf -- '- Commit: `%s`\n' "$(git -C "${PROJECT_ROOT}" rev-parse HEAD)"
    printf -- '- Total de findings reportados: **%s**\n' "${TOTAL_FINDINGS}"
    printf -- '- Ferramentas com findings: **%s**\n' "${FINDING_TOOLS}"
    printf -- '- Ferramentas limpas: **%s**\n' "${CLEAN_TOOLS}"
    printf -- '- Ferramentas com erro: **%s**\n' "${ERROR_TOOLS}"
    printf -- '- Ferramentas ignoradas: **%s**\n\n' "${SKIPPED_TOOLS}"
    printf '## Resultados\n\n'
    printf '| Ferramenta | Status | Findings | Relatório | Observação |\n'
    printf '|---|---:|---:|---|---|\n'
    jq -r '. | "| \(.tool) | \(.status) | \(.count) | `\(.report)` | \(.note | gsub("\\|"; "\\\\|")) |"' \
      "${RESULTS_JSONL}"
    printf '\n## Interpretação\n\n'
    printf 'As contagens podem incluir duplicatas entre scanners e não são somáveis como risco. '
    printf 'O escopo é código de produção: Bandit e Semgrep excluem testes, e a contagem do '
    printf 'Bandit cobre apenas MEDIUM+HIGH (os LOW seguem no JSON, fora do total). '
    printf 'A coluna Observação registra o recorte aplicado a cada ferramenta. '
    printf 'Todo finding precisa ser confirmado no código antes de uma correção. '
    printf 'Um status `error` invalida a auditoria e deve ser resolvido antes do deploy.\n'
  } > "${SUMMARY_MD}"

  ln -sfn -- "${RUN_ID}" "${REPORTS_ROOT}/latest"

  log "Resumo Markdown: ${SUMMARY_MD}"
  log "Resumo JSON: ${SUMMARY_JSON}"
  log "Log completo: ${LOG_FILE}"
  printf '\nFindings reportados: %s | scanners com erro: %s\n' "${TOTAL_FINDINGS}" "${ERROR_TOOLS}"
}

main() {
  require_bool FAIL_ON_FINDINGS "${FAIL_ON_FINDINGS}"
  require_bool RUN_CODEQL "${RUN_CODEQL}"
  require_bool RUN_SHELLCHECK "${RUN_SHELLCHECK}"

  section "Scan de segurança do Nick Agent"
  require_tools
  write_metadata
  snapshot_source

  # Secrets primeiro, antes que os demais relatórios sejam produzidos.
  scan_gitleaks_git
  scan_gitleaks_worktree

  scan_semgrep
  scan_bandit
  scan_pip_audit
  scan_npm_audit
  scan_osv
  scan_checkov
  scan_trivy_fs
  scan_syft
  scan_shellcheck
  scan_codeql
  scan_container_image
  scan_zap_baseline

  generate_summary

  if (( ERROR_TOOLS > 0 )); then
    warn "${ERROR_TOOLS} ferramenta(s) falharam; a auditoria é inválida."
    return 2
  fi
  if [[ "${FAIL_ON_FINDINGS}" == "1" ]] && (( TOTAL_FINDINGS > 0 )); then
    warn "Foram encontrados ${TOTAL_FINDINGS} findings em código de produção. Revise ${SUMMARY_MD}."
    return 1
  fi

  log "Scan concluído sem erros de ferramenta."
  return 0
}

main "$@"
