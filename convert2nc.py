#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==========================================================================
convert2nc.py — Conversão de saídas do Eta para NetCDF (1 var/arquivo)
--------------------------------------------------------------------------
Baseado nas MESMAS convenções do "Script de verificação" (verifica_eta_era5.py):
  - Entrada sempre via descritor GrADS .ctl (igual ao verificador), que aponta
    para dados BINÁRIOS (.bin) OU GRIB2 — a conversão prevê os dois casos.
  - Leitor nativo em numpy para o binário (como read_eta_native), aqui ESTENDIDO
    para variáveis 3D (TODOS os níveis) e TODOS os tempos.
  - Caminho wgrib2 para GRIB2 (como read_eta_wgrib2), também estendido a 3D.
  - Mesmo tratamento de OPTIONS (byteswapped/big_endian, yrev, zrev, template),
    UNDEF, XDEF/YDEF, e template de nome (%y4%m2%d2%h2).

Objetivo desta ferramenta:
  * Salvar CADA variável em UM ÚNICO arquivo NetCDF.
  * Variáveis 3D salvas com TODOS os níveis de TODOS os tempos.
  * Nomenclatura de saída: <nome_variavel>_<data>.nc   (data = AAAAMMDD do 1º tempo)

Uso:
  # Binário GrADS (.ctl + .bin):
  python convert2nc.py entrada.ctl -o saida/

  # GRIB2 descrito por .ctl (usa wgrib2, como no verificador):
  python convert2nc.py Eta_ams_08km_2026070700.ctl -o saida/ --grib

  # GRIB2 lido direto (sem .ctl), via cfgrib:
  python convert2nc.py modelo.grib2 -o saida/

  # Selecionar variáveis e forçar a data do nome:
  python convert2nc.py entrada.ctl -o saida/ --vars tp2m,u10m,v10m --date 20260707

Dependências:
  pip install numpy pandas xarray netCDF4
  # opcional, leitura direta de .grib2 sem wgrib2:
  pip install cfgrib
  # wgrib2 (binário do sistema) é necessário só para --grib via .ctl.

Autor: gerado para Jorge Gomes / projeto Convert2nc.
==========================================================================
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import xarray as xr


# =========================================================================
# Parser do .ctl  (estende _parse_ctl do verificador: + ZDEF, TDEF, TITLE, grib)
# =========================================================================
_MESES = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _grads_time(token):
    """Converte data GrADS (ex.: '00Z07JUL2026', '06:30Z07JUL2026') -> Timestamp."""
    t = token.strip().lower()
    m = re.match(r"(?:(\d{1,2})(?::(\d{2}))?z)?(\d{1,2})([a-z]{3})(\d{4})", t)
    if not m:
        # fallback: tenta pandas
        return pd.Timestamp(token)
    hh = int(m.group(1) or 0)
    mm = int(m.group(2) or 0)
    dd = int(m.group(3))
    mon = _MESES.get(m.group(4), 1)
    yy = int(m.group(5))
    return pd.Timestamp(year=yy, month=mon, day=dd, hour=hh, minute=mm)


def _grads_step(token):
    """Converte incremento GrADS (ex.: '6hr', '30mn', '1dy', '1mo') -> DateOffset/Timedelta."""
    m = re.match(r"(\d+)\s*([a-z]{2})", token.strip().lower())
    if not m:
        return pd.Timedelta(hours=6)
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "mn":
        return pd.Timedelta(minutes=n)
    if unit == "hr":
        return pd.Timedelta(hours=n)
    if unit == "dy":
        return pd.Timedelta(days=n)
    if unit == "mo":
        return pd.DateOffset(months=n)
    if unit == "yr":
        return pd.DateOffset(years=n)
    return pd.Timedelta(hours=n)


