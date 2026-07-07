#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
prec_acum24h.py — Acúmulo móvel de precipitação (24 h a cada 12 h)
--------------------------------------------------------------------------
Lê o NetCDF de precipitação já convertido pelo Convert2nc (PREC_<data>.nc,
dims time/lat/lon, PREC horária) e gera a PRECIPITAÇÃO ACUMULADA EM 24 h,
em janela MÓVEL de 12 em 12 h.

Definição das janelas (índice de tempo 1-based, inclusivo):
  - A PREC é horária; o tempo 1 é a análise (hora 0, sem chuva). A acumulação
    começa no tempo 2 -> defasagem de 1 h (--first 2, padrão).
  - Cada janela soma 24 tempos consecutivos (--win 24).
  - Por padrão as janelas NÃO se sobrepõem (--step 24):
    [2,25], [26,49], [50,73], ... todas 01Z->00Z.
    (ex.: 265 tempos -> 11 janelas, a última [242,265]).
  - Para janela móvel sobreposta de 12 em 12 h, use --step 12:
    [2,25], [14,37], [26,49], ... (terminando alternadamente em 00Z e 12Z).

O tempo de cada acúmulo é rotulado pelo FIM da janela (hora em que os 24 h se
completam), que é a convenção usual de "precipitação acumulada em 24 h".

Saída:  PREC-ACUM24h_<data_inicial>.nc  (uma variável, todas as janelas no time)

Uso:
  python prec_acum24h.py PREC_20260101.nc
  python prec_acum24h.py PREC_20260101.nc -o saida/ --win 24 --step 12 --first 2
  python prec_acum24h.py PREC_20260101.nc --mode diff   # se PREC for acumulada
                                                          # desde o início da rodada

Modos (--mode):
  sum  (padrão) -> soma os 24 tempos da janela. Use quando PREC é o INCREMENTO
                   por passo (precip. de cada hora).
  diff          -> PREC[fim] - PREC[início-1]. Use quando PREC é ACUMULADA
                   desde o início da rodada (total corrente).
"""

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd
import xarray as xr


def detecta_var(ds, preferida=None):
    """Escolhe a variável de precipitação: --varname, ou 'prec'/'PREC', ou a 1ª."""
    if preferida and preferida in ds.data_vars:
        return preferida
    for v in ds.data_vars:
        if str(v).lower() in ("prec", "precip", "prec_acum", "tp", "aprec"):
            return v
    vars3 = [v for v in ds.data_vars if {"time"}.issubset(set(ds[v].dims))]
    if not vars3:
        sys.exit("Nenhuma variável com dimensão 'time' no arquivo.")
    return vars3[0]


def data_inicial(inp, ds, forcado=None):
    """Data para o nome de saída: --date, ou token AAAAMMDD do nome, ou 1º tempo."""
    if forcado:
        return forcado
    m = re.search(r"(\d{8})", os.path.basename(inp))
    if m:
        return m.group(1)
    try:
        return pd.Timestamp(np.asarray(ds["time"].values).ravel()[0]).strftime("%Y%m%d")
    except Exception:
        return "00000000"


def janelas(nt, win, step, first):
    """Lista de (ini, fim) 1-based inclusivo das janelas móveis."""
    js = []
    s = first
    while s + win - 1 <= nt:
        js.append((s, s + win - 1))
        s += step
    return js


def main():
    ap = argparse.ArgumentParser(
        description="Acúmulo móvel de precipitação em 24 h, a cada 12 h.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", help="NetCDF de PREC (dims time/lat/lon).")
    ap.add_argument("-o", "--outdir", default=None,
                    help="Diretório de saída (padrão: mesmo do arquivo de entrada).")
    ap.add_argument("--win", type=int, default=24, help="Nº de tempos por janela (24).")
    ap.add_argument("--step", type=int, default=12,
                    help="Passo entre janelas, em tempos. 12 = janela móvel "
                         "sobreposta de 12 em 12 h (padrão); 24 = sem sobreposição.")
    ap.add_argument("--first", type=int, default=2,
                    help="Índice 1-based do 1º tempo de acúmulo (2 = defasagem de 1 h).")
    ap.add_argument("--mode", choices=["sum", "diff"], default="sum",
                    help="'sum' (PREC incremental, padrão) ou 'diff' (PREC acumulada).")
    ap.add_argument("--varname", default=None, help="Nome da variável de PREC (auto).")
    ap.add_argument("--date", default=None, help="Força a <data_inicial> do nome de saída.")
    ap.add_argument("--complevel", type=int, default=1, help="Compressão zlib (1).")
    args = ap.parse_args()

    if not os.path.exists(args.entrada):
        sys.exit(f"Arquivo não encontrado: {args.entrada}")

    ds = xr.open_dataset(args.entrada)
    var = detecta_var(ds, args.varname)
    da = ds[var]
    if "time" not in da.dims:
        sys.exit(f"Variável '{var}' não tem dimensão 'time'.")

    nt = da.sizes["time"]
    js = janelas(nt, args.win, args.step, args.first)
    if not js:
        sys.exit(f"Nenhuma janela cabe: nt={nt}, win={args.win}, first={args.first}.")

    print(f"Variável: {var} | tempos={nt} | win={args.win} step={args.step} "
          f"first={args.first} | modo={args.mode}")
    print(f"{len(js)} janela(s): {js[0]} ... {js[-1]} (índices 1-based, inclusivo)")

    tempos = pd.to_datetime(np.asarray(da["time"].values))
    campos, tempos_fim = [], []
    for (ini, fim) in js:
        i0 = ini - 1                      # -> 0-based
        i1 = fim                          # slice exclusivo no fim
        if args.mode == "sum":
            acc = da.isel(time=slice(i0, i1)).sum("time", skipna=False)
        else:  # diff: PREC[fim] - PREC[ini-1]
            acc = da.isel(time=i1 - 1) - da.isel(time=i0 - 1)
        campos.append(acc)
        tempos_fim.append(tempos[i1 - 1])   # rótulo = fim da janela

    out = xr.concat(campos, dim="time")
    out = out.assign_coords(time=("time", pd.DatetimeIndex(tempos_fim)))
    out.name = "PREC_ACUM24h"
    unid = da.attrs.get("units", "mm")
    out.attrs.update(
        units=unid,
        long_name="Precipitação acumulada em 24 h (janela móvel de 12 h)",
        cell_methods="time: sum (24 h)",
        acumulacao_horas=args.win,
        passo_horas=args.step,
        primeiro_tempo_indice=args.first,
    )
    for c in ("lat", "lon"):
        if c in ds:
            out[c].attrs.update(ds[c].attrs)

    ny = out.sizes.get("lat")
    nx = out.sizes.get("lon")
    enc = {"PREC_ACUM24h": {"_FillValue": np.float32(9.969209968386869e36)}}
    if args.complevel and args.complevel > 0:
        enc["PREC_ACUM24h"].update(zlib=True, complevel=int(args.complevel),
                                   chunksizes=(1, ny, nx))

    data = data_inicial(args.entrada, ds, args.date)
    outdir = args.outdir or os.path.dirname(os.path.abspath(args.entrada))
    os.makedirs(outdir, exist_ok=True)
    caminho = os.path.join(outdir, f"PREC-ACUM24h_{data}.nc")
    out.to_dataset(name="PREC_ACUM24h").to_netcdf(caminho, format="NETCDF4", encoding=enc)
    ds.close()

    print(f"[OK] {len(js)} acúmulos -> {caminho}")


if __name__ == "__main__":
    main()
