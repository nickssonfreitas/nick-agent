#!/usr/bin/env bash
# Instala uma suíte local de ferramentas de segurança para auditar o fork do Hermes.
# Alvo: Ubuntu 22.04+/Debian 12+, x86_64 ou arm64.
#
# Uso:
#   chmod +x .security/install.sh
#   ./.security/install.sh
#
# Variáveis opcionais:
#   INSTALL_SYSTEM_PACKAGES=1   Instala dependências via apt (padrão: 1)
#   INSTALL_DOCKER=1           Instala Docker via pacote da distribuição (padrão: 1)
#   PULL_ZAP_IMAGE=1           Baixa a imagem estável oficial do ZAP (padrão: 1)
#   INSTALL_CODEQL=1           Instala o bundle local do CodeQL (padrão: 1 em amd64)
#   ALLOW_UNVERIFIED_DOWNLOADS=0
#                               Nunca aceite binários sem SHA-256 (padrão: 0)
#   GITHUB_TOKEN=...           Opcional; aumenta o limite da API do GitHub
#
# Versões Python fixadas em 23/07/2026. Podem ser sobrescritas:
#   SEMGREP_VERSION=1.162.0 BANDIT_VERSION=1.9.4 \
#   PIP_AUDIT_VERSION=2.10.1 CHECKOV_VERSION=3.3.8 \
#   ./.security/install.sh
#
# Binários GitHub usam "latest" por padrão, verificam SHA-256 e registram a
# versão efetivamente instalada em .security/versions.lock.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly BIN_DIR="${SCRIPT_DIR}/bin"
readonly TOOLS_DIR="${SCRIPT_DIR}/tools"
readonly VENV_ROOT="${TOOLS_DIR}/python"
readonly LOG_DIR="${SCRIPT_DIR}/logs"
readonly LOCK_FILE="${SCRIPT_DIR}/versions.lock"
readonly ENV_FILE="${SCRIPT_DIR}/env.sh"
readonly RUN_ID="$(date -u +'%Y%m%dT%H%M%SZ')"
readonly LOG_FILE="${LOG_DIR}/install-${RUN_ID}.log"

readonly INSTALL_SYSTEM_PACKAGES="${INSTALL_SYSTEM_PACKAGES:-1}"
readonly INSTALL_DOCKER="${INSTALL_DOCKER:-1}"
readonly PULL_ZAP_IMAGE="${PULL_ZAP_IMAGE:-1}"
readonly INSTALL_CODEQL="${INSTALL_CODEQL:-1}"
readonly ALLOW_UNVERIFIED_DOWNLOADS="${ALLOW_UNVERIFIED_DOWNLOADS:-0}"

readonly SEMGREP_VERSION="${SEMGREP_VERSION:-1.162.0}"
readonly BANDIT_VERSION="${BANDIT_VERSION:-1.9.4}"
readonly PIP_AUDIT_VERSION="${PIP_AUDIT_VERSION:-2.10.1}"
readonly CHECKOV_VERSION="${CHECKOV_VERSION:-3.3.8}"

readonly GITLEAKS_VERSION="${GITLEAKS_VERSION:-latest}"
readonly OSV_SCANNER_VERSION="${OSV_SCANNER_VERSION:-latest}"
readonly TRIVY_VERSION="${TRIVY_VERSION:-latest}"
readonly SYFT_VERSION="${SYFT_VERSION:-latest}"
readonly CODEQL_VERSION="${CODEQL_VERSION:-latest}"
readonly ZAP_IMAGE="${ZAP_IMAGE:-ghcr.io/zaproxy/zaproxy:stable}"

mkdir -p -- "${BIN_DIR}" "${TOOLS_DIR}" "${VENV_ROOT}" "${LOG_DIR}"
touch -- "${LOG_FILE}"
chmod 600 -- "${LOG_FILE}"
exec > >(tee -a "${LOG_FILE}") 2>&1

TEMP_ROOT=""
SUDO=()
DOCKER=()

now() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { printf '[%s] [INFO] %s\n' "$(now)" "$*"; }
warn() { printf '[%s] [WARN] %s\n' "$(now)" "$*" >&2; }
die() { printf '[%s] [ERRO] %s\n' "$(now)" "$*" >&2; exit 1; }
section() { printf '\n[%s] ===== %s =====\n' "$(now)" "$*"; }