def parse_ctl(ctl_path):
    """Lê o .ctl inteiro: DSET, OPTIONS, UNDEF, XDEF, YDEF, ZDEF, TDEF, VARS, TITLE.

    Retorna dict com, entre outros:
      dset, options, undef, title, is_grib
      nx, x0, dx, ny, y0, dy
      levels (np.array), times (DatetimeIndex)
      vars: lista de (nome, nlev, units, desc)
    """
    ctldir = os.path.dirname(os.path.abspath(ctl_path))
    info = {"vars": [], "options": "", "title": ""}
    lines = open(ctl_path, encoding="latin-1").read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        low = line.lower()
        if not line or line.startswith("*"):
            i += 1
            continue
        if low.startswith("dset"):
            d = line.split(None, 1)[1].strip()
            info["dset"] = os.path.join(ctldir, d[1:]) if d.startswith("^") else d
        elif low.startswith("title"):
            info["title"] = line.split(None, 1)[1].strip() if " " in line else ""
        elif low.startswith("dtype"):
            info["options"] += " " + low            # ex.: 'dtype grib2'
        elif low.startswith("options"):
            info["options"] += " " + low
        elif low.startswith("undef"):
            info["undef"] = float(line.split()[1])
        elif low.startswith("xdef"):
            p = line.split()
            info.update(nx=int(p[1]), x0=float(p[3]), dx=float(p[4]))
        elif low.startswith("ydef"):
            p = line.split()
            info.update(ny=int(p[1]), y0=float(p[3]), dy=float(p[4]))
        elif low.startswith("zdef"):
            p = line.split()
            nz = int(p[1])
            ztype = p[2].lower()
            if ztype == "linear":
                z0 = float(p[3])
                dz = float(p[4])
                info["levels"] = z0 + np.arange(nz) * dz
            else:  # LEVELS (podem continuar em várias linhas)
                vals = [float(x) for x in p[3:]]
                while len(vals) < nz and i + 1 < len(lines):
                    i += 1
                    vals += [float(x) for x in lines[i].split()]
                info["levels"] = np.array(vals[:nz], dtype=float)
        elif low.startswith("tdef"):
            p = line.split()
            nt = int(p[1])
            # p[2] geralmente 'linear'
            t0 = _grads_time(p[3])
            step = _grads_step(p[4])
            if isinstance(step, pd.Timedelta):
                info["times"] = pd.DatetimeIndex([t0 + k * step for k in range(nt)])
            else:  # DateOffset (mo/yr)
                info["times"] = pd.DatetimeIndex([t0 + k * step for k in range(nt)])
        elif low.startswith("vars"):
            try:
                n = int(line.split()[1])
            except (IndexError, ValueError):
                n = 0
            count = 0
            while count < n and i + 1 < len(lines):
                i += 1
                vl = lines[i]
                if vl.strip().lower().startswith("endvars"):
                    break
                p = vl.split()
                if len(p) >= 2:
                    name = p[0]
                    try:
                        nlev = int(p[1])
                    except ValueError:
                        # ctl GRIB2: 2º campo pode não ser inteiro (ex.: '0,1,0')
                        m = re.match(r"(\d+)", p[1])
                        nlev = int(m.group(1)) if m else 0
                    units = p[2] if len(p) > 2 else ""
                    desc = " ".join(p[3:]) if len(p) > 3 else ""
                    info["vars"].append((name, nlev, units, desc))
                    count += 1
        i += 1

    # defaults sensatos
    info.setdefault("undef", -9.99e8)
    info.setdefault("levels", np.array([0.0]))
    if "times" not in info:
        info["times"] = pd.DatetimeIndex([pd.Timestamp.now().normalize()])
    info["is_grib"] = ("grib" in info["options"])
    info["byteswap"] = ("byteswapped" in info["options"]
                        or "big_endian" in info["options"])
    info["yrev"] = "yrev" in info["options"]
    info["zrev"] = "zrev" in info["options"]
    info["template"] = "template" in info["options"]
    info["sequential"] = "sequential" in info["options"]
    return info


def _tmpl_name(dset, vt):
    """Substitui o template GrADS (%y4%m2%d2%h2) pela data válida (igual verificador)."""
    vt = pd.Timestamp(vt)
    return (dset.replace("%y4", f"{vt.year:04d}")
            .replace("%y2", f"{vt.year % 100:02d}")
            .replace("%m2", f"{vt.month:02d}")
            .replace("%d2", f"{vt.day:02d}")
            .replace("%h2", f"{vt.hour:02d}"))


