# Como subir o Convert2nc para o GitHub

> O ambiente do Cowork não consegue finalizar operações de `git` nesta pasta
> (o sistema de arquivos montado não suporta os locks/renames atômicos do git),
> então o commit precisa ser feito **na sua máquina Windows**, dentro de
> `...\Claude\Projects\Convert2nc`. Assim usa suas credenciais.

## Passo 1 — Inicializar o repositório e primeiro commit

Abra o **Git Bash** na pasta do projeto e rode:

```bash
git init
git config user.name  "Jorge Gomes"
git config user.email "jorgeluisgomes@gmail.com"
git add .
git commit -m "Conversor Eta -> NetCDF (1 variavel/arquivo; binario GrADS e GRIB2, 3D com todos niveis e tempos)"
git branch -M main
```

O `.gitignore` já ignora `netcdf_out/`, `tmp/`, dados brutos (`.bin`, `.grib2`…)
e `.nc`, então só o código sobe.

> Se aparecer erro de repositório corrompido, apague o `.git` antes:
> PowerShell `Remove-Item -Recurse -Force .git` | Git Bash `rm -rf .git`

## Passo 2 — Criar o repositório no GitHub e enviar

### Opção A — com GitHub CLI (`gh`) instalado (mais rápido)
```bash
gh repo create convert2nc --private --source=. --remote=origin --push
```
Troque `--private` por `--public` se quiser público.

### Opção B — manual (sem `gh`)
1. Em https://github.com/new crie um repositório **vazio** chamado
   `convert2nc` (sem README/.gitignore/licença).
2. Conecte e envie:
```bash
git remote add origin https://github.com/JorgeLGomes/convert2nc.git
git push -u origin main
```
Na primeira vez o Git pedirá autenticação. Use um **Personal Access Token**
(GitHub > Settings > Developer settings > Tokens) como senha, ou o
Git Credential Manager (já vem com o Git for Windows).

## Atualizações futuras

Depois do primeiro push, é só rodar o script incluído:

```bash
bash git_commit.sh "descrição da mudança"
```
Ele faz `add -A`, `commit` e `push` automaticamente.

## Dica — SSH (evita digitar token toda vez)
```bash
ssh-keygen -t ed25519 -C "jorgeluisgomes@gmail.com"
```
E use a URL SSH no remote: `git@github.com:JorgeLGomes/convert2nc.git`
(o mesmo padrão do seu repositório `verificacao-eta-era5`).
