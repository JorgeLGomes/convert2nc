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
- **Sem `multiprocessing` no binário** — evita o limite de pickle de 4 GiB por
  variável 3D (que causava `OverflowError` com `--jobs`). Por isso `--jobs` é
  ignorado no binário; ele já grava em um único passo eficiente.

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
