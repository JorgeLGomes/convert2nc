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
| `--jobs/-j`   | Nº de processos para gravar variáveis em paralelo. Padrão: `1`.  |

## Desempenho

O leitor do binário GrADS usa `numpy.memmap` com leitura em bloco (uma única
abertura por arquivo e fatiamento vetorizado por variável), em vez de ler campo
a campo. O ganho é maior em sistemas de arquivos de rede (ex.: Lustre), onde
abrir arquivos é caro.

O gargalo costuma ser a **escrita compactada**. Ajuste conforme a necessidade:

- `--complevel 1` (padrão): bom equilíbrio; em campos meteorológicos (suaves)
  comprime quase como o nível 4 gastando muito menos tempo.
- `--complevel 0`: escrita muito mais rápida (arquivos maiores) — útil quando há
  espaço em disco sobrando ou os `.nc` são intermediários.
- `--jobs N`: grava N variáveis em paralelo (≈ nº de variáveis). ~2x com 4 jobs.

Exemplo rápido (sem compressão + paralelo):

```bash
python convert2nc.py entrada.ctl -o saida/ --complevel 0 --jobs 4
```
