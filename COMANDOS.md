# Comandos — conversão de múltiplas variáveis

Regras gerais:

- `--vars a,b,c` converte várias variáveis de uma vez → **um arquivo por
  variável** (`a_<data>.nc`, `b_<data>.nc`, …). Sem `--vars` = todas.
- Variáveis **3D** saem com **todos os níveis** e **todos os tempos**.
- `--asname` só renomeia quando se seleciona **uma** variável (ignora se >1).
- Descubra os nomes das variáveis antes:
  - Binário GrADS: nomes do `.ctl` (maiúsc./minúsc. conforme o GrADS).
  - GRIB2: nomes do eccodes → `--list-vars`.

---

## Eta — binário GrADS (.ctl + .bin)

```bash
# listar (veja o cabeçalho do .ctl) e converter algumas variáveis:
python convert2nc.py Eta08_E03_2026010100.ctl --vars TP2M,U10M,V10M,PREC,TEMP -o ./

# TODAS as variáveis, usando vários núcleos (divide as variáveis entre processos):
python convert2nc.py Eta08_E03_2026010100.ctl --jobs 16 -o ./

# só 3D (todos os níveis) — ex.: temperatura e vento em níveis de pressão:
python convert2nc.py Eta08_E03_2026010100.ctl --vars TEMP,UVEL,VVEL,OMEG --jobs 8 -o ./
```

## Eta — GRIB2 (.ctl com DTYPE grib2, motor eccodes, SEM wgrib2)

```bash
# 1) ver os nomes (shortName) do GRIB2:
python convert2nc.py Eta_ams_08km_2026010100.ctl --grib --list-vars

# 2) converter várias variáveis (cada uma vira um .nc):
python convert2nc.py Eta_ams_08km_2026010100.ctl --grib \
    --vars 2t,10u,10v,prmsl -o ./

# renomear VÁRIAS variáveis na saída (--rename): duas formas equivalentes
#   (a) lista na mesma ordem de --vars:
python convert2nc.py Eta_ams_08km_2026010100.ctl --grib \
    --vars 2t,10u,10v,prmsl --rename tp2m,u10m,v10m,pslm -o ./
#   (b) pares grib:novo (independe da ordem):
python convert2nc.py Eta_ams_08km_2026010100.ctl --grib \
    --vars 2t,10u,10v,prmsl --rename 2t:tp2m,10u:u10m,10v:v10m,prmsl:pslm -o ./
# -> gera tp2m_<data>.nc, u10m_<data>.nc, v10m_<data>.nc, pslm_<data>.nc

# uma só, renomeando a saída (ex.: acpcp -> PREC):
python convert2nc.py Eta_ams_08km_2026010100.ctl --grib --vars acpcp --asname PREC -o ./
```

## Em lote, por período (interativo, nó de login)

No `convert2nc.env`, ponha a lista em `VARS` e deixe `GRIB_ASNAME` vazio (só use
asname com 1 variável):

```bash
# convert2nc.env
VARS="tmp2m,ugrd10m,vgrd10m,acpcp"
GRIB_ASNAME=""
```

```bash
bash converte_periodo.sh 2026010100 2026033100
```
Cada rodada gera os arquivos das variáveis em `./nc/<init>/`.

## MERGE/GPM (precip horária) — período em 1 arquivo

O MERGE tem só a variável `rdp` (salva como `prec`):

```bash
python merge2nc.py 2026010100 2026013123 --var rdp --asname prec -o MERGE_202601.nc
# ou pelo runner:
bash roda_merge2nc.sh 2026010100 2026013123
```

## Acúmulo de 24 h da precipitação (janela móvel de 12 h)

```bash
# uma rodada / um arquivo:
python prec_acum24h.py ./nc/2026010100/PREC_20260101.nc

# várias de uma vez:
python prec_acum24h.py ./nc/*/PREC_*.nc

# se a PREC for acumulada desde o início da rodada, use --mode diff:
python prec_acum24h.py ./nc/*/PREC_*.nc --mode diff
```
