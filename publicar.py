# -*- coding: utf-8 -*-
"""
publicar.py - envia o painel atualizado para o GitHub Pages.

Copia o index.html recem gerado para o repositorio local de publicacao e faz
commit + push. O GitHub Pages republica sozinho em menos de um minuto.

E chamado pelo ATUALIZAR PAINEL.bat depois que o painel e regerado; tambem
pode ser rodado sozinho:

    python publicar.py

Se o repositorio ainda nao estiver configurado, o script explica o que falta
em vez de falhar com erro de git.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CONFIG = RAIZ / "publicar.json"


def git(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Publica o painel no GitHub Pages")
    p.add_argument("--repo", default="", help="pasta do repositorio local")
    p.add_argument("--silencioso", action="store_true",
                   help="nao reclama se ainda nao estiver configurado")
    args = p.parse_args(argv)

    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    destino = args.repo
    if not destino and CONFIG.is_file():
        try:
            destino = json.loads(CONFIG.read_text(encoding="utf-8")).get("repo", "")
        except json.JSONDecodeError as erro:
            # erro de digitacao no caminho e o motivo mais comum: no Windows,
            # a barra invertida precisa ser dobrada em JSON - ou use "/"
            print("ERRO: " + CONFIG.name + " esta com JSON invalido (" + str(erro)
                  + ").\n      No caminho use barras normais: C:/Users/...",
                  file=sys.stderr)
            return 1
        except OSError as erro:
            print("ERRO ao ler " + CONFIG.name + ": " + str(erro), file=sys.stderr)
            return 1

    if not destino:
        if not args.silencioso:
            print("Publicacao nao configurada: crie " + CONFIG.name
                  + ' com {"repo": "C:/caminho/do/repositorio"}')
        return 0

    repo = Path(destino)
    if not (repo / ".git").is_dir():
        print("ERRO: " + str(repo) + " nao e um repositorio git.", file=sys.stderr)
        return 1

    painel = RAIZ / "index.html"
    if not painel.is_file():
        print("ERRO: index.html nao existe. Gere o painel primeiro.",
              file=sys.stderr)
        return 1

    shutil.copy2(painel, repo / "index.html")

    codigo, saida = git(["remote", "get-url", "origin"], repo)
    if codigo != 0:
        print("Falta ligar o repositorio ao GitHub. No GitHub, crie um "
              "repositorio vazio e rode:\n"
              "    git -C \"" + str(repo) + "\" remote add origin <url>",
              file=sys.stderr)
        return 1

    git(["add", "-A"], repo)
    codigo, saida = git(
        ["commit", "-m", "painel " + datetime.now().strftime("%d/%m %H:%M")],
        repo)
    if codigo != 0 and "nothing to commit" in saida.lower():
        print("nada mudou desde a ultima publicacao")
        return 0

    codigo, saida = git(["push", "origin", "main"], repo)
    if codigo != 0:
        print("ERRO ao enviar para o GitHub:\n" + saida, file=sys.stderr)
        print("\nSe for a primeira vez, o git vai pedir seu login do GitHub "
              "numa janela.", file=sys.stderr)
        return 1

    print("publicado - o link atualiza em menos de um minuto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
