# Convert2nc

Converte saídas do modelo **Eta** para **NetCDF**, salvando **uma variável por
arquivo**. Baseado nas mesmas convenções do *Script de verificação*
(`verifica_eta_era5.py`): entrada via descritor GrADS `.ctl`, leitor nativo em
numpy para o binário e caminho `wgrib2` para GRIB2.

## Regras de conversão

- Cada variável é salva em **um único arquivo**: `<nome_variavel>_<data>.nc`.
- Variáveis **3D** são salvas com **todos os níveis** de **todos os tempos**
  (dims `time, lev, lat, lon`).
- Variáveis 2D ficam com dims `time, lat, lon`.
- `<data>` = `AAAAMMDD` do primeiro tempo (ou `--date` para forçar).
- Trata `UNDEF` → `NaN`, e as `OPTIONS` do GrADS: `byteswapped`/`big_endian`,
  `yrev`, `zrev`, `template` e `sequential`.

## Instalação

```bash
pip install numpy pandas xarray netCDF4
pip install cfgrib          # opcional: ler .grib2 direto (sem wgrib2)
# wgrib2 (binário do sistema) só é preciso para --grib via .ctl
```

## Uso

```bash
# Binário GrADS (.ctl + .bin) — leitor nativo numpy
python convert2nc.py entrada.ctl -o saida/

# GRIB2 descrito por .ctl (usa wgrib2, como no verificador)
python convert2nc.py Eta_ams_08km_2026070700.ctl -o saida/ --grib \
    --wgrib2 /caminho/para/wgrib2

# GRIB2 lido direto (sem .ctl), via cfgrib
python convert2nc.py modelo.grib2 -o saida/

# Selecionar variáveis e forçar a data do nome
python convert2nc.py entrada.ctl -o saida/ --vars tp2m,temp,uvel --date 20260707
```

## Argumentos

| Argumento     | Descrição                                                        |
|---------------|------------------------------------------------------------------|
| `entrada`     | Arquivo `.ctl` (GrADS/GRIB2) ou `.grib2` direto.                 |
| `-o/--outdir` | Diretório de saída (padrão `netcdf_out`).                        |
| `--vars`      | Variáveis a converter, separadas por vírgula (padrão: todas).    |
| `--date`      | Força a `<data>` do nome (`AAAAMMDD`). Padrão: 1º tempo do dado. |
| `--grib`      | Trata o `.ctl` como GRIB2 (usa `wgrib2`) mesmo sem `dtype grib2`.|
| `--wgrib2`    | Caminho do executável `wgrib2` (para `--grib`).                  |
| `--complevel` | Nível de compressão zlib 0–9 (`0`=sem compressão, mais rápido). Padrão: `1`. |
| `--jobs/-j`   | Processos p/ gravar variáveis em paralelo — **só GRIB2**. Padrão: `1`. |

## Desempenho e memória

O binário GrADS é convertido em **streaming, tempo a tempo**: o script abre cada
arquivo de tempo uma vez, lê a fatia daquele instante com `numpy.memmap` e a
grava direto em cada NetCDF. Isso significa:

- **Memória mínima** — nunca segura o dataset inteiro na RAM, apenas ~uma fatia
  por vez. Escala para domínios grandes (ex.: ams_08km 931×875 × 265 tempos, com
  todas as variáveis 3D e todos os níveis).
- **Cada byte lido uma vez** — leitura sequencial, ótima para o Lustre.
- **Chunk alinhado ao tempo** — cada variável é gravada com chunk de 1 instante
  (`(1, lat, lon)` no 2D; `(1, 1, lat, lon)` no 3D). Assim cada escrita tempo-a-
  tempo preenche um chunk inteiro, sem read-modify-write. Sem isso, o chunk
  padrão do HDF5 abrange vários tempos e é maior que o cache (1 MB), tornando a
  gravação **muito** lenta (uma variável 2D de 265 tempos podia levar minutos).

Compressão (`--complevel`):

- `--complevel 1` (padrão): recomendado. Em campos meteorológicos (suaves)
  comprime bem e é rápido. **Use este para variáveis 3D** — no nível 0 os `.nc`
  3D ficam enormes (uma 3D com muitos níveis × 265 tempos pode passar de 10 GB).
- `--complevel 0`: escrita mais rápida, arquivos maiores — ok para 2D ou `.nc`
  intermediários com espaço em disco sobrando.

