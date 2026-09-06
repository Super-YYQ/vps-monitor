#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
domain=""
if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || "$1" != "--domain" ]]; then
    echo 'Usage: bash deploy.sh [--domain radar.example.com]' >&2
    exit 1
  fi
  domain="$2"
  if [[ ! "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,63}$ ]]; then
    echo 'Please provide a domain name, without scheme, path, or port.' >&2
    exit 1
  fi
fi
if ! command -v docker >/dev/null 2>&1; then
  echo 'Installing Docker using the official Docker installer...'
  command -v curl >/dev/null 2>&1 || { echo 'Please install curl first.' >&2; exit 1; }
  installer="$(mktemp)"
  trap 'rm -f -- "$installer"' EXIT
  curl --fail --silent --show-error --location https://get.docker.com -o "$installer"
  if [[ "$EUID" -eq 0 ]]; then sh "$installer"; else sudo sh "$installer"; fi
  rm -f -- "$installer"
  trap - EXIT
fi
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  if [[ "$EUID" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  fi
fi
"${docker_cmd[@]}" info >/dev/null || { echo 'Start the Docker service, then retry.' >&2; exit 1; }
"${docker_cmd[@]}" compose version >/dev/null || { echo 'Install the Docker Compose v2 plugin, then retry.' >&2; exit 1; }
if [[ ! -f .env ]]; then
  admin_password="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  printf 'ADMIN_PASSWORD=%s\nBIND_ADDRESS=127.0.0.1\nPORT=8080\nSECURE_COOKIES=false\nAPP_ORIGIN=\nDOMAIN=\n' "$admin_password" > .env
  echo 'Generated initial credentials in .env (readable only by the current user).'
fi
chmod 600 .env
if [[ -n "$domain" ]]; then
  # Never source .env as shell code; preserve credentials and unrelated settings.
  env_temp="$(mktemp .env.XXXXXX)"
  awk -v domain="$domain" '
    !/^(DOMAIN|APP_ORIGIN|SECURE_COOKIES|BIND_ADDRESS)=/ { print }
    END { print "DOMAIN=" domain; print "APP_ORIGIN=https://" domain;
          print "SECURE_COOKIES=true"; print "BIND_ADDRESS=127.0.0.1" }
  ' .env > "$env_temp"
  mv -- "$env_temp" .env
fi
saved_domain="$(sed -n 's/^DOMAIN=//p' .env | tail -n 1)"
profile=()
if [[ -n "$saved_domain" ]]; then profile=(--profile https); fi
"${docker_cmd[@]}" compose "${profile[@]}" config --quiet
"${docker_cmd[@]}" compose "${profile[@]}" up --build --detach --wait --wait-timeout 180
echo 'VPS Radar is running. Initial admin password: see ADMIN_PASSWORD in .env.'
if [[ -n "$saved_domain" ]]; then
  printf 'Open https://%s (DNS must point here and ports 80/443 must be reachable).\n' "$saved_domain"
else
  echo 'Open http://127.0.0.1:8080. On a remote server use an SSH tunnel:'
  echo '  ssh -L 8080:127.0.0.1:8080 user@server'
fi
