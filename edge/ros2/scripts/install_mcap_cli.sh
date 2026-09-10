#!/usr/bin/env bash
set -euo pipefail

version="0.3.0"
expected_sha256="be9734ef63ada9d0cc7a3aa41378ab65fd482601e5f5b3b52098d8e6553deabf"
asset_url="https://github.com/foxglove/mcap/releases/download/releases%2Fmcap-cli%2Fv${version}/mcap-linux-arm64"
install_dir="${MCAP_INSTALL_DIR:-${HOME}/.local/bin}"

temporary_dir="$(mktemp -d)"
trap 'rm -rf -- "${temporary_dir}"' EXIT
download_path="${temporary_dir}/mcap-linux-arm64"

curl --fail --location --proto '=https' --tlsv1.2 \
  --output "${download_path}" "${asset_url}"
printf '%s  %s\n' "${expected_sha256}" "${download_path}" | sha256sum --check --status
mkdir -p -- "${install_dir}"
install -m 0755 "${download_path}" "${install_dir}/mcap"
"${install_dir}/mcap" --version
