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

import warnings

import numpy as np
import pandas as pd
import xarray as xr

# Silencia avisos ruidosos de bibliotecas (ex.: FutureWarning do cfgrib/xarray
# ao mesclar múltiplas mensagens GRIB2). Não afeta erros de verdade.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)


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

    ntime = len(times)
    sel_names = [v[0] for v in sel]
    # cubos de saída (preenchidos com NaN -> robusto a arquivo truncado)
    cubos = {name: np.full((ntime, nfields_var[name], ny, nx), np.nan, dtype="f4")
             for name in sel_names}

    def _preencher_bloco(block, ti):
        """block: (nblk, fields_por_arquivo, ny, nx) OU (fields, ny, nx) p/ 1 tempo.
        Copia, de forma VETORIZADA, o recorte de cada variável para seu cubo."""
        um_tempo = (block.ndim == 3)
        for name in sel_names:
            off, k = offsets[name], nfields_var[name]
            if um_tempo:
                disp = min(k, max(0, block.shape[0] - off))
                if disp > 0:
                    cubos[name][ti, :disp] = block[off:off + disp].astype("f4")
            else:
                disp = min(k, max(0, block.shape[1] - off))
                nb = block.shape[0]
                if disp > 0:
                    cubos[name][:nb, :disp] = block[:, off:off + disp].astype("f4")

    if seq:
        # Fortran 'sequential' (raro): marca(4)+dados+marca(4) por registro 2D.
        # Mantém leitura por campo (com bounds), mas 1 abertura por arquivo.
        rec = fld * 4 + 8
        for ti, vt in enumerate(times):
            fn = _tmpl_name(info["dset"], vt) if info["template"] else info["dset"]
            if not os.path.exists(fn):
                continue
            base = 0 if info["template"] else ti * fields_per_time
            with open(fn, "rb") as f:
                for name in sel_names:
                    off, k = offsets[name], nfields_var[name]
                    for z in range(k):
                        f.seek((base + off + z) * rec + 4)
                        a = np.fromfile(f, dtype=dtype, count=fld)
                        if a.size == fld:
                            cubos[name][ti, z] = a.reshape(ny, nx).astype("f4")
    elif info["template"]:
        # 1 arquivo por tempo: memmap + fatiamento vetorizado, 1 abertura/arquivo
        for ti, vt in enumerate(times):
            fn = _tmpl_name(info["dset"], vt)
            if not os.path.exists(fn):
                continue
            mm = np.memmap(fn, dtype=dtype, mode="r")
            navail = mm.size // fld
            nblk = min(navail, fields_per_time)
            if nblk > 0:
                _preencher_bloco(mm[:nblk * fld].reshape(nblk, ny, nx), ti)
            del mm
    else:
        # arquivo único com todos os tempos: 1 memmap + 1 reshape + fatias
        fn = info["dset"]
        if os.path.exists(fn):
            mm = np.memmap(fn, dtype=dtype, mode="r")
            per_time = fields_per_time * fld
            nt_full = min(ntime, mm.size // per_time)
            if nt_full > 0:
                block = mm[:nt_full * per_time].reshape(nt_full, fields_per_time, ny, nx)
                _preencher_bloco(block, 0)
            del mm

    # pós-processamento VETORIZADO por cubo (undef, yrev, zrev) + DataArray
    data_vars = {}
    for (name, nlev, units, desc) in sel:
        cubo = cubos[name]
        if np.isfinite(undef):
            cubo[cubo == np.float32(undef)] = np.nan
        if yrev:
            cubo = cubo[:, :, ::-1, :]
        k = nfields_var[name]
        eh_3d = k > 1
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
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True)  # compat. Python 3.6
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


def _escrever_uma(args):
    """Worker (picklável) que grava UMA variável em NetCDF. Retorna (var, path, dims, eh_3d)."""
    ds_var, var, caminho, complevel = args
    enc = {var: {}}
    if complevel and complevel > 0:
        enc[var].update(zlib=True, complevel=int(complevel))
    if np.issubdtype(ds_var[var].dtype, np.floating):
        enc[var]["_FillValue"] = np.float32(9.969209968386869e36)
    ds_var.to_netcdf(caminho, format="NETCDF4", encoding=enc)
    da = ds_var[var]
    dims = " x ".join(f"{d}={ds_var.sizes[d]}" for d in da.dims)
    return var, caminho, dims, tem_nivel(da)