# =========================================================================
# Leitor nativo do BINÁRIO GrADS -> xarray.Dataset (3D, todos níveis e tempos)
# =========================================================================
def read_grads_binary(info, wanted=None):
    """Lê TODAS (ou as selecionadas) variáveis do binário GrADS.

    - 2D  -> dims (time, lat, lon)
    - 3D  -> dims (time, lev, lat, lon), com TODOS os níveis
    - todos os tempos do TDEF (arquivo único) ou por-tempo (OPTIONS template)
    """
    nx, ny = info["nx"], info["ny"]
    fld = nx * ny
    undef = info["undef"]
    lat = info["y0"] + np.arange(ny) * info["dy"]
    lon = info["x0"] + np.arange(nx) * info["dx"]
    levels = info["levels"]
    times = info["times"]
    dtype = ">f4" if info["byteswap"] else "<f4"
    yrev, zrev = info["yrev"], info["zrev"]
    seq = info["sequential"]
    var_list = info["vars"]

    # nº de campos 2D por variável e offset (em campos) dentro de um bloco de tempo
    offsets, nfields_var, fields_per_time = {}, {}, 0
    for (name, nlev, _u, _d) in var_list:
        k = max(1, nlev)
        offsets[name] = fields_per_time
        nfields_var[name] = k
        fields_per_time += k

    if wanted:
        wl = [w.strip() for w in wanted]
        sel = [v for v in var_list if v[0] in wl or v[0].lower() in [w.lower() for w in wl]]
        faltando = [w for w in wl if w.lower() not in [v[0].lower() for v in var_list]]
        if faltando:
            print(f"  Aviso: variáveis não encontradas no .ctl e ignoradas: {faltando}")
    else:
        sel = var_list
    if not sel:
        raise RuntimeError("Nenhuma variável selecionada existe no .ctl.")

    # Fortran 'sequential': cada registro tem 4 bytes de marca antes e depois
    def read_field(f, field_index):
        if seq:
            # marca(4) + dados(fld*4) + marca(4) por registro 2D
            f.seek(field_index * (fld * 4 + 8) + 4)
        else:
            f.seek(field_index * fld * 4)
        a = np.fromfile(f, dtype=dtype, count=fld)
        if a.size < fld:                       # arquivo truncado
            a = np.concatenate([a, np.full(fld - a.size, np.nan, dtype="f4")])
        a = a.reshape(ny, nx).astype("f4")
        a = np.where(a == undef, np.nan, a)
        if yrev:
            a = a[::-1, :]
        return a

    data_vars = {}
    for (name, nlev, units, desc) in sel:
        k = nfields_var[name]
        eh_3d = k > 1
        cubo = np.full((len(times), k, ny, nx), np.nan, dtype="f4")
        for ti, vt in enumerate(times):
            if info["template"]:
                fn = _tmpl_name(info["dset"], vt)
                base_field = offsets[name]       # offset dentro do arquivo do tempo
            else:
                fn = info["dset"]
                base_field = ti * fields_per_time + offsets[name]
            if not os.path.exists(fn):
                continue
            with open(fn, "rb") as f:
                for z in range(k):
                    cubo[ti, z] = read_field(f, base_field + z)
        if zrev and eh_3d:
            cubo = cubo[:, ::-1, :, :]
        if eh_3d:
            da = xr.DataArray(
                cubo, dims=("time", "lev", "lat", "lon"),
                coords={"time": times.values, "lev": levels[:k], "lat": lat, "lon": lon})
        else:
            da = xr.DataArray(
                cubo[:, 0], dims=("time", "lat", "lon"),
                coords={"time": times.values, "lat": lat, "lon": lon})
        if units:
            da.attrs["units"] = units
        if desc:
            da.attrs["long_name"] = desc
        data_vars[name] = da

    ds = xr.Dataset(data_vars)
    ds["lat"].attrs.update(units="degrees_north", long_name="latitude")
    ds["lon"].attrs.update(units="degrees_east", long_name="longitude")
    if "lev" in ds.coords:
        ds["lev"].attrs.update(long_name="level")
    if info.get("title"):
        ds.attrs["title"] = info["title"]
    return ds