cleanup() {
  local exit_code=$?
  if [[ -n "${TEMP_ROOT}" && -d "${TEMP_ROOT}" ]]; then
    rm -rf -- "${TEMP_ROOT}"
  fi
  if (( exit_code != 0 )); then
    printf '[%s] [ERRO] Instalação interrompida na linha %s. Consulte: %s\n' \
      "$(now)" "${BASH_LINENO[0]:-desconhecida}" "${LOG_FILE}" >&2
  fi
  exit "${exit_code}"
}
trap cleanup EXIT
trap 'die "Sinal recebido; nenhuma etapa posterior será executada."' INT TERM

require_bool() {
  local name="$1" value="$2"
  [[ "${value}" == "0" || "${value}" == "1" ]] || die "${name} deve ser 0 ou 1."
}

require_bool INSTALL_SYSTEM_PACKAGES "${INSTALL_SYSTEM_PACKAGES}"
require_bool INSTALL_DOCKER "${INSTALL_DOCKER}"
require_bool PULL_ZAP_IMAGE "${PULL_ZAP_IMAGE}"
require_bool INSTALL_CODEQL "${INSTALL_CODEQL}"
require_bool ALLOW_UNVERIFIED_DOWNLOADS "${ALLOW_UNVERIFIED_DOWNLOADS}"

command_exists() { command -v "$1" >/dev/null 2>&1; }

setup_privilege_escalation() {
  if [[ "${EUID}" -eq 0 ]]; then
    SUDO=()
  elif command_exists sudo; then
    SUDO=(sudo)
  else
    die "É necessário executar como root ou ter o comando sudo para instalar pacotes do sistema."
  fi
}

check_platform() {
  [[ "$(uname -s)" == "Linux" ]] || die "Este instalador suporta somente Linux."
  [[ -r /etc/os-release ]] || die "Não foi possível identificar a distribuição Linux."

  # shellcheck disable=SC1091
  source /etc/os-release
  local family="${ID:-} ${ID_LIKE:-}"
  if [[ "${family}" != *debian* && "${family}" != *ubuntu* ]]; then
    die "Distribuição não suportada: ${PRETTY_NAME:-desconhecida}. Use Ubuntu/Debian."
  fi

  case "$(uname -m)" in
    x86_64|amd64)
      OS_ARCH="amd64"
      GITLEAKS_ARCH="x64"
      TRIVY_ARCH="64bit"
      ;;
    aarch64|arm64)
      OS_ARCH="arm64"
      GITLEAKS_ARCH="arm64"
      TRIVY_ARCH="ARM64"
      ;;
    *) die "Arquitetura não suportada: $(uname -m)." ;;
  esac

  readonly OS_ARCH GITLEAKS_ARCH TRIVY_ARCH
  log "Sistema: ${PRETTY_NAME:-Linux}; arquitetura: ${OS_ARCH}."
}

install_system_packages() {
  section "Dependências do sistema"
  if [[ "${INSTALL_SYSTEM_PACKAGES}" == "0" ]]; then
    log "Instalação de pacotes do sistema desativada."
    return
  fi

  setup_privilege_escalation
  "${SUDO[@]}" apt-get update

  local packages=(
    ca-certificates
    curl
    git
    jq
    unzip
    tar
    gzip
    coreutils
    findutils
    python3
    python3-venv
    python3-pip
    nodejs
    npm
    shellcheck
  )

  if [[ "${INSTALL_DOCKER}" == "1" ]] && ! command_exists docker; then
    packages+=(docker.io)
  fi

  DEBIAN_FRONTEND=noninteractive "${SUDO[@]}" apt-get install -y --no-install-recommends "${packages[@]}"

  if [[ "${INSTALL_DOCKER}" == "1" ]] && command_exists systemctl && command_exists docker; then
    "${SUDO[@]}" systemctl enable --now docker || warn "Docker foi instalado, mas o serviço não pôde ser iniciado automaticamente."
  fi
}

