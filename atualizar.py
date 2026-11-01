# -*- coding: utf-8 -*-
"""
atualizar.py - roda o pipeline inteiro e abre o painel.

E o unico comando do dia a dia:

    python atualizar.py

Ele faz, nesta ordem:
  1. revarre o vault do Obsidian (le as notas novas que voce escreveu)
  2. reaplica as decisoes de cobertura de cada edital que tiver decisoes.json
  3. regera o index.html e abre no navegador

Nao precisa informar o caminho do vault: o script se localiza sozinho a partir
da propria pasta (<vault>/<pasta de concursos>/_edital-tracker).

Opcoes:
    --sem-abrir      nao abre o navegador no fim
    --vault CAMINHO  usa outro vault em vez do detectado
    --api            usa a API da Anthropic nos editais SEM decisoes.json
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def passo(titulo: str) -> None:
    print("\n" + "=" * 66)
    print("  " + titulo)
    print("=" * 66)


def rodar(args: list[str]) -> bool:
    """Executa um script do projeto e devolve se deu certo."""
    print("> python " + " ".join(args))
    processo = subprocess.run(
        [sys.executable, "-I"] + args, cwd=str(RAIZ),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    saida = (processo.stdout or "") + (processo.stderr or "")
    for linha in saida.strip().split("\n"):
        if linha.strip():
            print("  " + linha)
    return processo.returncode == 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atualiza o painel de editais")
    p.add_argument("--vault", default="", help="raiz do vault (detectada por padrao)")
    p.add_argument("--sem-abrir", action="store_true")
    p.add_argument("--api", action="store_true",
                   help="usa a API nos editais que nao tem decisoes.json")
    args = p.parse_args(argv)

    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # <vault>/<pasta de concursos>/_edital-tracker/atualizar.py
    pasta_concursos = RAIZ.parent
    vault = Path(args.vault).expanduser().resolve() if args.vault else RAIZ.parents[1]

    if not (vault / pasta_concursos.name).is_dir():
        print("ERRO: nao consegui localizar o vault a partir de " + str(RAIZ)
              + "\n      rode com --vault \"caminho\\do\\vault\"", file=sys.stderr)
        return 1

    passo("1/3  Lendo as notas do Obsidian")
    ok = rodar([
        "obsidian_scraper.py", "--vault", str(vault),
        "--somente", pasta_concursos.name, "-o", "data/inventario.json",
    ])
    if not ok:
        print("\nERRO ao ler o vault. Nada foi alterado.", file=sys.stderr)
        return 1

    passo("2/3  Atualizando a cobertura de cada edital")
    editais = sorted((RAIZ / "editais").glob("*/edital.json"))
    if not editais:
        print("  nenhum edital cadastrado ainda - veja o README para criar o primeiro")
    for caminho in editais:
        identificador = caminho.parent.name
        decisoes = caminho.parent / "decisoes.json"
        if decisoes.is_file():
            rodar(["semantic_matcher.py", "--id", identificador,
                   "--importar", str(decisoes)])
        elif args.api:
            rodar(["semantic_matcher.py", "--id", identificador])
        else:
            print("  [" + identificador + "] sem decisoes.json - mantido como esta"
                  "\n     (use --api para avaliar pela API, ou peca uma revisao)")

    passo("3/4  Gerando o painel")
    comando = ["dashboard_generator.py", "--vault", vault.name]
    if not args.sem_abrir:
        comando.append("--abrir")
    if not rodar(comando):
        return 1

    # O passo 2 REAPLICA as decisoes gravadas, nao reavalia. Entao uma nota nova
    # entra no acervo sem mexer na cobertura. Aqui avisamos quando isso acontece.
    passo("4/4  Conferindo se alguma nota nova ficou de fora da cobertura")
    pendente = not rodar(["revisar_cobertura.py"])

    print("\nPronto. O painel esta em: " + str(RAIZ / "index.html"))
    if pendente:
        print("\n  >> Ha nota nova sem decisao de cobertura (veja a lista acima).")
        print("  >> A cobertura dos editais NAO muda sozinha: peca ao agente")
        print("     \"revise a cobertura com as notas novas\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