# =========================================================================
# Leitor GRIB2
# =========================================================================
def read_grib_cfgrib(caminho):
    """Lê um .grib2 direto (todas as variáveis/níveis/tempos) via cfgrib."""
    try:
        import cfgrib  # noqa: F401
    except ImportError:
        sys.exit("Para ler GRIB2 direto instale cfgrib:  pip install cfgrib")
    print(f"[GRIB2/cfgrib] Lendo: {caminho}")
    try:
        ds = xr.open_dataset(caminho, engine="cfgrib",
                             backend_kwargs={"indexpath": ""})
    except Exception as e:
        print(f"  Leitura simples falhou ({e}); usando cfgrib.open_datasets + merge...")
        import cfgrib
        dss = cfgrib.open_datasets(caminho, backend_kwargs={"indexpath": ""})
        ds = xr.merge(dss, compat="override", combine_attrs="drop_conflicts")
    return ds


def read_grib_via_ctl_wgrib2(info, wgrib2="wgrib2"):
    """Lê GRIB2 descrito por um .ctl (template por tempo) usando wgrib2 -netcdf.

    Converte cada arquivo GRIB2 (um por tempo válido) em NetCDF temporário e
    concatena no tempo. Mantém TODOS os níveis/variáveis. Espelha o uso de
    wgrib2 do script de verificação, sem exigir os 'match' por variável.
    """
    import tempfile
    times = info["times"]
    parts = []
    tmpdir = tempfile.mkdtemp(prefix="conv_grib_")
    for vt in times:
        grb = _tmpl_name(info["dset"], vt) if info["template"] else info["dset"]
        if not os.path.exists(grb):
            continue
        out = os.path.join(tmpdir, f"_{pd.Timestamp(vt).strftime('%Y%m%d%H')}.nc")
        res = subprocess.run([wgrib2, grb, "-netcdf", out],
                             capture_output=True, text=True)
        if res.returncode != 0 or not os.path.exists(out):
            print(f"  Aviso: wgrib2 falhou para {os.path.basename(grb)}: "
                  f"{res.stderr[-300:]}")
            continue
        d = xr.open_dataset(out)
        if "time" not in d.dims and "time" not in d.coords:
            d = d.expand_dims(time=[np.datetime64(pd.Timestamp(vt))])
        parts.append(d)
    if not parts:
        raise RuntimeError("wgrib2 não produziu nenhum NetCDF. Verifique o "
                           "caminho do wgrib2 e o DSET do .ctl.")
    ds = xr.concat(parts, dim="time") if len(parts) > 1 else parts[0]
    return ds


# =========================================================================
# Nomenclatura e escrita
# =========================================================================
DIM_NIVEL = ("lev", "level", "levels", "z", "plev", "isobaricinhpa",
             "height", "depth", "hybrid", "sigma")


def tem_nivel(da):
    return any(str(d).lower() in DIM_NIVEL for d in da.dims)


def data_saida(ds, forcado=None):
    if forcado:
        return forcado
    for nome in ("time", "valid_time", "t"):
        if nome in ds.coords or nome in ds.dims:
            try:
                t0 = np.asarray(ds[nome].values).ravel()[0]
                return pd.Timestamp(t0).strftime("%Y%m%d")
            except Exception:
                pass
    return datetime.now().strftime("%Y%m%d")


