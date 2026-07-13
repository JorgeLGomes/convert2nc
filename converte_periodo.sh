#!/usr/bin/env bash
# =====================================================================
# Conversão INTERATIVA em lote, por PERÍODO de rodada (nó de login).
# Use quando os dados (/oper/...) NÃO são visíveis dos nós de processamento,
# então não dá para usar o PBS. Roda direto no ian01.
#
# Uso:
#   bash converte_periodo.sh 2026010100 2026033100
#   bash converte_periodo.sh                 # usa INIT_FROM/INIT_TO do convert2nc.env
#
# Dica: para não perder o progresso se cair a conexão, rode dentro de tmux/screen
# ou com nohup:
#   nohup bash converte_periodo.sh 2026010100 2026033100 > periodo.log 2>&1 &
#   tail -f periodo.log
# =====================================================================
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- config local (não versionada): CONDA_ENV, VARS, GRIB_ASNAME, etc. ---
[ -f "./convert2nc.env" ] && source "./convert2nc.env"

# --- ambiente conda (necessário p/ GRIB2/eccodes) ---
: "${CONDA_ENV:=}"
: "${CONDA_SH:=/p/app/anaconda/etc/profile.d/conda.sh}"
if [ -n "$CONDA_ENV" ]; then
    module load anaconda/24.1.2 2>/dev/null || true
    source "$CONDA_SH"
    conda activate "$CONDA_ENV"
fi
python --version

# =====================================================================
# CONFIG (pode vir do convert2nc.env; aqui ficam os padrões)
# =====================================================================
INIT_FROM="${1:-${INIT_FROM:-2026010100}}"   # 1º arg ou convert2nc.env
INIT_TO="${2:-${INIT_TO:-2026033100}}"       # 2º arg ou convert2nc.env
STEP_H="${STEP_H:-12}"                        # passo entre rodadas (12 = 00 e 12 UTC)

# árvore dos dados oper e nome do .ctl (dentro de AAAA/MM/DD/HH/).
# Use %INIT% como marcador da data de inicialização (AAAAMMDDHH).
BASE="${BASE:-/oper/dados/modelo/eta/ams_08km/brutos}"
CTLTPL="${CTLTPL:-Eta_ams_08km_%INIT%.ctl}"

OUTROOT="${OUTROOT:-./nc}"                     # saída: OUTROOT/AAAAMMDDHH/
VARS="${VARS:-acpcp}"                          # nome(s) do eccodes (veja --list-vars)
GRIB_ASNAME="${GRIB_ASNAME:-}"                 # renomear 1 variável (ex.: PREC)
RENAME="${RENAME:-}"                           # renomear VÁRIAS (pares grib:novo)
COMPLEVEL="${COMPLEVEL:-1}"
JOBS="${JOBS:-4}"                              # conversões simultâneas (login: modesto!)
# =====================================================================

echo ">>> período $INIT_FROM .. $INIT_TO (passo ${STEP_H}h) | $JOBS em paralelo"
echo ">>> saída em $OUTROOT | VARS=$VARS ASNAME=$GRIB_ASNAME"

conv_one() {
    init="$1"
    y=${init:0:4}; m=${init:4:2}; d=${init:6:2}; h=${init:8:2}
    ctl="$BASE/$y/$m/$d/$h/${CTLTPL/'%INIT%'/$init}"
    out="$OUTROOT/$init"
    if [ ! -f "$ctl" ]; then
        echo "[--] $init sem .ctl ($ctl)"
        return
    fi
    mkdir -p "$out"
    args=(--grib --complevel "$COMPLEVEL")
    [ -n "$VARS" ] && args+=(--vars "$VARS")
    [ -n "$RENAME" ] && args+=(--rename "$RENAME")
    [ -n "$GRIB_ASNAME" ] && args+=(--asname "$GRIB_ASNAME")
    echo "[$(date +%T)] início $init"
    if python convert2nc.py "$ctl" -o "$out" "${args[@]}" > "$out/convert_${init}.log" 2>&1; then
        echo "[$(date +%T)] OK   $init"
    else
        echo "[$(date +%T)] FALHA $init (veja $out/convert_${init}.log)"
    fi
}

# --- laço do período com pool de no máx. $JOBS processos ---
cur="$INIT_FROM"
while [ "$((10#$cur))" -le "$((10#$INIT_TO))" ]; do
    conv_one "$cur" &
    while [ "$(jobs -r | wc -l)" -ge "$JOBS" ]; do wait -n; done
    y=${cur:0:4}; m=${cur:4:2}; d=${cur:6:2}; h=${cur:8:2}
    ep=$(date -u -d "$y-$m-$d $h:00:00" +%s)
    ep=$((ep + STEP_H * 3600))
    cur=$(date -u -d "@$ep" +%Y%m%d%H)
done
wait
echo "Fim: $(date)"