Para reduzir tempo/disco, converta só as variáveis necessárias:

```bash
python convert2nc.py entrada.ctl -o saida/ --vars TP2M,U10M,V10M,PREC
```

`--jobs N` divide as variáveis entre N processos (cada um lê a sua parte do
disco e grava seus NetCDF — sem serializar arrays, então não há o limite de
4 GiB do `multiprocessing`). Útil para aproveitar muitos núcleos numa conversão;
o proveito vai até ~nº de variáveis do arquivo.

## Acúmulo de precipitação em 24 h (`prec_acum24h.py`)

Gera a precipitação **acumulada em 24 h** em **janela móvel de 12 h**, a partir
do `PREC_<data>.nc` já convertido (PREC horária):

```bash
python prec_acum24h.py PREC_20260101.nc            # -> PREC-ACUM24h_20260101.nc
python prec_acum24h.py PREC_20260101.nc -o saida/  --win 24 --step 12 --first 2
```

Janelas (índice de tempo 1-based, inclusivo): como o tempo 1 é a análise
(hora 0) e a acumulação começa no tempo 2 (defasagem de 1 h), as janelas são
`[2,25]`, `[14,37]`, `[26,49]`, … — 24 tempos cada, avançando de 12 em 12 h.
Cada acúmulo é rotulado pela hora do **fim** da janela. Saída:
`PREC-ACUM24h_<data_inicial>.nc` (todas as janelas no eixo `time`).

Modos (`--mode`):

- `sum` (padrão) — soma os 24 tempos da janela. Use quando a `PREC` é o
  **incremento** por passo (precipitação de cada hora).
- `diff` — `PREC[fim] - PREC[início-1]`. Use quando a `PREC` é **acumulada
  desde o início da rodada** (total corrente).

Várias datas de uma vez (aceita glob do shell):

```bash
python prec_acum24h.py ./nc/*/PREC_*.nc     # gera um PREC-ACUM24h por rodada
```

No cluster, use o `roda_prec_acum24h.pbs` (mesma lógica do batch de conversão:
descoberta por glob, filtro de período `INIT_FROM/INIT_TO`, execução paralela nos
256 núcleos). Envie com `qsub roda_prec_acum24h.pbs`.

## Submissão no cluster (PBS)

O `roda_convert2nc.pbs` (nó de 256 processadores) tem dois modos, escolhidos na
seção CONFIG do script:

- `MODE=single` — converte **um** `.ctl` usando `--jobs` processos por variável.
  Ajuste `JOBS` (útil até ~nº de variáveis do arquivo).
- `MODE=batch` — converte **vários** `.ctl` (glob de rodadas) em paralelo,
  saturando o nó: roda `256 / JOBS_PER_FILE` conversões simultâneas, cada uma com
  `JOBS_PER_FILE` processos. Cada rodada vai para `OUTROOT/AAAAMMDDHH/`.

Envie com `qsub roda_convert2nc.pbs`. Ajuste a fila (`#PBS -q`), o caminho do
venv e os caminhos dos dados. Em Lustre, mais processos = mais aberturas de
arquivo; se a IO saturar, reduza `JOBS`/`JOBS_PER_FILE`.

### Binário ou GRIB2 (`FORMATO`)

- `FORMATO="bin"` (padrão) — `.ctl` + `.bin`, leitor nativo streaming (o mais
  otimizado).
- `FORMATO="grib2"` — `.ctl` com `DTYPE grib2`; usa o `wgrib2` (defina `WGRIB2`).
  Aponte `CTL_GLOB`/`CTL` para os `.ctl` do GRIB2.

Observações do caminho GRIB2 (menos otimizado que o binário):

- Requer o executável **wgrib2** no cluster.
- A conversão é feita via `wgrib2 -netcdf`, então os **nomes das variáveis** na
  saída são os do wgrib2 (ex.: `APCP_surface`, `TMP_2maboveground`), **não** os
  nomes GrADS (`PREC`, `TP2M`). Logo, `VARS="PREC"` pode **não casar**. Rode
  primeiro `MODE=single` com `VARS=""` para ver os nomes gerados e então defina
  `VARS` com o nome do wgrib2.
- Lê para a memória (sem o streaming do binário) e usa `--jobs 1` por conversão;
  a paralelização é **entre arquivos** (`JOBS_PER_FILE` controla a concorrência).
