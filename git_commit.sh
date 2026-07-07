#!/usr/bin/env bash
# =====================================================================
# Commit + push do projeto Convert2nc.
# Funciona no Git Bash (Windows) e no cluster.
# Uso:
#   bash git_commit.sh                      # mensagem automática com data/hora
#   bash git_commit.sh "minha mensagem"     # mensagem personalizada
# =====================================================================
set -euo pipefail

# vai para a pasta do próprio script (raiz do repositório)
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# confere se é um repositório git
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERRO: esta pasta não é um repositório git."
  echo "Inicialize antes (ver SUBIR_GIT.md) ou rode dentro da pasta clonada."
  exit 1
fi

MSG="${1:-Atualização Convert2nc ($(date '+%Y-%m-%d %H:%M'))}"

echo "== Alterações =="
git add -A
git status --short

if git diff --cached --quiet; then
  echo "Nada para commitar. Repositório já está em dia."
  exit 0
fi

git commit -m "$MSG"

# envia se houver um remoto 'origin' configurado
if git remote get-url origin >/dev/null 2>&1; then
  BR="$(git rev-parse --abbrev-ref HEAD)"
  echo "== Enviando para origin/$BR =="
  git push -u origin "$BR"
  echo "OK: commit enviado."
else
  echo "Commit feito localmente. (Sem remoto 'origin' — configure com: git remote add origin <URL>)"
fi
