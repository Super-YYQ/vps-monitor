#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mkdir -p "$repo/artifacts"
sandbox="$(mktemp -d "$repo/artifacts/deploy-test.XXXXXX")"
mkdir -p "$sandbox/bin"
cp "$repo/deploy.sh" "$sandbox/deploy.sh"
cat > "$sandbox/bin/docker" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DEPLOY_TEST_LOG"
exit 0
MOCK
chmod +x "$sandbox/bin/docker"
export PATH="$sandbox/bin:$PATH"
export DEPLOY_TEST_LOG="$sandbox/docker.log"
bash "$sandbox/deploy.sh" >/dev/null
[[ -f "$sandbox/.env" ]]
first_password="$(sed -n 's/^ADMIN_PASSWORD=//p' "$sandbox/.env")"
[[ "${#first_password}" -eq 48 ]]
grep -q '^BIND_ADDRESS=127.0.0.1$' "$sandbox/.env"
bash "$sandbox/deploy.sh" --domain radar.example.com >/dev/null
second_password="$(sed -n 's/^ADMIN_PASSWORD=//p' "$sandbox/.env")"
[[ "$first_password" == "$second_password" ]]
grep -q '^APP_ORIGIN=https://radar.example.com$' "$sandbox/.env"
grep -q '^SECURE_COOKIES=true$' "$sandbox/.env"
bash "$sandbox/deploy.sh" >/dev/null
grep -q 'compose --profile https up --build --detach --wait' "$sandbox/docker.log"
[[ "$(grep -c '^ADMIN_PASSWORD=' "$sandbox/.env")" -eq 1 ]]
if bash "$sandbox/deploy.sh" --domain 'https://invalid.example/path' >/dev/null 2>&1; then
  echo 'Invalid domain was accepted' >&2
  exit 1
fi
echo 'Deploy script: initial setup, HTTPS configuration, repeat execution, invalid domain checks passed.'
