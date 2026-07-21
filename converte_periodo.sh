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
# Ativa SÓ se ainda não estiver no ambiente certo. Se você já rodou
# 'conda activate <env>' antes, o script não mexe (o 'module load' pode trocar
# o python para o base do Anaconda, SEM eccodes -> ModuleNotFoundError).
: "${CONDA_ENV:=}"
: "${CONDA_SH:=/p/app/anaconda/etc/profile.d/conda.sh}"
if [ -n "$CONDA_ENV" ] && [ "${CONDA_PREFIX:-}" != "$CONDA_ENV" ]; then
    module load anaconda/24.1.2 2>/dev/null || true
    source "$CONDA_SH" 2>/dev/null || true
    conda activate "$CONDA_ENV" || { echo "!! Falha ao ativar $CONDA_ENV"; exit 1; }
fi
python --version
# eccodes só é necessário p/ GRIB2. Para binário (FORMATO=bin) não checa.
if [ "${FORMATO:-grib2}" = "grib2" ]; then
    python -c "import eccodes" 2>/dev/null || {
        echo "!! eccodes indisponível neste python ($(command -v python))."
        echo "   Ative o ambiente conda antes:  conda activate ${CONDA_ENV:-<seu_env>}"
        exit 1
    }
fi

# =====================================================================
# CONFIG (pode vir do convert2nc.env; aqui ficam os padrões)
# =====================================================================
INIT_FROM="${1:-${INIT_FROM:-2026010100}}"   # 1º arg ou convert2nc.env
INIT_TO="${2:-${INIT_TO:-2026033100}}"       # 2º arg ou convert2nc.env
STEP_H="${STEP_H:-12}"                        # passo entre rodadas (12 = 00 e 12 UTC)

FORMATO="${FORMATO:-grib2}"                    # "bin" (.ctl+.bin) ou "grib2"
BASE="${BASE:-/oper/dados/modelo/eta/ams_08km/brutos}"
# CTLREL: caminho do .ctl RELATIVO a BASE, com %INIT% (=AAAAMMDDHH) e códigos
# strftime (%Y %m %d %H). Cobre estruturas de diretório diferentes:
#   Eta grib2 oper  : "%Y/%m/%d/%H/Eta_ams_08km_%INIT%.ctl"
#   Eta binário jaci: "%INIT%/E03/Eta08_E03_%INIT%.ctl"
# (compatível: se não definir CTLREL, usa %Y/%m/%d/%H/ + CTLTPL antigo.)
CTLREL="${CTLREL:-%Y/%m/%d/%H/${CTLTPL:-Eta_ams_08km_%INIT%.ctl}}"

OUTROOT="${OUTROOT:-./nc}"                     # saída: OUTROOT/AAAAMMDDHH/
VARS="${VARS:-acpcp}"                          # nome(s) do eccodes (veja --list-vars)
GRIB_ASNAME="${GRIB_ASNAME:-}"                 # renomear 1 variável (ex.: PREC)
RENAME="${RENAME:-}"                           # renomear VÁRIAS (pares grib:novo)
COMPLEVEL="${COMPLEVEL:-1}"
JOBS="${JOBS:-4}"                              # conversões simultâneas (login: modesto!)
# =====================================================================

echo ">>> período $INIT_FROM .. $INIT_TO (passo ${STEP_H}h) | $JOBS em paralelo"
echo ">>> FORMATO=$FORMATO | saída em $OUTROOT | VARS=$VARS"

conv_one() {
    init="$1"
    y=${init:0:4}; m=${init:4:2}; d=${init:6:2}; h=${init:8:2}
    rel="${CTLREL//%INIT%/$init}"                 # troca %INIT% -> AAAAMMDDHH
    rel=$(date -u -d "$y-$m-$d $h:00:00" +"$rel")  # expande %Y/%m/%d/%H, se houver
    ctl="$BASE/$rel"
    out="$OUTROOT/$init"
    if [ ! -f "$ctl" ]; then
        echo "[--] $init sem .ctl ($ctl)"
        return
    fi
    mkdir -p "$out"
    args=(--complevel "$COMPLEVEL")
    if [ "$FORMATO" = "grib2" ]; then
        args+=(--grib --grib-engine "${GRIB_ENGINE:-cfgrib}")
        [ "${GRIB_ENGINE:-cfgrib}" = "wgrib2" ] && args+=(--wgrib2 "${WGRIB2:-wgrib2}")
    fi
    [ -n "$VARS" ] && args+=(--vars "$VARS")
    [ -n "$RENAME" ] && args+=(--rename "$RENAME")
    [ -n "$GRIB_ASNAME" ] && args+=(--asname "$GRIB_ASNAME")
    [ -n "${SPLIT:-}" ]  && args+=(--split "$SPLIT")
    [ -n "${PREFIX:-}" ] && args+=(--prefix "$PREFIX")
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
