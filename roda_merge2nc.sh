#!/usr/bin/env bash
# =====================================================================
# Runner do merge2nc.py — MERGE/GPM (precip horária) -> 1 NetCDF/período.
# Roda INTERATIVAMENTE no nó de login (os dados /oper não são vistos nos nós).
#
# Uso:
#   bash roda_merge2nc.sh                       # usa DE/ATE definidos abaixo
#   bash roda_merge2nc.sh 2026010100 2026013123 # passa o período na linha de comando
#
# Para não perder o progresso se cair a conexão (períodos longos):
#   nohup bash roda_merge2nc.sh 2026010100 2026013123 > merge.log 2>&1 &
#   tail -f merge.log
# =====================================================================
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- config local (não versionada): CONDA_ENV etc. ---
[ -f "./convert2nc.env" ] && source "./convert2nc.env"

# --- ambiente conda (Python>=3.8 + eccodes) ---
: "${CONDA_ENV:=/p/projetos/grpeta/Team/jorge.gomes/conda/envs/convert2nc}"
: "${CONDA_SH:=/p/app/anaconda/etc/profile.d/conda.sh}"
module load anaconda/24.1.2 2>/dev/null || true
source "$CONDA_SH"
conda activate "$CONDA_ENV"
python --version

# =====================================================================
# PARÂMETROS — edite aqui (ou passe DE/ATE como argumentos)
# =====================================================================
DE="${1:-2026010100}"                 # AAAAMMDDHH inicial (inclusive)
ATE="${2:-2026013123}"                # AAAAMMDDHH final   (inclusive)
BASE="/oper/share/ioper/tempo/MERGE/GPM/HOURLY"
VAR="rdp"                             # variável no GRIB2 (MERGE = rdp)
ASNAME="prec"                         # nome na saída (.nc)
STEP=1                                # passo em horas
COMPLEVEL=1
OUT="MERGE_${DE}_${ATE}.nc"           # 1 arquivo com todo o período
# =====================================================================

echo ">>> MERGE $DE .. $ATE  |  $VAR -> $ASNAME  |  saída: $OUT"

python merge2nc.py "$DE" "$ATE" \
    --base "$BASE" \
    --var "$VAR" \
    --asname "$ASNAME" \
    --step "$STEP" \
    --complevel "$COMPLEVEL" \
    -o "$OUT"

echo "Fim: $(date)"
