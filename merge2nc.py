#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
merge2nc.py — Produto MERGE/GPM (precipitação horária) -> 1 NetCDF por período
--------------------------------------------------------------------------
O MERGE/CPTEC tem UM arquivo GRIB2 por HORA (acúmulo horário de precipitação),
na árvore:  BASE/AAAA/MM/DD/MERGE_CPTEC_AAAAMMDDHH.grib2

Este script lê um PERÍODO (hora a hora) e grava TODA a série num ÚNICO NetCDF
(dims time, lat, lon), em streaming (memória mínima) via eccodes — sem wgrib2.

Uso:
  python merge2nc.py 2026010100 2026013123 -o MERGE_202601.nc
  python merge2nc.py 2026010100 2026010123 --list-vars      # ver variável do GRIB2
  python merge2nc.py 2026010100 2026013123 \
      --base /oper/share/ioper/tempo/MERGE/GPM/HOURLY -o merge_jan.nc

Requer o ambiente conda (Python>=3.8 + eccodes). Veja environment.yml.
==========================================================================
"""

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


# --------------------------------------------------------------------------
# Leitores eccodes (mesma lógica do convert2nc.py, embutida p/ ser standalone)
# --------------------------------------------------------------------------
def _grib2_inventario(fn):
    from eccodes import codes_grib_new_from_file, codes_get, codes_release
    inv = {}
    with open(fn, "rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                sn = codes_get(gid, "shortName")
                tol = codes_get(gid, "typeOfLevel")
                try:
                    nm = codes_get(gid, "name")
                except Exception:
                    nm = sn
                inv.setdefault(sn, dict(typeOfLevel=tol, name=nm))
            finally:
                codes_release(gid)
    return inv


def _grib2_grade(fn, shortname):
    from eccodes import (codes_grib_new_from_file, codes_get, codes_get_array,
                         codes_release)
    with open(fn, "rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                return None
            try:
                if codes_get(gid, "shortName") != shortname:
                    continue
                ni = codes_get(gid, "Ni")
                nj = codes_get(gid, "Nj")
                try:
                    lats = np.asarray(codes_get_array(gid, "distinctLatitudes"), float)
                    lons = np.asarray(codes_get_array(gid, "distinctLongitudes"), float)
                except Exception:
                    la1 = codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                    la2 = codes_get(gid, "latitudeOfLastGridPointInDegrees")
                    lo1 = codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                    lo2 = codes_get(gid, "longitudeOfLastGridPointInDegrees")
                    lats = np.linspace(la1, la2, nj)
                    lons = np.linspace(lo1, lo2, ni)
                if lats[0] < lats[-1]:
                    lats = lats[::-1]
                if lons[0] > lons[-1]:
                    lons = lons[::-1]
                return lats, lons
            finally:
                codes_release(gid)


def _grib2_le_2d(fn, shortname):
    from eccodes import (codes_grib_new_from_file, codes_get, codes_get_values,
                         codes_release)
    with open(fn, "rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                if codes_get(gid, "shortName") != shortname:
                    continue
                ni = codes_get(gid, "Ni")
                nj = codes_get(gid, "Nj")
                a = codes_get_values(gid).reshape(nj, ni).astype("f4")
                if codes_get(gid, "jScansPositively"):
                    a = a[::-1, :]
                if codes_get(gid, "iScansNegatively"):
                    a = a[:, ::-1]
                return a
            finally:
                codes_release(gid)
    return None


# --------------------------------------------------------------------------
def caminho_merge(base, subdir_fmt, tmpl, vt):
    """BASE/AAAA/MM/DD/MERGE_CPTEC_AAAAMMDDHH.grib2 (padrão)."""
    sub = pd.Timestamp(vt).strftime(subdir_fmt)
    nome = tmpl.replace("%INIT%", pd.Timestamp(vt).strftime("%Y%m%d%H"))
    return os.path.join(base, sub, nome)


def escolhe_var(inv, pedido):
    if pedido:
        if pedido in inv:
            return pedido
        low = {k.lower(): k for k in inv}
        if pedido.lower() in low:
            return low[pedido.lower()]
        sys.exit(f"Variável '{pedido}' não está no GRIB2. Disponíveis: {list(inv)}")
    if len(inv) == 1:
        return next(iter(inv))
    for cand in ("rdp", "prec", "tp", "acpcp", "prate", "unknown"):
        for k in inv:
            if k.lower() == cand:
                return k
    return next(iter(inv))   # último recurso: a 1ª


def primeiro_existente(base, subdir_fmt, tmpl, times):
    for vt in times:
        fn = caminho_merge(base, subdir_fmt, tmpl, vt)
        if os.path.exists(fn):
            return fn
    return None


def main():
    ap = argparse.ArgumentParser(
        description="MERGE/GPM (precip horária) -> 1 NetCDF por período (eccodes).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("de", help="Data/hora inicial AAAAMMDDHH (inclusive).")
    ap.add_argument("ate", help="Data/hora final AAAAMMDDHH (inclusive).")
    ap.add_argument("-o", "--out", default=None,
                    help="NetCDF de saída (padrão: MERGE_<de>_<ate>.nc).")
    ap.add_argument("--base", default="/oper/share/ioper/tempo/MERGE/GPM/HOURLY",
                    help="Diretório base do MERGE horário.")
    ap.add_argument("--subdir", default="%Y/%m/%d",
                    help="Subpasta (strftime) dentro de base. Padrão: %%Y/%%m/%%d.")
    ap.add_argument("--tmpl", default="MERGE_CPTEC_%INIT%.grib2",
                    help="Nome do arquivo; %%INIT%% = AAAAMMDDHH.")
    ap.add_argument("--step", type=int, default=1, help="Passo em horas (padrão 1).")
    ap.add_argument("--var", default=None, help="shortName do GRIB2 (auto se omitido).")
    ap.add_argument("--asname", default="prec", help="Nome da variável na saída.")
    ap.add_argument("--list-vars", action="store_true",
                    help="Lista as variáveis do 1º GRIB2 e sai.")
    ap.add_argument("--complevel", type=int, default=1, help="Compressão zlib (1).")
    args = ap.parse_args()

    try:
        t0 = pd.to_datetime(args.de, format="%Y%m%d%H")
        t1 = pd.to_datetime(args.ate, format="%Y%m%d%H")
    except Exception:
        sys.exit("Datas devem estar no formato AAAAMMDDHH (ex.: 2026010100).")
    if t1 < t0:
        sys.exit("Data final anterior à inicial.")
    times = pd.date_range(t0, t1, freq=f"{args.step}h")
    ntime = len(times)

    f0 = primeiro_existente(args.base, args.subdir, args.tmpl, times)
    if not f0:
        sys.exit(f"Nenhum GRIB2 encontrado no período em {args.base} "
                 f"(confira --base/--subdir/--tmpl).")

    inv = _grib2_inventario(f0)
    if args.list_vars:
        print(f"Variáveis no GRIB2 ({os.path.basename(f0)}):")
        for sn, d in inv.items():
            print(f"  {sn:<14} nível={d['typeOfLevel']:<14} {d.get('name', '')}")
        return

    var = escolhe_var(inv, args.var)
    lat, lon = _grib2_grade(f0, var)
    ny, nx = len(lat), len(lon)

    out = args.out or f"MERGE_{args.de}_{args.ate}.nc"
    outdir = os.path.dirname(os.path.abspath(out))
    os.makedirs(outdir, exist_ok=True)

    print(f"MERGE {args.de}..{args.ate} (passo {args.step}h) | {ntime} tempos | "
          f"var GRIB2='{var}' -> '{args.asname}' | grade {ny}x{nx}")
    print(f"Saída: {os.path.abspath(out)}")

    import netCDF4
    fillv = np.float32(9.969209968386869e36)
    nc = netCDF4.Dataset(out, "w", format="NETCDF4")
    nc.createDimension("time", ntime)
    nc.createDimension("lat", ny)
    nc.createDimension("lon", nx)
    tv = nc.createVariable("time", "f8", ("time",))
    tv.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
    tv.calendar = "standard"
    tv[:] = [(pd.Timestamp(t) - t0).total_seconds() / 3600.0 for t in times]
    latv = nc.createVariable("lat", "f4", ("lat",))
    latv[:] = lat
    latv.units = "degrees_north"
    latv.long_name = "latitude"
    lonv = nc.createVariable("lon", "f4", ("lon",))
    lonv[:] = lon
    lonv.units = "degrees_east"
    lonv.long_name = "longitude"
    kw = dict(fill_value=fillv)
    if args.complevel and args.complevel > 0:
        kw.update(zlib=True, complevel=int(args.complevel), chunksizes=(1, ny, nx))
    dv = nc.createVariable(args.asname, "f4", ("time", "lat", "lon"), **kw)
    dv.units = "mm"
    dv.long_name = "Precipitação horária (MERGE/GPM CPTEC)"

    faltando = 0
    for ti, vt in enumerate(times):
        fn = caminho_merge(args.base, args.subdir, args.tmpl, vt)
        if not os.path.exists(fn):
            faltando += 1
            continue
        arr = _grib2_le_2d(fn, var)
        if arr is not None:
            dv[ti, :, :] = arr
        if (ti + 1) % 200 == 0:
            print(f"    ... {ti + 1}/{ntime}", flush=True)
    nc.close()

    print(f"[OK] {ntime - faltando}/{ntime} horas gravadas "
          f"({faltando} ausentes) -> {os.path.basename(out)}")


if __name__ == "__main__":
    main()
