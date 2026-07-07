#!/usr/bin/env bash
# =====================================================================
# Commit (+ push) do projeto Convert2nc.
# Funciona no Git Bash (Windows), Linux/macOS e no cluster.
#
# Faz tudo em um só comando:
#   - inicializa o repositório na 1ª vez (git init + identidade + branch main)
#   - configura o remoto 'origin' se você passar a URL
#   - git add -A + commit + push
#
# Uso:
#   bash git_commit.sh                                  # mensagem automática
#   bash git_commit.sh "minha mensagem"                 # mensagem personalizada
#   bash git_commit.sh "primeiro commit" <URL-do-repo>  # define origin e faz push
#
# Ex.:
#   bash git_commit.sh "primeiro commit" git@github.com:JorgeLGomes/convert2nc.git
# =====================================================================
set -euo pipefail

# vai para a pasta do próprio script (raiz do repositório)
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MSG="${1:-Atualização Convert2nc ($(date '+%Y-%m-%d %H:%M'))}"
REMOTE_URL="${2:-}"

GIT_NAME="Jorge Gomes"
GIT_EMAIL="jorgeluisgomes@gmail.com"

# --- 1) inicializa o repositório se ainda não existir ---
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "== Inicializando repositório git =="
  git init
  git config user.name  "$GIT_NAME"
  git config user.email "$GIT_EMAIL"
  git branch -M main
fi

# garante identidade (caso ainda não esteja definida)
git config user.name  >/dev/null 2>&1 || git config user.name  "$GIT_NAME"
git config user.email >/dev/null 2>&1 || git config user.email "$GIT_EMAIL"

# --- 2) configura o remoto 'origin' se a URL foi passada ---
if [[ -n "$REMOTE_URL" ]]; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$REMOTE_URL"
  else
    git remote add origin "$REMOTE_URL"
  fi
  echo "== Remoto origin = $REMOTE_URL =="
fi

# --- 3) add + commit ---
echo "== Alterações =="
git add -A
git status --short

if git diff --cached --quiet; then
  echo "Nada para commitar. Repositório já está em dia."
  exit 0
fi

git commit -m "$MSG"

# --- 4) push (se houver 'origin') ---
if git remote get-url origin >/dev/null 2>&1; then
  BR="$(git rev-parse --abbrev-ref HEAD)"
  echo "== Enviando para origin/$BR =="
  git push -u origin "$BR"
  echo "OK: commit enviado."
else
  echo "Commit feito localmente."
  echo "Para enviar ao GitHub, rode novamente com a URL do repositório, ex.:"
  echo "  bash git_commit.sh \"$MSG\" git@github.com:JorgeLGomes/convert2nc.git"
fi
