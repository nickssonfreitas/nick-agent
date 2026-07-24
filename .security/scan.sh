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

mkdir -p -- "${LOG_DIR}" "${REPORT_DIR}" "${SOURCE_SNAPSHOT}"
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
  (cd "${PROJECT_ROOT}" && gitleaks git --redact=100 --report-format json --report-path "${report}" .) \
    >"${REPORT_DIR}/gitleaks-git.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report gitleaks-git "${report}" "${rc}" 'length' "Histórico completo do Git."
}

scan_gitleaks_worktree() {
  section "Gitleaks — arquivos atuais"
  local report="${REPORT_DIR}/gitleaks-worktree.json" stderr="${REPORT_DIR}/gitleaks-worktree.stderr.log" rc
  set +e
  gitleaks dir --redact=100 --report-format json --report-path "${report}" "${SOURCE_SNAPSHOT}" \
    >"${REPORT_DIR}/gitleaks-worktree.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report gitleaks-worktree "${report}" "${rc}" 'length' "Inclui mudanças locais não ignoradas."
}

scan_semgrep() {
  section "Semgrep — análise estática"
  local report="${REPORT_DIR}/semgrep.json" stderr="${REPORT_DIR}/semgrep.stderr.log" rc
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
    .) >"${REPORT_DIR}/semgrep.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report semgrep "${report}" "${rc}" '(.results // []) | length' "Ruleset: ${SEMGREP_CONFIG}."
}

scan_bandit() {
  section "Bandit — segurança Python"
  local report="${REPORT_DIR}/bandit.json" stderr="${REPORT_DIR}/bandit.stderr.log" rc
  # Alvo e exclusoes em caminho absoluto. Com 'bandit -r .' os caminhos saem como
  # './venv/...' e um -x 'venv' nao casa pelo prefixo './', deixando venv/,
  # .security/ (venvs dos proprios scanners) e node_modules/ serem varridos.
  local skip="${PROJECT_ROOT}/.git,${PROJECT_ROOT}/.security,${PROJECT_ROOT}/node_modules"
  skip+=",${PROJECT_ROOT}/.venv,${PROJECT_ROOT}/venv,${PROJECT_ROOT}/dist,${PROJECT_ROOT}/build"
  set +e
  bandit -r "${PROJECT_ROOT}" -x "${skip}" \
    -f json -o "${report}" >"${REPORT_DIR}/bandit.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report bandit "${report}" "${rc}" '(.results // []) | length' "Código Python."
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
  if [[ ! -f "${PROJECT_ROOT}/package-lock.json" ]]; then
    record_result npm-audit skipped 0 0 "" "package-lock.json não encontrado."
    return
  fi

  set +e
  (cd "${PROJECT_ROOT}" && npm audit --json --package-lock-only --audit-level=low) \
    >"${report}" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report npm-audit "${report}" "${rc}" \
    '(.metadata.vulnerabilities.total // ((.metadata.vulnerabilities // {}) | to_entries | map(.value) | add) // 0)' \
    "Nenhuma correção automática foi aplicada."
}

scan_osv() {
  section "OSV-Scanner — dependências e lockfiles"
  local report="${REPORT_DIR}/osv-scanner.json" stderr="${REPORT_DIR}/osv-scanner.stderr.log" rc
  set +e
  osv-scanner scan source --recursive --format json --output-file "${report}" "${PROJECT_ROOT}" \
    >"${REPORT_DIR}/osv-scanner.stdout.log" 2>"${stderr}"
  rc=$?
  set -e
  classify_json_report osv-scanner "${report}" "${rc}" \
    '[.results[]?.packages[]?.vulnerabilities[]?] | length' "Scan recursivo de manifests e lockfiles."
}

scan_checkov() {
  section "Checkov — IaC, Docker e configurações"
  local report="${REPORT_DIR}/checkov.json" stderr="${REPORT_DIR}/checkov.stderr.log" rc
  set +e
  checkov -d "${PROJECT_ROOT}" -o json --quiet \
    --skip-path '^\.git/' \
    --skip-path '^\.security/(bin|tools|logs|reports)/' \
    --skip-path '(^|/)node_modules/' \
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
    --skip-dirs "${PROJECT_ROOT}/node_modules" \
    --skip-dirs "${PROJECT_ROOT}/.venv" \
    --skip-dirs "${PROJECT_ROOT}/venv" \
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
    record_result codeql skipped 0 0 "" "CodeQL não foi instalado nesta arquitetura/configuração."
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
    printf -- '- Total bruto de findings: **%s**\n' "${TOTAL_FINDINGS}"
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
    printf 'As contagens são brutas e podem incluir duplicatas entre scanners. '
    printf 'Todo finding precisa ser confirmado no código antes de uma correção. '
    printf 'Um status `error` invalida a auditoria e deve ser resolvido antes do deploy.\n'
  } > "${SUMMARY_MD}"

  ln -sfn -- "${RUN_ID}" "${REPORTS_ROOT}/latest"

  log "Resumo Markdown: ${SUMMARY_MD}"
  log "Resumo JSON: ${SUMMARY_JSON}"
  log "Log completo: ${LOG_FILE}"
  printf '\nFindings brutos: %s | scanners com erro: %s\n' "${TOTAL_FINDINGS}" "${ERROR_TOOLS}"
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
    warn "Foram encontrados ${TOTAL_FINDINGS} findings brutos. Revise ${SUMMARY_MD}."
    return 1
  fi

  log "Scan concluído sem erros de ferramenta."
  return 0
}

main "$@"