check_required_commands() {
  section "Validação das dependências básicas"
  local missing=()
  local cmd
  for cmd in curl git jq unzip tar gzip sha256sum python3 node npm; do
    command_exists "${cmd}" || missing+=("${cmd}")
  done
  ((${#missing[@]} == 0)) || die "Comandos ausentes: ${missing[*]}."

  local py_version
  py_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  python3 - <<'PY' || die "Python 3.10 ou superior é obrigatório."
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
  log "Python ${py_version}, Node $(node --version), npm $(npm --version)."
}

create_temp_root() {
  TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/hermes-security-install.XXXXXX")"
  chmod 700 -- "${TEMP_ROOT}"
}

write_lock_header() {
  cat > "${LOCK_FILE}" <<EOF_LOCK
# Gerado automaticamente por .security/install.sh
# Data UTC: $(now)
# Projeto: ${PROJECT_ROOT}
EOF_LOCK
  chmod 600 -- "${LOCK_FILE}"
}

record_lock() {
  local tool="$1" version="$2" source="$3" sha256_value="${4:-n/a}"
  printf '%s\t%s\t%s\t%s\n' "${tool}" "${version}" "${sha256_value}" "${source}" >> "${LOCK_FILE}"
}

curl_common_args() {
  CURL_ARGS=(
    --fail
    --silent
    --show-error
    --location
    --retry 5
    --retry-delay 2
    --retry-all-errors
    --connect-timeout 20
    --max-time 1800
    --proto '=https'
    --tlsv1.2
    --header 'Accept: application/vnd.github+json'
    --header 'X-GitHub-Api-Version: 2022-11-28'
    --header 'User-Agent: hermes-security-installer/1.0'
  )
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    CURL_ARGS+=(--header "Authorization: Bearer ${GITHUB_TOKEN}")
  fi
}

fetch_release_json() {
  local repo="$1" requested="$2" output="$3" endpoint
  if [[ "${requested}" == "latest" ]]; then
    endpoint="https://api.github.com/repos/${repo}/releases/latest"
  else
    local tag="${requested}"
    [[ "${tag}" == v* ]] || tag="v${tag}"
    endpoint="https://api.github.com/repos/${repo}/releases/tags/${tag}"
  fi

  curl_common_args
  curl "${CURL_ARGS[@]}" --output "${output}" "${endpoint}"
  jq -e '.tag_name and (.assets | type == "array")' "${output}" >/dev/null \
    || die "Resposta de release inválida para ${repo}."
}

select_asset() {
  local release_json="$1" regex="$2"
  local count
  count="$(jq --arg regex "${regex}" '[.assets[] | select(.name | test($regex; "i"))] | length' "${release_json}")"
  [[ "${count}" == "1" ]] || {
    warn "Regex de artefato: ${regex}"
    jq -r '.assets[].name' "${release_json}" >&2 || true
    die "Esperava exatamente 1 artefato; encontrei ${count}."
  }
  jq -c --arg regex "${regex}" '.assets[] | select(.name | test($regex; "i"))' "${release_json}"
}

download_url() {
  local url="$1" destination="$2"
  curl_common_args
  curl "${CURL_ARGS[@]}" --output "${destination}" "${url}"
}

verify_release_asset() {
  local release_json="$1" asset_name="$2" asset_path="$3"
  local digest expected actual checksum_asset checksum_url checksum_path

  digest="$(jq -r --arg name "${asset_name}" '.assets[] | select(.name == $name) | (.digest // empty)' "${release_json}")"
  if [[ "${digest}" == sha256:* ]]; then
    expected="${digest#sha256:}"
  else
    checksum_asset="$(jq -r '
      [.assets[]
       | select(.name | test("(checksums?|sha256sums?|sha256sum)[^/]*\\.(txt|sha256)$"; "i"))]
      | if length > 0 then .[0].name else empty end
    ' "${release_json}")"

    if [[ -n "${checksum_asset}" ]]; then
      checksum_url="$(jq -r --arg name "${checksum_asset}" '.assets[] | select(.name == $name) | .browser_download_url' "${release_json}")"
      checksum_path="${TEMP_ROOT}/${checksum_asset}"
      download_url "${checksum_url}" "${checksum_path}"
      expected="$(awk -v file="${asset_name}" '
        {
          name=$2
          sub(/^\*/, "", name)
          if (name == file) { print $1; exit }
        }
      ' "${checksum_path}")"
    else
      expected=""
    fi
  fi

  actual="$(sha256sum "${asset_path}" | awk '{print $1}')"

  if [[ -z "${expected}" ]]; then
    if [[ "${ALLOW_UNVERIFIED_DOWNLOADS}" == "1" ]]; then
      warn "${asset_name}: fornecedor não apresentou SHA-256 detectável; aceitando por configuração explícita."
      printf '%s' "${actual}"
      return
    fi
    die "${asset_name}: não foi possível obter SHA-256 publicado. Defina ALLOW_UNVERIFIED_DOWNLOADS=1 somente se aceitar esse risco."
  fi

  [[ "${expected}" =~ ^[a-fA-F0-9]{64}$ ]] || die "Checksum publicado inválido para ${asset_name}."
  [[ "${actual,,}" == "${expected,,}" ]] || die "Checksum incorreto para ${asset_name}."
  log "SHA-256 confirmado: ${asset_name}." >&2
  printf '%s' "${actual}"
}

install_archive_binary() {
  local tool="$1" repo="$2" requested="$3" regex="$4" binary_name="$5"
  section "Instalação: ${tool}"

  local work="${TEMP_ROOT}/${tool}"
  local release_json="${work}/release.json"
  mkdir -p -- "${work}/extract"
  fetch_release_json "${repo}" "${requested}" "${release_json}"

  local tag asset asset_name asset_url archive sha
  tag="$(jq -r '.tag_name' "${release_json}")"
  asset="$(select_asset "${release_json}" "${regex}")"
  asset_name="$(jq -r '.name' <<<"${asset}")"
  asset_url="$(jq -r '.browser_download_url' <<<"${asset}")"
  archive="${work}/${asset_name}"

  log "Baixando ${tool} ${tag}: ${asset_name}."
  download_url "${asset_url}" "${archive}"
  sha="$(verify_release_asset "${release_json}" "${asset_name}" "${archive}")"

  case "${asset_name}" in
    *.tar.gz|*.tgz) tar -xzf "${archive}" -C "${work}/extract" ;;
    *.zip) unzip -q "${archive}" -d "${work}/extract" ;;
    *) cp -- "${archive}" "${work}/extract/${binary_name}" ;;
  esac

  local candidate
  candidate="$(find "${work}/extract" -type f -name "${binary_name}" -print -quit)"
  [[ -n "${candidate}" ]] || {
    # Alguns projetos nomeiam o binário baixado com sufixo de plataforma.
    candidate="$(find "${work}/extract" -maxdepth 2 -type f -print -quit)"
  }
  [[ -n "${candidate}" && -f "${candidate}" ]] || die "Binário ${binary_name} não encontrado no artefato ${asset_name}."

  install -m 0755 -- "${candidate}" "${BIN_DIR}/${binary_name}"
  record_lock "${tool}" "${tag}" "${asset_url}" "${sha}"
  log "Instalado: ${BIN_DIR}/${binary_name}."
}

