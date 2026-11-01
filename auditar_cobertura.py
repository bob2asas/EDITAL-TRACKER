# -*- coding: utf-8 -*-
"""
auditar_cobertura.py - ferramenta de conferencia das decisoes de cobertura.

Procura termos no CORPO COMPLETO das notas do vault (nao so no titulo e nos
cabecalhos), normalizando acentos. Serve para checar, com evidencia, se um
subtopico do edital esta mesmo coberto - ou se a decisao foi otimista demais.

Uso:
    python auditar_cobertura.py --vault "C:\\caminho\\do\\vault" --termos "restos a pagar" "suprimento de fundos"
    python auditar_cobertura.py --vault "..." --arquivo termos.txt --trecho

Somente leitura: nao altera nada no vault.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

IGNORAR = [".obsidian", ".trash", ".git", ".opencode", ".agents", ".claude",
           "_edital-tracker", "99 - Templates", "node_modules", ".smart-env"]


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def carregar(vault: Path, incluir_conflitos: bool) -> list[tuple[Path, str, str]]:
    notas = []
    for caminho in sorted(vault.rglob("*.md")):
        rel = caminho.relative_to(vault)
        if any(ig.lower() in {p.lower() for p in rel.parts} for ig in IGNORAR):
            continue
        if not incluir_conflitos and ".sync-conflict" in caminho.name:
            continue
        try:
            bruto = caminho.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        notas.append((rel, bruto, sem_acento(bruto)))
    return notas


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Audita termos do edital no vault")
    p.add_argument("--vault", required=True)
    p.add_argument("--termos", nargs="*", default=[])
    p.add_argument("--arquivo", default="",
                   help="arquivo .txt com um termo por linha")
    p.add_argument("--trecho", action="store_true",
                   help="mostra o trecho encontrado, para julgar o contexto")
    p.add_argument("--incluir-conflitos", action="store_true",
                   help="inclui arquivos .sync-conflict (ignorados por padrao)")
    p.add_argument("--largura", type=int, default=90)
    args = p.parse_args(argv)

    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    termos = list(args.termos)
    if args.arquivo:
        termos += [
            l.strip() for l in Path(args.arquivo).read_text(
                encoding="utf-8").splitlines() if l.strip()
        ]
    if not termos:
        print("informe --termos ou --arquivo", file=sys.stderr)
        return 1

    notas = carregar(Path(args.vault).expanduser().resolve(),
                     args.incluir_conflitos)
    print("notas lidas: {0}\n".format(len(notas)))

    for termo in termos:
        alvo = sem_acento(termo)
        achados = []
        for rel, bruto, normal in notas:
            if alvo in normal:
                pos = normal.find(alvo)
                trecho = re.sub(
                    r"\s+", " ",
                    bruto[max(0, pos - 45): pos + len(alvo) + 45]
                ).strip()
                achados.append((rel, trecho))

        print("{0:<42} {1:>3} notas".format(termo[:42], len(achados)))
        for rel, trecho in achados[:4]:
            print("    . " + rel.name.replace(".md", "")[:62])
            if args.trecho:
                print("      ..." + trecho[:args.largura] + "...")
        if len(achados) > 4:
            print("    ... e mais " + str(len(achados) - 4))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
