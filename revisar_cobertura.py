# -*- coding: utf-8 -*-
"""
revisar_cobertura.py - avisa quando ha nota no vault que nenhuma decisao cita.

Por que existe: as decisoes de cobertura ficam gravadas em
editais/<id>/decisoes.json. O atualizar.py REAPLICA essas decisoes a cada
rodada - ele nao reavalia. Entao, quando voce escreve uma nota nova, ela
entra no acervo mas a cobertura dos editais continua igual, em silencio.

Este script fecha esse buraco: compara as notas do inventario com as notas
citadas nas decisoes e lista as que ninguem citou, junto com os subtopicos
ainda descobertos que casam com elas. Ele NAO decide nada - so aponta o que
precisa de revisao.

Uso:  python revisar_cobertura.py [--silencioso]
Saida: codigo 0 se nao ha nada pendente, 1 se ha (para o atualizar.py avisar)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# o console do Windows e cp1252 e quebra em acento e emoji das notas
for fluxo in (sys.stdout, sys.stderr):
    try:
        fluxo.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

PALAVRAS_VAZIAS = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "em", "no", "na",
    "nos", "nas", "para", "por", "com", "sem", "sob", "ao", "aos", "um", "uma",
    "que", "se", "sua", "seu", "suas", "seus", "nota", "aula", "parte", "geral",
    "noticias", "conceito", "conceitos", "introducao", "basicos", "basico",
}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def tokens(texto: str) -> set[str]:
    brutos = re.split(r"[^a-z0-9]+", normalizar(texto))
    return {t for t in brutos if len(t) > 3 and t not in PALAVRAS_VAZIAS}


def carregar_inventario() -> list[dict]:
    arq = RAIZ / "data/inventario.json"
    if not arq.exists():
        return []
    dados = json.loads(arq.read_text(encoding="utf-8"))
    return dados["notas"] if isinstance(dados, dict) else dados


def carregar_revisadas() -> set[str]:
    """Nomes (normalizados) das notas ja revisadas que nao tem subtopico correspondente."""
    arq = RAIZ / "data/notas-revisadas.json"
    if not arq.exists():
        return set()
    dados = json.loads(arq.read_text(encoding="utf-8"))
    return {normalizar(n) for n in dados.get("revisadas", {})}


def gravar_pendencias(orfas: dict[str, str]) -> None:
    """Grava a lista de pendencias para o painel mostrar o aviso no proprio HTML."""
    destino = RAIZ / "data/pendencias.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    nomes = sorted(c.rsplit("/", 1)[-1][:-3] for c in orfas.values())
    destino.write_text(json.dumps({"quantidade": len(nomes), "notas": nomes},
                                  ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Aponta notas novas sem decisao de cobertura")
    p.add_argument("--silencioso", action="store_true",
                   help="so devolve o codigo de saida, sem listar detalhes")
    args = p.parse_args(argv)

    notas = carregar_inventario()
    if not notas:
        print("inventario.json ausente - rode o obsidian_scraper.py antes")
        return 0

    # nome da nota -> caminho
    por_nome: dict[str, str] = {}
    for n in notas:
        caminho = n.get("caminho") or n.get("path")
        base = caminho.rsplit("/", 1)[-1][:-3]
        por_nome[normalizar(base)] = caminho

    # notas citadas em alguma decisao
    citadas: set[str] = set()
    editais = []
    for pasta in sorted((RAIZ / "editais").iterdir()):
        arq_dec, arq_ed = pasta / "decisoes.json", pasta / "edital.json"
        if not arq_dec.exists() or not arq_ed.exists():
            continue
        dec = json.loads(arq_dec.read_text(encoding="utf-8"))
        for chave, valor in dec.items():
            if chave.startswith("_") or not isinstance(valor, dict):
                continue
            for nome in valor.get("notas", []):
                citadas.add(normalizar(nome))
        editais.append((pasta.name, json.loads(arq_ed.read_text(encoding="utf-8"))))

    # Notas que alguem JA revisou e concluiu que nenhum subtopico dos editais
    # cadastrados depende delas (ex.: CPC 31 sem edital que o peca). Sem isto o
    # aviso nunca zeraria e acabaria ignorado.
    revisadas = carregar_revisadas()

    orfas = {nome: cam for nome, cam in por_nome.items()
             if nome not in citadas and nome not in revisadas}
    # notas de apoio (SQL aula a aula, MOCs) nao precisam aparecer uma a uma
    orfas = {n: c for n, c in orfas.items() if not n.startswith("aula ")}

    gravar_pendencias(orfas)

    if not orfas:
        if not args.silencioso:
            print("tudo certo: toda nota do vault ja aparece em alguma decisao de cobertura")
        return 0

    # para cada nota orfa, procura subtopicos descobertos que casem com ela
    achados: list[tuple[str, str, str, str]] = []
    for nome, caminho in orfas.items():
        toks_nota = tokens(nome)
        if len(toks_nota) < 2:
            continue
        for eid, ed in editais:
            for disc in ed["disciplinas"]:
                for topico in disc["topicos"]:
                    for sub in topico["subtopicos"]:
                        if sub.get("concluido"):
                            continue
                        comuns = toks_nota & tokens(sub["texto"])
                        if len(comuns) >= 2:
                            achados.append((nome, eid, disc["nome"], sub["texto"]))

    print(f"\n  ATENCAO: {len(orfas)} nota(s) do vault nao aparecem em nenhuma decisao de cobertura.")
    print("  As decisoes sao reaplicadas, nao recalculadas - entao a cobertura NAO mudou sozinha.")
    if args.silencioso:
        return 1

    print("\n  Notas sem decisao:")
    for nome in sorted(orfas):
        print(f"    - {orfas[nome]}")

    if achados:
        print(f"\n  Subtopicos ainda descobertos que casam com essas notas ({len(achados)}):")
        atual = None
        for nome, eid, disc, texto in sorted(achados):
            if nome != atual:
                print(f"\n    [{nome}]")
                atual = nome
            print(f"      {eid:22s} {disc[:28]:30s} {texto[:70]}")
        print("\n  Peca ao agente para revisar a cobertura desses itens.")
    else:
        print("\n  Nenhum subtopico descoberto casou com elas - provavelmente sao")
        print("  notas de materia que nao cai nos editais cadastrados.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