install_one_python_tool() {
  local tool="$1" package="$2" version="$3" executable="$4"
  local venv="${VENV_ROOT}/${tool}"

  log "Instalando ${package}==${version} em ambiente isolado."
  if [[ ! -x "${venv}/bin/python" ]]; then
    python3 -m venv "${venv}"
  fi

  "${venv}/bin/python" -m pip install --disable-pip-version-check --no-input --upgrade pip setuptools wheel
  "${venv}/bin/python" -m pip install --disable-pip-version-check --no-input --no-cache-dir \
    "${package}==${version}"
  "${venv}/bin/python" -m pip check

  [[ -x "${venv}/bin/${executable}" ]] || die "Executável Python não encontrado: ${executable}."
  ln -sfn -- "../tools/python/${tool}/bin/${executable}" "${BIN_DIR}/${executable}"
  record_lock "${tool}" "${version}" "https://pypi.org/project/${package}/" "pypi"
}

install_python_tools() {
  section "Ferramentas Python isoladas"

  install_one_python_tool semgrep semgrep "${SEMGREP_VERSION}" semgrep
  install_one_python_tool bandit bandit "${BANDIT_VERSION}" bandit
  install_one_python_tool pip-audit pip-audit "${PIP_AUDIT_VERSION}" pip-audit
  install_one_python_tool checkov checkov "${CHECKOV_VERSION}" checkov

  log "Auditando as dependências instaladas nos ambientes das ferramentas Python."
  local tool venv requirements_file audit_failures=0
  for tool in semgrep bandit pip-audit checkov; do
    venv="${VENV_ROOT}/${tool}"
    requirements_file="${TEMP_ROOT}/${tool}-installed.txt"
    "${venv}/bin/python" -m pip freeze --all > "${requirements_file}"
    if ! "${VENV_ROOT}/pip-audit/bin/pip-audit" \
      --disable-pip --no-deps --requirement "${requirements_file}"; then
      warn "pip-audit encontrou vulnerabilidade(s) no ambiente da ferramenta ${tool}."
      ((audit_failures+=1))
    fi
  done

  if ((audit_failures > 0)); then
    warn "${audit_failures} ambiente(s) de scanner possuem advisories conhecidos; revise o log e as versões fixadas."
  fi
}