def salvar_por_variavel(ds, outdir, data_str, apenas=None, complevel=1, jobs=1,
                        rename=None, prefix=""):
    """Salva CADA variável em um NetCDF separado: <prefix><var>_<data>.nc.

    complevel: nível zlib (0 = sem compressão, mais rápido; 1 = rápido/padrão).
    jobs:      nº de processos para gravar variáveis em paralelo (1 = sequencial).
    rename:    mapa {nome_entrada: nome_saída} para renomear variável e arquivo.
    prefix:    prefixo do nome do arquivo (ex.: 'Eta08_E01_').
    """
    rename = rename or {}
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

    tarefas = []
    for var in variaveis:
        outname = rename.get(var.lower(), var)
        caminho = os.path.join(outdir, f"{prefix}{outname}_{data_str}.nc")
        tarefas.append((ds[var].to_dataset(name=outname), outname, caminho, complevel))

    def _log(res):
        var, caminho, dims, eh_3d = res
        tipo = "3D todos níveis/tempos" if eh_3d else "2D todos tempos"
        print(f"  [OK] {var:<12} {tipo:<24} ({dims}) -> {os.path.basename(caminho)}")

    gerados = []
    if jobs and jobs > 1 and len(tarefas) > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for res in ex.map(_escrever_uma, tarefas):
                _log(res)
                gerados.append(res[1])
    else:
        for t in tarefas:
            res = _escrever_uma(t)
            _log(res)
            gerados.append(res[1])
    return gerados


# =========================================================================
# Conversão do BINÁRIO GrADS em modo STREAMING (tempo a tempo)
# --------------------------------------------------------------------------
# Grava cada variável direto em NetCDF, lendo um instante por vez. Memória fica
# em ~uma fatia (não segura o dataset inteiro) e cada byte do binário é lido só
# uma vez. Escala para domínios grandes (ex.: ams_08km 931x875 x 265 tempos) e
# NÃO usa multiprocessing, evitando o limite de pickle de 4 GiB por variável 3D.
# =========================================================================
def _ordena_campos(var_list):
    """offset (em campos 2D) e nº de campos de cada variável dentro de um tempo."""
    offsets, nfields_var, fpt = {}, {}, 0
    for (name, nlev, _u, _d) in var_list:
        k = max(1, nlev)
        offsets[name] = fpt
        nfields_var[name] = k
        fpt += k
    return offsets, nfields_var, fpt


