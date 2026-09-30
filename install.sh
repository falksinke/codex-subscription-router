#!/bin/bash

set -euo pipefail

readonly EXPECTED_REVISION="${CODEX_SUBSCRIPTION_ROUTER_REVISION:-}"
readonly DESTINATION_APP="${HOME}/Applications/Codex Subscription Router.app"
readonly DESTINATION_HELPER="${HOME}/Applications/Codex Subscription Router Computer Use.app"

log() {
    printf '\n==> %s\n' "$1" >&2
}

fail() {
    printf '\nInstall failed: %s\n' "$1" >&2
    exit 1
}

require_prerequisites() {
    if [ "$(uname -s)" != "Darwin" ]; then
        fail "Codex Subscription Router supports macOS only."
    fi
    if [ "$(uname -m)" != "arm64" ]; then
        fail "Codex Subscription Router currently requires Apple silicon."
    fi
    if [ ! -d "/Applications/ChatGPT.app" ]; then
        fail "install the official ChatGPT app in /Applications first."
    fi

    local missing=()
    local command_name
    for command_name in git go node npm python3 security xcrun; do
        if ! command -v "${command_name}" >/dev/null 2>&1; then
            missing+=("${command_name}")
        fi
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        fail "missing prerequisites: ${missing[*]}. Install Xcode Command Line Tools, Go 1.26+, and Node.js 22.12+, then rerun this command."
    fi

    local node_major
    local node_minor
    node_major="$(node -p 'process.versions.node.split(".")[0]')"
    node_minor="$(node -p 'process.versions.node.split(".")[1]')"
    if [ "${node_major}" -lt 22 ] || { [ "${node_major}" -eq 22 ] && [ "${node_minor}" -lt 12 ]; }; then
        fail "Node.js 22.12 or newer is required; found $(node --version)."
    fi

    local go_version
    local go_major
    local go_minor
    go_version="$(go env GOVERSION | sed 's/^go//')"
    go_major="${go_version%%.*}"
    go_minor="${go_version#*.}"
    go_minor="${go_minor%%.*}"
    if [ "${go_major}" -lt 1 ] || { [ "${go_major}" -eq 1 ] && [ "${go_minor}" -lt 26 ]; }; then
        fail "Go 1.26 or newer is required; found go${go_version}."
    fi
}

resolve_source_dir() {
    local script_source="${BASH_SOURCE[0]:-}"
    local script_dir=""
    if [ -n "${script_source}" ] && [ -f "${script_source}" ]; then
        script_dir="$(CDPATH= cd -- "$(dirname -- "${script_source}")" && pwd)"
    fi
    if [ -z "${script_dir}" ] || [ ! -f "${script_dir}/scripts/patch_app.py" ]; then
        fail "run install.sh from a reviewed local Git checkout; remote shell execution is not supported."
    fi
    if ! [[ "${EXPECTED_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
        fail "set CODEX_SUBSCRIPTION_ROUTER_REVISION to the full commit SHA you reviewed."
    fi
    if [ "$(git -C "${script_dir}" rev-parse --show-toplevel)" != "${script_dir}" ]; then
        fail "install.sh must be at the root of its reviewed Git checkout."
    fi
    if [ "$(git -C "${script_dir}" rev-parse HEAD)" != "${EXPECTED_REVISION}" ]; then
        fail "the checkout does not match the reviewed revision."
    fi
    if [ -n "$(git -C "${script_dir}" status --porcelain --untracked-files=all)" ]; then
        fail "the reviewed checkout has local changes; review and commit them before installation."
    fi
    printf '%s\n' "${script_dir}"
}

main() {
    log "Checking this Mac"
    require_prerequisites

    local project_dir
    project_dir="$(resolve_source_dir)"
    cd "${project_dir}"

    if [ "${CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING:-0}" = "1" ]; then
        fail "this build requires an Apple Development or Developer ID Application certificate; ad-hoc signing cannot satisfy its library validation."
    fi

    log "Installing locked build tools"
    npm ci --ignore-scripts --no-audit --no-fund

    local patch_arguments=()
    if [ -d "${DESTINATION_APP}" ] || [ -d "${DESTINATION_HELPER}" ]; then
        patch_arguments+=("--force")
    fi
    if [ "${CODEX_SUBSCRIPTION_ROUTER_ALLOW_SIGNING_TEAM_CHANGE:-0}" = "1" ]; then
        patch_arguments+=("--allow-signing-team-change")
    fi

    log "Building and signing Codex Subscription Router"
    python3 scripts/patch_app.py "${patch_arguments[@]}"

    log "Launching Codex Subscription Router"
    env -u CODEX_CLI_PATH open "${DESTINATION_APP}"
    printf '\nInstalled successfully: %s\n' "${DESTINATION_APP}"
}

main "$@"