install_native_tools() {
  install_archive_binary \
    gitleaks gitleaks/gitleaks "${GITLEAKS_VERSION}" \
    "^gitleaks_[0-9.]+_linux_${GITLEAKS_ARCH}\\.tar\\.gz$" \
    gitleaks

  install_archive_binary \
    osv-scanner google/osv-scanner "${OSV_SCANNER_VERSION}" \
    "^osv-scanner(_v?[0-9.]+)?_linux_${OS_ARCH}(\\.tar\\.gz|\\.zip)?$" \
    osv-scanner

  install_archive_binary \
    trivy aquasecurity/trivy "${TRIVY_VERSION}" \
    "^trivy_[0-9.]+_Linux-${TRIVY_ARCH}\\.tar\\.gz$" \
    trivy

  install_archive_binary \
    syft anchore/syft "${SYFT_VERSION}" \
    "^syft_[0-9.]+_linux_${OS_ARCH}\\.tar\\.gz$" \
    syft
}

install_codeql_bundle() {
  section "Instalação: CodeQL"

  if [[ "${INSTALL_CODEQL}" == "0" ]]; then
    log "CodeQL desativado por configuração."
    return
  fi

  if [[ "${OS_ARCH}" != "amd64" ]]; then
    warn "O bundle Linux oficial usado aqui é x86_64; CodeQL será ignorado em ${OS_ARCH}."
    return
  fi

  log "O uso do CodeQL está sujeito aos termos da GitHub e este instalador o destina ao fork open source informado."

  local work="${TEMP_ROOT}/codeql"
  local release_json="${work}/release.json"
  mkdir -p -- "${work}/extract"
  fetch_release_json github/codeql-action "${CODEQL_VERSION}" "${release_json}"

  local tag asset asset_name asset_url archive sha
  tag="$(jq -r '.tag_name' "${release_json}")"
  asset="$(select_asset "${release_json}" "^codeql-bundle-linux64\\.tar\\.gz$")"
  asset_name="$(jq -r '.name' <<<"${asset}")"
  asset_url="$(jq -r '.browser_download_url' <<<"${asset}")"
  archive="${work}/${asset_name}"

  log "Baixando CodeQL ${tag}: ${asset_name}."
  download_url "${asset_url}" "${archive}"
  sha="$(verify_release_asset "${release_json}" "${asset_name}" "${archive}")"
  tar -xzf "${archive}" -C "${work}/extract"

  local codeql_exec codeql_root
  codeql_exec="$(find "${work}/extract" -type f -path '*/codeql/codeql' -print -quit)"
  if [[ -z "${codeql_exec}" ]]; then
    codeql_exec="$(find "${work}/extract" -type f -name codeql -print -quit)"
  fi
  [[ -n "${codeql_exec}" ]] || die "Executável CodeQL não encontrado no bundle."
  codeql_root="$(dirname "${codeql_exec}")"

  rm -rf -- "${TOOLS_DIR}/codeql"
  cp -a -- "${codeql_root}" "${TOOLS_DIR}/codeql"
  ln -sfn -- "../tools/codeql/codeql" "${BIN_DIR}/codeql"
  record_lock codeql "${tag}" "${asset_url}" "${sha}"
  log "CodeQL instalado em ${TOOLS_DIR}/codeql."
}

