#!/usr/bin/env bash
# =====================================================================
# Cria um ambiente virtual Python (venv) para o Convert2nc e instala as
# dependências do requirements.txt. Funciona no Linux/macOS e no Git Bash.
#
# Uso:
#   bash setup_env.sh                 # cria ./.venv e instala tudo
#   bash setup_env.sh minhaenv        # usa a pasta ./minhaenv
#
# Depois, para ATIVAR o ambiente:
#   Linux/macOS/Git Bash:  source .venv/bin/activate
#   Windows PowerShell:    .\.venv\Scripts\Activate.ps1
#   Windows CMD:           .\.venv\Scripts\activate.bat
# =====================================================================
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENVDIR="${1:-.venv}"

# escolhe o executável Python disponível
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [[ -z "$PY" ]]; then
  echo "ERRO: Python não encontrado no PATH."; exit 1
fi
echo "== Usando: $($PY --version) =="

# cria o venv se ainda não existir
if [[ ! -d "$ENVDIR" ]]; then
  echo "== Criando venv em ./$ENVDIR =="
  "$PY" -m venv "$ENVDIR"
fi

# caminho do pip do venv (Linux/macOS vs Windows)
if [[ -x "$ENVDIR/bin/pip" ]]; then
  PIP="$ENVDIR/bin/pip"
else
  PIP="$ENVDIR/Scripts/pip.exe"
fi

echo "== Atualizando pip =="
"$PIP" install --upgrade pip >/dev/null

echo "== Instalando dependências (requirements.txt) =="
"$PIP" install -r requirements.txt

echo
echo "== Pronto! Para ativar o ambiente: =="
echo "   Linux/macOS/Git Bash:  source $ENVDIR/bin/activate"
echo "   Windows PowerShell:    .\\$ENVDIR\\Scripts\\Activate.ps1"
echo
echo "Depois rode, por exemplo:"
echo "   python convert2nc.py entrada.ctl -o saida/"
