#!/usr/bin/env bash
set -euo pipefail

REPO="${1:-voxclode}"
DESCRIPTION="${DESCRIPTION:-VoxCLode — colourful terminal studio for VoxCPM2 voice cloning}"
TOKEN="${GH_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "Задайте GH_TOKEN в окружении. Не вставляйте токен в файлы проекта." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Проверяю токен и пользователя…"
USER_JSON="$(curl -fsS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/user)"
OWNER="$(printf '%s' "$USER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["login"])')"
echo "Пользователь: $OWNER"

PAYLOAD="$(python3 - "$REPO" "$DESCRIPTION" <<'PY'
import json, sys
print(json.dumps({
    "name": sys.argv[1],
    "description": sys.argv[2],
    "private": False,
    "has_issues": True,
    "has_wiki": False,
}, ensure_ascii=False))
PY
)"

if curl -fsS -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/user/repos \
  -d "$PAYLOAD" >/tmp/voxclode-repo.json; then
  echo "Репозиторий создан."
else
  echo "Репозиторий уже существует или API вернул ошибку; продолжаю."
fi

if [[ ! -d .git ]]; then
  git init -b main >/dev/null
fi

git add -A
if ! git diff --cached --quiet; then
  git -c user.name="VoxCLode" -c user.email="voxclode@users.noreply.github.com" \
    commit -m "VoxCLode: initial public release" >/dev/null
fi

git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"

# Токен передаётся только в заголовке текущей команды и не сохраняется в .git/config.
BASIC="$(printf 'x-access-token:%s' "$TOKEN" | base64 | tr -d '\n')"
git -c http.extraheader="AUTHORIZATION: basic $BASIC" push -u origin main

echo "Готово: https://github.com/$OWNER/$REPO"