configure_docker_command() {
  DOCKER=()
  command_exists docker || return 1

  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
    return 0
  fi

  if [[ "${EUID}" -ne 0 ]] && command_exists sudo; then
    if sudo docker info >/dev/null 2>&1; then
      DOCKER=(sudo docker)
      return 0
    fi
  fi

  return 1
}

pull_zap() {
  section "OWASP ZAP em container"

  if [[ "${PULL_ZAP_IMAGE}" == "0" ]]; then
    log "Download da imagem ZAP desativado por configuração."
    return
  fi

  if ! configure_docker_command; then
    warn "Docker não está disponível ou acessível; a imagem ZAP não foi baixada."
    return
  fi

  "${DOCKER[@]}" pull "${ZAP_IMAGE}"

  local repo_digest image_id
  repo_digest="$("${DOCKER[@]}" image inspect --format '{{index .RepoDigests 0}}' "${ZAP_IMAGE}" 2>/dev/null || true)"
  image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "${ZAP_IMAGE}")"
  record_lock zap "${repo_digest:-${ZAP_IMAGE}}" "${ZAP_IMAGE}" "${image_id#sha256:}"
  log "ZAP disponível como ${repo_digest:-${ZAP_IMAGE}}."
  log "O usuário NÃO foi adicionado ao grupo docker; use sudo docker quando necessário."
}

write_environment_file() {
  section "Configuração do PATH"
  cat > "${ENV_FILE}" <<EOF_ENV
#!/usr/bin/env bash
# Carregue com: source .security/env.sh
export HERMES_SECURITY_HOME="${SCRIPT_DIR}"
export PATH="${BIN_DIR}:\${PATH}"
export ZAP_IMAGE="${ZAP_IMAGE}"
EOF_ENV
  chmod 600 -- "${ENV_FILE}"
  log "Criado ${ENV_FILE}."
}

validate_tool() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '[%s] [OK]   %s\n' "$(now)" "${name}"
  else
    printf '[%s] [FALHA] %s\n' "$(now)" "${name}" >&2
    return 1
  fi
}

final_validation() {
  section "Validação final"
  local failures=0

  validate_tool semgrep "${BIN_DIR}/semgrep" --version || ((failures+=1))
  validate_tool bandit "${BIN_DIR}/bandit" --version || ((failures+=1))
  validate_tool pip-audit "${BIN_DIR}/pip-audit" --version || ((failures+=1))
  validate_tool checkov "${BIN_DIR}/checkov" --version || ((failures+=1))
  validate_tool gitleaks "${BIN_DIR}/gitleaks" version || ((failures+=1))
  validate_tool osv-scanner "${BIN_DIR}/osv-scanner" --version || ((failures+=1))
  validate_tool trivy "${BIN_DIR}/trivy" --version || ((failures+=1))
  validate_tool syft "${BIN_DIR}/syft" version || ((failures+=1))
  validate_tool npm-audit npm audit --help || ((failures+=1))

  if [[ -x "${BIN_DIR}/codeql" ]]; then
    validate_tool codeql "${BIN_DIR}/codeql" version || ((failures+=1))
  fi

  if command_exists shellcheck; then
    validate_tool shellcheck shellcheck --version || ((failures+=1))
  fi

  ((failures == 0)) || die "${failures} validação(ões) falharam."
}

print_summary() {
  section "Instalação concluída"
  cat <<EOF_SUMMARY
Diretório de ferramentas: ${SCRIPT_DIR}
Binários:                ${BIN_DIR}
Versões e checksums:     ${LOCK_FILE}
Log completo:            ${LOG_FILE}

Ative as ferramentas nesta sessão:
  source .security/env.sh

Confira as versões:
  semgrep --version
  bandit --version
  pip-audit --version
  checkov --version
  gitleaks version
  osv-scanner --version
  trivy --version
  syft version
  codeql version  # quando instalado

Este script apenas instala as ferramentas; ele não altera o código do fork e
não executa correções automáticas.
EOF_SUMMARY
}

main() {
  section "Instalador de segurança do fork Hermes"
  log "Projeto detectado: ${PROJECT_ROOT}."
  log "Log desta execução: ${LOG_FILE}."

  check_platform
  install_system_packages
  check_required_commands
  create_temp_root
  write_lock_header
  install_python_tools
  install_native_tools
  install_codeql_bundle
  pull_zap
  write_environment_file
  final_validation
  print_summary
}

main "$@"