def _ler_bloco_tempo(info, ti, mm, fpt):
    """Retorna os campos 2D do instante ti como (n_campos, ny, nx) — view do memmap.

    Abre 1 arquivo por tempo (template) ou fatia o memmap único (arquivo único).
    Retorna None se o arquivo do tempo não existir.
    """
    nx, ny = info["nx"], info["ny"]
    fld = nx * ny
    dtype = ">f4" if info["byteswap"] else "<f4"
    times = info["times"]
    if info["template"]:
        fn = _tmpl_name(info["dset"], times[ti])
        if not os.path.exists(fn):
            return None
        m = np.memmap(fn, dtype=dtype, mode="r")
        n = min(m.size // fld, fpt)
        return m[:n * fld].reshape(n, ny, nx)
    if info["sequential"]:
        # Fortran sequential (raro): marca(4)+dados+marca(4) por registro 2D
        rec = fld * 4 + 8
        base = ti * fpt
        out = np.full((fpt, ny, nx), np.nan, "f4")
        with open(info["dset"], "rb") as f:
            for j in range(fpt):
                f.seek((base + j) * rec + 4)
                a = np.fromfile(f, dtype=dtype, count=fld)
                if a.size == fld:
                    out[j] = a.reshape(ny, nx).astype("f4")
        return out
    # arquivo único com todos os tempos
    per_time = fpt * fld
    if mm is None or ti >= mm.size // per_time:
        return None
    return mm[ti * per_time:(ti + 1) * per_time].reshape(fpt, ny, nx)


def _worker_converter_chunk(args):
    """Worker picklável: converte um SUBCONJUNTO de variáveis (lê seu próprio dado).

    Só nomes/parâmetros são serializados (nada de arrays grandes) — cada processo
    faz o próprio I/O do binário. Evita o limite de pickle de 4 GiB.
    """
    info, chunk, outdir, data_str, complevel, rename, prefix, split = args
    return converter_binario(info, outdir, data_str, wanted=chunk,
                             complevel=complevel, jobs=1, progresso_cada=0,
                             rename=rename, prefix=prefix, split=split)


def converter_binario(info, outdir, data_str, wanted=None, complevel=1, jobs=1,
                      progresso_cada=20, rename=None, prefix="", split=None):
    """Converte o binário GrADS para NetCDF (1 arquivo por variável), tempo a tempo.

    jobs>1: divide as variáveis entre `jobs` processos; cada um lê só as suas do
    disco e grava seus NetCDF (sem serializar arrays). Escala numa mesma conversão.
    prefix: prefixo do nome do arquivo (ex.: 'Eta08_E01_' -> Eta08_E01_TP2M_<data>.nc).
    split:  None = um arquivo por variável com todos os tempos (padrão);
            'month' = um arquivo por variável POR MÊS-calendário
            (<prefix><var>_<data>_<AAAAMM>.nc) — para rodadas longas/clima.
    """
    import netCDF4
    rename = rename or {}
    os.makedirs(outdir, exist_ok=True)

    # resolve a seleção de variáveis já aqui (para poder dividir entre processos)
    sel0 = list(info["vars"])
    if wanted:
        wl0 = [w.strip().lower() for w in wanted]
        sel0 = [v for v in info["vars"] if v[0].lower() in wl0]
        faltando = [w for w in wanted
                    if w.strip().lower() not in [v[0].lower() for v in info["vars"]]]
        if faltando:
            print(f"  Aviso: variáveis ausentes e ignoradas: {faltando}", flush=True)
    if not sel0:
        sys.exit("Nenhuma variável para converter. Verifique --vars.")

    # ---- caminho PARALELO: divide variáveis em `jobs` grupos (round-robin) ----
    if jobs and jobs > 1 and len(sel0) > 1:
        from concurrent.futures import ProcessPoolExecutor
        nomes = [v[0] for v in sel0]
        n = min(jobs, len(nomes))
        chunks = [nomes[i::n] for i in range(n)]          # balanceia 3D entre grupos
        print(f"  {len(nomes)} variável(is) em {n} processo(s) paralelo(s)...",
              flush=True)
        tarefas = [(info, ch, outdir, data_str, complevel, rename, prefix, split)
                   for ch in chunks]
        gerados = []
        with ProcessPoolExecutor(max_workers=n) as ex:
            for res in ex.map(_worker_converter_chunk, tarefas):
                gerados.extend(res)
        return gerados
    nx, ny = info["nx"], info["ny"]
    undef = info["undef"]
    yrev, zrev = info["yrev"], info["zrev"]
    dtype = ">f4" if info["byteswap"] else "<f4"
    lat = info["y0"] + np.arange(ny) * info["dy"]
    lon = info["x0"] + np.arange(nx) * info["dx"]
    levels = info["levels"]
    times = info["times"]
    ntime = len(times)
    offsets, nfields_var, fpt = _ordena_campos(info["vars"])
    sel = sel0

    # memmap único (caso arquivo único)
    mm = None
    if not info["template"] and not info["sequential"] and os.path.exists(info["dset"]):
        mm = np.memmap(info["dset"], dtype=dtype, mode="r")

    # --split month: segmenta a série em meses-calendário contíguos; cada
    # segmento vira um conjunto de arquivos <prefix><var>_<data>_<AAAAMM>.nc,
    # processado e fechado antes do próximo (nunca mais que n_vars arquivos
    # abertos). Sem split: um único segmento com todos os tempos.
    if split == "month":
        segmentos = []
        for ti, t in enumerate(times):
            rotulo = pd.Timestamp(t).strftime("%Y%m")
            if not segmentos or segmentos[-1][0] != rotulo:
                segmentos.append((rotulo, []))
            segmentos[-1][1].append(ti)
    else:
        segmentos = [(None, list(range(ntime)))]

    fillv = np.float32(9.969209968386869e36)
    gerados = []
    for rotulo, tis in segmentos:
        nt_seg = len(tis)
        # cria um writer NetCDF por variável (todos abertos ao mesmo tempo)
        writers = {}
        for (name, nlev, units, desc) in sel:
            k = nfields_var[name]
            eh_3d = k > 1
            outname = rename.get(name.lower(), name)
            sufixo = f"_{rotulo}" if rotulo else ""
            path = os.path.join(outdir, f"{prefix}{outname}_{data_str}{sufixo}.nc")
            nc = netCDF4.Dataset(path, "w", format="NETCDF4")
            nc.createDimension("time", nt_seg)
            nc.createDimension("lat", ny)
            nc.createDimension("lon", nx)
            t0 = pd.Timestamp(times[tis[0]])
            tv = nc.createVariable("time", "f8", ("time",))
            tv.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
            tv.calendar = "standard"
            tv[:] = [(pd.Timestamp(times[ti]) - t0).total_seconds() / 3600.0
                     for ti in tis]
            latv = nc.createVariable("lat", "f4", ("lat",))
            latv[:] = lat
            latv.units = "degrees_north"
            latv.long_name = "latitude"
            lonv = nc.createVariable("lon", "f4", ("lon",))
            lonv[:] = lon
            lonv.units = "degrees_east"
            lonv.long_name = "longitude"
            if eh_3d:
                nc.createDimension("lev", k)
                levv = nc.createVariable("lev", "f4", ("lev",))
                levv[:] = levels[:k]
                levv.long_name = "level"
                dims = ("time", "lev", "lat", "lon")
            else:
                dims = ("time", "lat", "lon")
            kw = dict(fill_value=fillv)
            if complevel and complevel > 0:
                kw.update(zlib=True, complevel=int(complevel))
                # Chunk = 1 tempo (e 1 nível, no 3D). Alinha o chunk ao padrão de
                # escrita (um instante por vez): cada gravação preenche um chunk
                # inteiro, sem read-modify-write/recompressão. Sem isso, o chunk
                # padrão abrange vários tempos e é maior que o cache do HDF5, o que
                # torna a escrita tempo-a-tempo MUITO lenta (efeito super-linear).
                kw["chunksizes"] = (1, 1, ny, nx) if eh_3d else (1, ny, nx)
            dv = nc.createVariable(outname, "f4", dims, **kw)
            if units:
                dv.units = units
            if desc:
                dv.long_name = desc
            if info.get("title"):
                nc.title = info["title"]
            writers[name] = (nc, dv, offsets[name], k, eh_3d)

        etiqueta = f"[mês {rotulo}] " if rotulo else ""
        print(f"  {etiqueta}{len(writers)} variável(is) x {nt_seg} tempos — "
              f"gravando tempo a tempo...", flush=True)

        # varredura temporal única (cada byte lido 1x; memória ~ uma fatia)
        for li, ti in enumerate(tis):
            block = _ler_bloco_tempo(info, ti, mm, fpt)
            if block is not None:
                for name, (nc, dv, off, k, eh_3d) in writers.items():
                    nb = min(k, max(0, block.shape[0] - off))
                    if nb <= 0:
                        continue
                    sub = np.array(block[off:off + nb]).astype("f4")
                    if np.isfinite(undef):
                        sub[sub == np.float32(undef)] = np.nan
                    if yrev:
                        sub = sub[:, ::-1, :]
                    if zrev and eh_3d:
                        sub = sub[::-1]
                    if eh_3d:
                        dv[li, :nb, :, :] = sub
                    else:
                        dv[li, :, :] = sub[0]
            if progresso_cada and (li + 1) % progresso_cada == 0:
                print(f"    ... {li + 1}/{nt_seg} tempos", flush=True)

        for name, (nc, dv, off, k, eh_3d) in writers.items():
            path = nc.filepath()
            nc.close()
            tipo = "3D todos níveis/tempos" if eh_3d else "2D todos tempos"
            dimtxt = (f"time={nt_seg} x lev={k} x lat={ny} x lon={nx}" if eh_3d
                      else f"time={nt_seg} x lat={ny} x lon={nx}")
            print(f"  [OK] {etiqueta}{name:<12} {tipo:<24} ({dimtxt}) -> "
                  f"{os.path.basename(path)}", flush=True)
            gerados.append(path)
    if mm is not None:
        del mm
    return gerados


# =========================================================================
# GRIB2 via cfgrib (SEM wgrib2) — streaming tempo a tempo
# --------------------------------------------------------------------------
# Lê o .ctl (template + tempos) e abre cada GRIB2 por-tempo com cfgrib/eccodes,
# gravando cada variável direto em NetCDF (memória mínima). Não requer wgrib2.
# =========================================================================
def _grib2_inventario(fn):
    """Inventário do GRIB2 via eccodes (1 passada): {shortName: dict(typeOfLevel,
    levels, name)}. Rápido — lê só chaves de cabeçalho, não decodifica valores."""
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
                lev = codes_get(gid, "level")
                try:
                    nm = codes_get(gid, "name")
                except Exception:
                    nm = sn
                d = inv.setdefault(sn, dict(typeOfLevel=tol, levels=set(), name=nm))
                d["levels"].add(lev)
            finally:
                codes_release(gid)
    for d in inv.values():
        d["levels"] = sorted(d["levels"])
    return inv


def _grib2_grade(fn, shortname):
    """lat (norte->sul) e lon (oeste->leste) da 1ª mensagem de shortName (eccodes)."""
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


def _grib2_le_ecc(fn, shortname, typeoflevel, levels):
    """Lê UMA variável de um GRIB2 via eccodes (1 passada). 2D -> (nj,ni);
    3D -> (nlev,nj,ni) na ordem de `levels`. Normaliza norte->sul, oeste->leste."""
    from eccodes import (codes_grib_new_from_file, codes_get, codes_get_values,
                         codes_release)
    lev_idx = {lv: i for i, lv in enumerate(levels)} if levels else None
    out = None
    with open(fn, "rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                if codes_get(gid, "shortName") != shortname:
                    continue
                if typeoflevel and codes_get(gid, "typeOfLevel") != typeoflevel:
                    continue
                ni = codes_get(gid, "Ni")
                nj = codes_get(gid, "Nj")
                a = codes_get_values(gid).reshape(nj, ni).astype("f4")
                if codes_get(gid, "jScansPositively"):
                    a = a[::-1, :]
                if codes_get(gid, "iScansNegatively"):
                    a = a[:, ::-1]
                if levels is None:
                    return a
                if out is None:
                    out = np.full((len(levels), nj, ni), np.nan, "f4")
                lv = codes_get(gid, "level")
                if lv in lev_idx:
                    out[lev_idx[lv]] = a
            finally:
                codes_release(gid)
    return out


def _grib2_primeiro_arquivo(info):
    dset = info["dset"]
    for vt in info["times"]:
        fn = _tmpl_name(dset, vt) if info.get("template", True) else dset
        if os.path.exists(fn):
            return fn
    return None


def listar_vars_grib2(info):
    """Imprime as variáveis disponíveis no 1º GRIB2 (para escolher em --vars)."""
    f0 = _grib2_primeiro_arquivo(info)
    if not f0:
        sys.exit("Nenhum arquivo GRIB2 encontrado (confira DSET/template do .ctl).")
    inv = _grib2_inventario(f0)
    print(f"Variáveis no GRIB2 ({os.path.basename(f0)}):")
    for sn, d in inv.items():
        nlev = len(d["levels"])
        tipo = f"3D ({d['typeOfLevel']}={nlev} níveis)" if nlev > 1 else "2D"
        print(f"  {sn:<14} {tipo:<20} nível={d['typeOfLevel']:<16} {d.get('name', '')}")


def converter_grib2_cfgrib(info, outdir, data_str, wanted=None, complevel=1,
                           asname=None, progresso_cada=20, rename=None, prefix=""):
    """Converte GRIB2 (descrito por .ctl) para NetCDF via cfgrib, tempo a tempo."""
    import netCDF4
    rename = rename or {}
    os.makedirs(outdir, exist_ok=True)
    times = info["times"]
    ntime = len(times)
    dset = info["dset"]

    f0 = _grib2_primeiro_arquivo(info)
    if not f0:
        sys.exit("Nenhum arquivo GRIB2 encontrado (confira DSET/template do .ctl).")
    inv = _grib2_inventario(f0)          # inventário 1x (shortName, nível, tipo)
    todos = list(inv.keys())

    sel = todos
    if wanted:
        wl = [w.strip().lower() for w in wanted]
        sel = [sn for sn in todos if sn.lower() in wl]
        if not sel:
            sys.exit(f"Variáveis {wanted} não encontradas. Disponíveis: {todos}\n"
                     f"(use --list-vars para ver os nomes)")
    if asname and len(sel) != 1:
        print("  Aviso: --asname só se aplica ao selecionar 1 variável; ignorado.")
        asname = None

    fillv = np.float32(9.969209968386869e36)
    writers = {}
    for sn in sel:
        d = inv[sn]
        tol = d["typeOfLevel"]
        eh_3d = len(d["levels"]) > 1
        levels = d["levels"] if eh_3d else None
        g = _grib2_grade(f0, sn)
        if g is None:
            print(f"  Aviso: grade não obtida para {sn}; pulando.")
            continue
        lat, lon = g
        ny, nx = len(lat), len(lon)
        if sn.lower() in rename:
            outname = rename[sn.lower()]
        elif asname and len(sel) == 1:
            outname = asname
        else:
            outname = sn
        path = os.path.join(outdir, f"{prefix}{outname}_{data_str}.nc")
        nc = netCDF4.Dataset(path, "w", format="NETCDF4")
        nc.createDimension("time", ntime)
        nc.createDimension("lat", ny)
        nc.createDimension("lon", nx)
        t0 = pd.Timestamp(times[0])
        tv = nc.createVariable("time", "f8", ("time",))
        tv.units = f"hours since {t0.strftime('%Y-%m-%d %H:%M:%S')}"
        tv.calendar = "standard"
        tv[:] = [(pd.Timestamp(t) - t0).total_seconds() / 3600.0 for t in times]
        latv = nc.createVariable("lat", "f4", ("lat",))
        latv[:] = lat
        latv.units = "degrees_north"
        lonv = nc.createVariable("lon", "f4", ("lon",))
        lonv[:] = lon
        lonv.units = "degrees_east"
        if eh_3d:
            k = len(levels)
            nc.createDimension("lev", k)
            levv = nc.createVariable("lev", "f4", ("lev",))
            levv[:] = np.asarray(levels, "f4")
            levv.long_name = str(tol)
            dims = ("time", "lev", "lat", "lon")
            chunk = (1, 1, ny, nx)
        else:
            dims = ("time", "lat", "lon")
            chunk = (1, ny, nx)
        kw = dict(fill_value=fillv)
        if complevel and complevel > 0:
            kw.update(zlib=True, complevel=int(complevel), chunksizes=chunk)
        dv = nc.createVariable(outname, "f4", dims, **kw)
        writers[sn] = (nc, dv, outname, eh_3d, tol, levels)

    print(f"  {len(writers)} variável(is) x {ntime} tempos (GRIB2/eccodes)...",
          flush=True)
    for ti, vt in enumerate(times):
        fn = _tmpl_name(dset, vt) if info.get("template", True) else dset
        if not os.path.exists(fn):
            continue
        for sn, (nc, dv, outname, eh_3d, tol, levels) in writers.items():
            arr = _grib2_le_ecc(fn, sn, tol, levels)   # 1 passada, só esta variável
            if arr is None:
                continue
            if eh_3d:
                dv[ti, :, :, :] = arr
            else:
                dv[ti, :, :] = arr
        if progresso_cada and (ti + 1) % progresso_cada == 0:
            print(f"    ... {ti + 1}/{ntime} tempos", flush=True)

    gerados = []
    for sn, (nc, dv, outname, eh_3d, tol, levels) in writers.items():
        p = nc.filepath()
        nc.close()
        tipo = "3D todos níveis/tempos" if eh_3d else "2D todos tempos"
        print(f"  [OK] {outname:<14} {tipo} -> {os.path.basename(p)}", flush=True)
        gerados.append(p)
    return gerados


def _rename_map(rename_str, vars_list):
    """Mapa nome_de_entrada -> nome_de_saída.

    Aceita: pares 'grib:novo,grib2:novo2'  OU  uma lista simples do mesmo
    tamanho de --vars (correspondência posicional).
    Ex.: --vars 2t,10u,10v,prmsl  --rename tp2m,u10m,v10m,pslm
         --rename 2t:tp2m,10u:u10m,10v:v10m,prmsl:pslm
    """
    rmap = {}
    if not rename_str:
        return rmap
    parts = [p.strip() for p in rename_str.split(",") if p.strip()]
    if all(":" in p for p in parts):
        for p in parts:
            a, b = p.split(":", 1)
            rmap[a.strip().lower()] = b.strip()     # chave minúscula (case-insensitive)
    elif vars_list and len(parts) == len(vars_list):
        for a, b in zip([v.strip().lower() for v in vars_list], parts):
            rmap[a] = b
    else:
        sys.exit("--rename: use pares 'grib:novo' OU uma lista do mesmo "
                 "tamanho de --vars.")
    return rmap


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
                    help="Trata o .ctl como GRIB2, mesmo sem 'dtype grib2'.")
    ap.add_argument("--grib-engine", choices=["cfgrib", "wgrib2"], default="cfgrib",
                    help="Como ler GRIB2: 'cfgrib' (Python/eccodes, sem wgrib2, "
                         "padrão) ou 'wgrib2' (precisa de wgrib2 com suporte NetCDF).")
    ap.add_argument("--wgrib2", default="wgrib2",
                    help="Caminho do executável wgrib2 (só para --grib-engine wgrib2).")
    ap.add_argument("--list-vars", action="store_true",
                    help="Lista as variáveis do GRIB2 (nomes do cfgrib) e sai.")
    ap.add_argument("--asname", default=None,
                    help="Renomeia a variável de saída (só ao selecionar 1 var). "
                         "Ex.: --vars tp --asname PREC -> PREC_<data>.nc")
    ap.add_argument("--rename", default=None,
                    help="Renomeia VÁRIAS variáveis. Pares 'grib:novo' ou lista "
                         "na ordem de --vars. Ex.: --vars 2t,10u,10v,prmsl "
                         "--rename tp2m,u10m,v10m,pslm")
    ap.add_argument("--complevel", type=int, default=1,
                    help="Nível de compressão zlib 0-9 (0=sem compressão, mais "
                         "rápido; 1=padrão rápido). Padrão: 1.")
    ap.add_argument("--jobs", "-j", type=int, default=1,
                    help="Nº de processos paralelos (divide as variáveis entre "
                         "eles). Útil para aproveitar muitos núcleos numa conversão.")
    ap.add_argument("--prefix", default="",
                    help="Prefixo do nome dos arquivos de saída. Ex.: "
                         "--prefix Eta08_E01_ -> Eta08_E01_TP2M_<data>.nc")
    ap.add_argument("--split", choices=["none", "month"], default="none",
                    help="'month' = um NetCDF por variável POR MÊS-calendário "
                         "(<var>_<data>_<AAAAMM>.nc) — para rodadas longas/clima. "
                         "Só no caminho binário (.ctl+.bin). Padrão: none.")
    args = ap.parse_args()
    split = None if args.split == "none" else args.split

    if not os.path.exists(args.entrada):
        sys.exit(f"Arquivo de entrada não encontrado: {args.entrada}")

    if split and (args.grib or not args.entrada.lower().endswith(".ctl")):
        print("Aviso: --split só se aplica ao caminho binário (.ctl+.bin); ignorado.")
        split = None

    t0 = datetime.now()
    apenas = args.vars.split(",") if args.vars else None
    renomear = _rename_map(args.rename, apenas)
    ext = os.path.splitext(args.entrada)[1].lower()

    # ---- descobre o tipo de entrada ----
    info = None
    if ext in (".grb2", ".grib2", ".grb", ".grib"):
        modo = "grib"
    elif ext == ".ctl":
        info = parse_ctl(args.entrada)
        modo = "grib_ctl" if (info["is_grib"] or args.grib) else "bin"
    else:
        cand = os.path.splitext(args.entrada)[0] + ".ctl"
        if not os.path.exists(cand):
            sys.exit(f"Entrada não reconhecida: {args.entrada}. Passe .ctl ou .grib2.")
        info = parse_ctl(cand)
        modo = "bin"

    # ---- BINÁRIO: streaming tempo a tempo (memória mínima, sem pickle) ----
    if modo == "bin":
        data_str = args.date or pd.Timestamp(info["times"][0]).strftime("%Y%m%d")
        print(f"[GrADS/binário] {os.path.basename(args.entrada)} — streaming tempo a tempo")
        print(f"\nData na nomenclatura: {data_str}")
        print(f"Saída: {os.path.abspath(args.outdir)}")
        print(f"Compressão zlib nível {args.complevel}  |  jobs={args.jobs}\n")
        print("Convertendo variáveis (1 arquivo por variável):")
        gerados = converter_binario(info, args.outdir, data_str, apenas,
                                    complevel=args.complevel, jobs=args.jobs,
                                    rename=renomear, prefix=args.prefix,
                                    split=split)
    # ---- GRIB2 descrito por .ctl ----
    elif modo == "grib_ctl":
        if args.list_vars:
            listar_vars_grib2(info)
            return
        if split:
            print("Aviso: --split só se aplica ao caminho binário; ignorado.")
        data_str = args.date or pd.Timestamp(info["times"][0]).strftime("%Y%m%d")
        if args.grib_engine == "wgrib2":
            print(f"[GRIB2/.ctl] {os.path.basename(args.entrada)} — wgrib2")
            ds = read_grib_via_ctl_wgrib2(info, wgrib2=args.wgrib2)
            data_str = data_saida(ds, args.date)
            print(f"\nData na nomenclatura: {data_str}")
            print(f"Saída: {os.path.abspath(args.outdir)}\n")
            gerados = salvar_por_variavel(ds, args.outdir, data_str, apenas,
                                          complevel=args.complevel, jobs=args.jobs,
                                          rename=renomear, prefix=args.prefix)
        else:  # cfgrib (padrão, SEM wgrib2)
            print(f"[GRIB2/.ctl] {os.path.basename(args.entrada)} — eccodes streaming")
            print(f"\nData na nomenclatura: {data_str}")
            print(f"Saída: {os.path.abspath(args.outdir)}")
            print(f"Compressão zlib nível {args.complevel}\n")
            print("Convertendo variáveis (1 arquivo por variável):")
            gerados = converter_grib2_cfgrib(info, args.outdir, data_str, apenas,
                                             complevel=args.complevel,
                                             asname=args.asname, rename=renomear,
                                             prefix=args.prefix)
    # ---- arquivo .grib2 direto ----
    else:
        ds = read_grib_cfgrib(args.entrada)
        data_str = data_saida(ds, args.date)
        print(f"\nData na nomenclatura: {data_str}")
        print(f"Saída: {os.path.abspath(args.outdir)}\n")
        gerados = salvar_por_variavel(ds, args.outdir, data_str, apenas,
                                      complevel=args.complevel, jobs=args.jobs,
                                      rename=renomear, prefix=args.prefix)

    dt = (datetime.now() - t0).total_seconds()
    print(f"\nConcluído em {dt:.1f}s. {len(gerados)} arquivo(s) NetCDF gerado(s).")


if __name__ == "__main__":
    main()