def salvar_por_variavel(ds, outdir, data_str, apenas=None):
    """Salva CADA variável em um NetCDF separado: <var>_<data>.nc."""
    os.makedirs(outdir, exist_ok=True)
    variaveis = list(ds.data_vars)
    if apenas:
        pedidas = [v.strip() for v in apenas]
        low = {v.lower(): v for v in variaveis}
        variaveis = [low[p.lower()] for p in pedidas if p.lower() in low]
        faltando = [p for p in pedidas if p.lower() not in low]
        if faltando:
            print(f"  Aviso: variáveis ausentes e ignoradas: {faltando}")
    if not variaveis:
        sys.exit("Nenhuma variável para salvar. Verifique a entrada / --vars.")

    gerados = []
    for var in variaveis:
        da = ds[var]
        eh_3d = tem_nivel(da)
        ds_var = da.to_dataset(name=var)
        enc = {var: {"zlib": True, "complevel": 4}}
        if np.issubdtype(da.dtype, np.floating):
            enc[var]["_FillValue"] = np.float32(9.969209968386869e36)
        caminho = os.path.join(outdir, f"{var}_{data_str}.nc")
        ds_var.to_netcdf(caminho, format="NETCDF4", encoding=enc)
        dims = " x ".join(f"{d}={ds_var.sizes[d]}" for d in da.dims)
        tipo = "3D todos níveis/tempos" if eh_3d else "2D todos tempos"
        print(f"  [OK] {var:<12} {tipo:<24} ({dims}) -> {os.path.basename(caminho)}")
        gerados.append(caminho)
    return gerados


# =========================================================================
# Carregamento de acordo com o tipo de entrada
# =========================================================================
def carregar(entrada, forcar_grib=False, wgrib2="wgrib2"):
    ext = os.path.splitext(entrada)[1].lower()
    if ext in (".grb2", ".grib2", ".grb", ".grib"):
        return read_grib_cfgrib(entrada)
    if ext == ".ctl":
        info = parse_ctl(entrada)
        if info["is_grib"] or forcar_grib:
            print(f"[GRIB2/.ctl] {os.path.basename(entrada)} — usando wgrib2")
            return read_grib_via_ctl_wgrib2(info, wgrib2=wgrib2)
        print(f"[GrADS/binário] {os.path.basename(entrada)} — leitor nativo numpy")
        return read_grads_binary(info)
    # binário passado direto: tenta achar o .ctl irmão
    cand = os.path.splitext(entrada)[0] + ".ctl"
    if os.path.exists(cand):
        info = parse_ctl(cand)
        return read_grads_binary(info)
    raise ValueError(f"Entrada não reconhecida: {entrada}. Passe um .ctl ou .grib2.")


# =========================================================================
# CLI
# =========================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Converte saídas do Eta (binário GrADS .ctl+.bin ou GRIB2) "
                    "para NetCDF, UM arquivo por variável. Variáveis 3D com "
                    "todos os níveis e todos os tempos.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", help="Arquivo .ctl (GrADS/GRIB2) ou .grib2 direto.")
    ap.add_argument("-o", "--outdir", default="netcdf_out",
                    help="Diretório de saída (padrão: netcdf_out).")
    ap.add_argument("--vars", default=None,
                    help="Variáveis a converter, separadas por vírgula (padrão: todas).")
    ap.add_argument("--date", default=None,
                    help="Força a <data> do nome (AAAAMMDD). Padrão: 1º tempo do dado.")
    ap.add_argument("--grib", action="store_true",
                    help="Trata o .ctl como GRIB2 (usa wgrib2), mesmo sem 'dtype grib2'.")
    ap.add_argument("--wgrib2", default="wgrib2",
                    help="Caminho do executável wgrib2 (para --grib).")
    args = ap.parse_args()

    if not os.path.exists(args.entrada):
        sys.exit(f"Arquivo de entrada não encontrado: {args.entrada}")

    ds = carregar(args.entrada, forcar_grib=args.grib, wgrib2=args.wgrib2)
    data_str = data_saida(ds, args.date)
    apenas = args.vars.split(",") if args.vars else None

    print(f"\nData na nomenclatura: {data_str}")
    print(f"Saída: {os.path.abspath(args.outdir)}\n")
    print("Convertendo variáveis (1 arquivo por variável):")
    gerados = salvar_por_variavel(ds, args.outdir, data_str, apenas)
    print(f"\nConcluído. {len(gerados)} arquivo(s) NetCDF gerado(s).")


if __name__ == "__main__":
    main()
