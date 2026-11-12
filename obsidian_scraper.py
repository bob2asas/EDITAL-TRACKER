# -*- coding: utf-8 -*-
"""
obsidian_scraper.py - Passo 2 do pipeline Edital Tracker.

Varre o vault do Obsidian (SOMENTE LEITURA) e monta um inventario JSON das
notas: titulo, tags, frontmatter, headings, TL;DR e um "resumo semantico"
compacto que sera enviado ao LLM no Passo 3.

Uso:
    python obsidian_scraper.py --vault "C:/caminho/do/vault" -o data/inventario.json
    python obsidian_scraper.py --vault "..." --somente "02 - Concursos" --preview

GARANTIA: este script NUNCA escreve, move ou apaga nada dentro do vault.
Os arquivos .md sao abertos em modo leitura ('r'); a unica escrita acontece
no arquivo --output, que e bloqueado se estiver dentro do vault sem --permitir-saida-no-vault.

Sem dependencias externas (stdlib apenas).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

# Pastas ignoradas por padrao (config do Obsidian, plugins, templates, este projeto)
IGNORAR_PADRAO = [
    ".obsidian", ".trash", ".git", ".opencode", ".agents", ".claude",
    "_edital-tracker", "99 - Templates", "node_modules", ".smart-env",
    # nao sao material de estudo: caixa de entrada, projetos e automacao
    "00 - Inbox", "01 - Projetos", "Automacao", "imagens",
    # Biblioteca Legal: texto oficial das leis, usado como alvo de links.
    # E material de consulta, nao nota de estudo - entrar no acervo inflaria
    # a contagem e o matcher passaria a "cobrir" subtopicos com o texto da lei.
    "Legislacao",
]

# Arquivos soltos na raiz do vault que nao sao nota de estudo
ARQUIVOS_RAIZ_IGNORADOS = {"claude.md", "readme.md", "agents.md", "gemini.md"}

# Arquivos que sao indices, nao conteudo de estudo
RE_ARQUIVO_MOC = re.compile(r"^(moc|mapa|indice|\u00edndice)\b[\s\-]", re.IGNORECASE)

RE_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
RE_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
RE_CALLOUT_RESUMO = re.compile(
    r"^>\s*\[!(?:abstract|summary|tldr|info|note)\][^\n]*\n((?:>[^\n]*\n?)+)",
    re.MULTILINE | re.IGNORECASE,
)
RE_TAG_INLINE = re.compile(r"(?:(?<=\s)|^)#([A-Za-z\u00c0-\u00ff][\w\u00c0-\u00ff/\-]{1,48})")
RE_NEGRITO = re.compile(r"\*\*(.+?)\*\*")
RE_WIKILINK = re.compile(r"\[\[([^\]\|#]+)(?:[#\|][^\]]*)?\]\]")
RE_BLOCO_CODIGO = re.compile(r"```.*?```", re.DOTALL)
RE_BLOCO_MERMAID = re.compile(r"```mermaid.*?```", re.DOTALL)
RE_MD_RUIDO = re.compile(r"[*_`>\[\]]+")

PREFIXO_DATA_PASTA = re.compile(r"^\(Aula[^)]*\)\s*[-\u2013\u2014]?\s*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------


@dataclass
class Nota:
    id: str                      # caminho relativo (chave estavel)
    titulo: str                  # H1 da nota, ou nome do arquivo
    arquivo: str                 # nome do arquivo
    caminho: str                 # caminho relativo ao vault
    pasta: str                   # pasta-pai relativa
    area: str                    # pasta de 1o nivel (ex.: "02 - Concursos")
    materia: str                 # pasta de 2o nivel (ex.: "Direito Tributario")
    aula: str                    # "(Aula 05)" extraido da pasta, se houver
    tags: list = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)
    headings: list = field(default_factory=list)
    conceitos: list = field(default_factory=list)   # termos em **negrito**
    links: list = field(default_factory=list)       # wikilinks de saida
    resumo: str = ""                                # TL;DR do callout
    resumo_semantico: str = ""                      # texto enviado ao LLM
    palavras: int = 0
    modificado_em: str = ""
    is_moc: bool = False


# ---------------------------------------------------------------------------
# Parser de frontmatter (subset de YAML suficiente para notas Obsidian)
# ---------------------------------------------------------------------------


def _coagir(valor: str):
    bruto = valor.strip().strip('"').strip("'").strip()
    if bruto in ("", "~", "null"):
        return ""
    if bruto.lower() in ("true", "yes"):
        return True
    if bruto.lower() in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", bruto):
        return int(bruto)
    if re.fullmatch(r"-?\d+\.\d+", bruto):
        return float(bruto)
    return bruto


def parse_frontmatter(texto: str) -> tuple[dict, str]:
    """Devolve (frontmatter, corpo). Suporta escalares, listas '-' e [a, b]."""
    m = RE_FRONTMATTER.match(texto)
    if not m:
        return {}, texto

    dados: dict = {}
    chave_atual: str | None = None
    for linha in m.group(1).split("\n"):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        # item de lista
        item = re.match(r"^\s+-\s*(.*)$", linha)
        if item and chave_atual:
            valor = _coagir(item.group(1))
            if valor != "":
                dados.setdefault(chave_atual, [])
                if isinstance(dados[chave_atual], list):
                    dados[chave_atual].append(valor)
            continue
        # chave: valor
        par = re.match(r"^([A-Za-z0-9_\u00c0-\u00ff][\w\s\-\u00c0-\u00ff]*?)\s*:\s*(.*)$", linha)
        if par:
            chave = par.group(1).strip()
            resto = par.group(2).strip()
            chave_atual = chave
            if resto == "":
                dados[chave] = []            # lista vem nas proximas linhas
            elif resto.startswith("[") and resto.endswith("]"):
                dados[chave] = [
                    _coagir(x) for x in resto[1:-1].split(",") if x.strip()
                ]
            else:
                dados[chave] = _coagir(resto)
    return dados, texto[m.end():]


# ---------------------------------------------------------------------------
# Extracao
# ---------------------------------------------------------------------------


# Tag valida no Obsidian: sem espacos, sem colchetes, pelo menos uma letra
RE_TAG_VALIDA = re.compile(r"^[\wÀ-ÿ][\wÀ-ÿ/\-]{0,48}$")


def normalizar_tag(tag: str) -> str:
    """Normaliza e descarta lixo (placeholders de template, comentarios YAML)."""
    limpa = str(tag).strip().lstrip("#").strip().strip('"').strip("'")
    limpa = limpa.split("#")[0].strip()          # remove comentario inline do YAML
    if not RE_TAG_VALIDA.match(limpa):
        return ""
    if not any(c.isalpha() for c in limpa):
        return ""
    return limpa


def extrair_tags(frontmatter: dict, corpo_sem_codigo: str) -> list[str]:
    tags: list[str] = []
    bruto = frontmatter.get("tags") or frontmatter.get("tag") or []
    if isinstance(bruto, str):
        bruto = re.split(r"[,\s]+", bruto)
    if isinstance(bruto, list):
        tags.extend(
            limpa for limpa in (normalizar_tag(t) for t in bruto if str(t).strip())
            if limpa
        )

    for m in RE_TAG_INLINE.finditer(corpo_sem_codigo):
        candidata = normalizar_tag(m.group(1))
        # Evita capturar '#' de headings ou cores hex
        if candidata and not re.fullmatch(r"[0-9a-fA-F]{3,8}", candidata):
            tags.append(candidata)

    vistas, saida = set(), []
    for t in tags:
        chave = t.lower()
        if t and chave not in vistas:
            vistas.add(chave)
            saida.append(t)
    return saida


def limpar_inline(texto: str) -> str:
    texto = RE_WIKILINK.sub(r"\1", texto)
    texto = RE_MD_RUIDO.sub("", texto)
    return re.sub(r"\s+", " ", texto).strip()


def extrair_resumo(corpo: str) -> str:
    m = RE_CALLOUT_RESUMO.search(corpo)
    if not m:
        return ""
    linhas = [l.lstrip(">").strip() for l in m.group(1).split("\n")]
    return limpar_inline(" ".join(l for l in linhas if l))


def montar_resumo_semantico(nota: Nota, max_chars: int) -> str:
    """
    Texto compacto e denso que representa a nota para o LLM do Passo 3.
    Ordem = prioridade: titulo > materia > TL;DR > headings > conceitos > tags.
    """
    partes: list[str] = []
    if nota.materia and nota.materia != nota.titulo:
        partes.append("[" + nota.materia + "]")
    partes.append(nota.titulo)
    if nota.resumo:
        partes.append("TL;DR: " + nota.resumo)
    secoes = [h["texto"] for h in nota.headings if h["nivel"] in (2, 3)]
    if secoes:
        partes.append("Secoes: " + " | ".join(secoes))
    if nota.conceitos:
        partes.append("Conceitos: " + "; ".join(nota.conceitos[:25]))
    if nota.tags:
        partes.append("Tags: " + ", ".join(nota.tags))

    texto = " || ".join(partes)
    texto = re.sub(r"\s+", " ", texto).strip()
    if len(texto) > max_chars:
        texto = texto[: max_chars - 3].rstrip() + "..."
    return texto


def ler_nota(caminho: Path, vault: Path, max_chars: int) -> Nota | None:
    try:
        texto = caminho.read_text(encoding="utf-8")          # modo leitura
    except UnicodeDecodeError:
        try:
            texto = caminho.read_text(encoding="cp1252")
        except Exception as erro:                            # noqa: BLE001
            print("  ! ilegivel: " + str(caminho) + " (" + str(erro) + ")",
                  file=sys.stderr)
            return None
    except OSError as erro:
        print("  ! erro de leitura: " + str(caminho) + " (" + str(erro) + ")",
              file=sys.stderr)
        return None

    frontmatter, corpo = parse_frontmatter(texto)
    corpo_sem_mermaid = RE_BLOCO_MERMAID.sub(" ", corpo)
    corpo_sem_codigo = RE_BLOCO_CODIGO.sub(" ", corpo_sem_mermaid)

    rel = caminho.relative_to(vault)
    partes_rel = rel.parts
    pasta_rel = str(rel.parent).replace("\\", "/")
    if pasta_rel == ".":
        pasta_rel = ""

    headings = [
        {"nivel": len(m.group(1)), "texto": limpar_inline(m.group(2))}
        for m in RE_HEADING.finditer(corpo_sem_codigo)
    ]
    h1 = next((h["texto"] for h in headings if h["nivel"] == 1), "")

    nome_pasta = rel.parent.name if rel.parent != Path(".") else ""
    m_aula = re.match(r"^\((Aula[^)]*)\)", nome_pasta, re.IGNORECASE)

    conceitos: list[str] = []
    for m in RE_NEGRITO.finditer(corpo_sem_codigo):
        termo = limpar_inline(m.group(1)).rstrip(":").strip()
        if 3 <= len(termo) <= 70 and termo.lower() not in (c.lower() for c in conceitos):
            conceitos.append(termo)

    links: list[str] = []
    for m in RE_WIKILINK.finditer(corpo_sem_codigo):
        alvo = m.group(1).strip().split("/")[-1]
        if alvo and alvo not in links:
            links.append(alvo)

    nota = Nota(
        id=str(rel).replace("\\", "/"),
        titulo=h1 or caminho.stem,
        arquivo=caminho.name,
        caminho=str(rel).replace("\\", "/"),
        pasta=pasta_rel,
        area=partes_rel[0] if len(partes_rel) > 1 else "",
        materia=partes_rel[1] if len(partes_rel) > 2 else "",
        aula=m_aula.group(1) if m_aula else "",
        tags=extrair_tags(frontmatter, corpo_sem_codigo),
        frontmatter=frontmatter,
        headings=headings,
        conceitos=conceitos[:40],
        links=links[:30],
        resumo=extrair_resumo(corpo_sem_codigo),
        # Conta o corpo INTEIRO (inclusive blocos de codigo): uma nota de SQL
        # ou de script e conteudo de estudo, nao uma nota vazia.
        palavras=len(corpo.split()),
        modificado_em=datetime.fromtimestamp(
            caminho.stat().st_mtime
        ).date().isoformat(),
        is_moc=bool(RE_ARQUIVO_MOC.match(caminho.stem)),
    )
    nota.resumo_semantico = montar_resumo_semantico(nota, max_chars)
    return nota


# ---------------------------------------------------------------------------
# Varredura
# ---------------------------------------------------------------------------


def deve_ignorar(rel: Path, ignorar: list[str]) -> bool:
    partes = {p.lower() for p in rel.parts}
    if any(ig.lower() in partes for ig in ignorar):
        return True
    # Arquivo solto na raiz do vault (CLAUDE.md e afins) nao e nota de estudo.
    if len(rel.parts) == 1 and rel.name.lower() in ARQUIVOS_RAIZ_IGNORADOS:
        return True
    # Copias de conflito do Syncthing/Drive sao duplicatas da mesma nota;
    # contadas duas vezes, inflam o acervo e repetem notas no match.
    nome = rel.name.lower()
    return ".sync-conflict" in nome or " (copia em conflito" in nome \
        or "conflicted copy" in nome


def varrer(
    vault: Path,
    ignorar: list[str],
    somente: list[str],
    max_chars: int,
    min_palavras: int,
    incluir_mocs: bool,
) -> tuple[list[Nota], dict]:
    notas: list[Nota] = []
    puladas = {"ignoradas": 0, "fora_do_filtro": 0, "curtas": 0, "mocs": 0, "erros": 0}

    for caminho in sorted(vault.rglob("*.md")):
        if not caminho.is_file():
            continue
        rel = caminho.relative_to(vault)

        if deve_ignorar(rel, ignorar):
            puladas["ignoradas"] += 1
            continue
        if somente:
            alvo = str(rel).replace("\\", "/").lower()
            if not any(alvo.startswith(s.lower().rstrip("/") + "/") for s in somente):
                puladas["fora_do_filtro"] += 1
                continue

        nota = ler_nota(caminho, vault, max_chars)
        if nota is None:
            puladas["erros"] += 1
            continue
        if nota.is_moc and not incluir_mocs:
            puladas["mocs"] += 1
            continue
        if nota.palavras < min_palavras:
            puladas["curtas"] += 1
            continue
        notas.append(nota)

    return notas, puladas


def montar_inventario(vault: Path, notas: list[Nota], puladas: dict) -> dict:
    contagem_tags: dict[str, int] = {}
    for nota in notas:
        for tag in nota.tags:
            contagem_tags[tag] = contagem_tags.get(tag, 0) + 1

    por_materia: dict[str, int] = {}
    for nota in notas:
        chave = nota.materia or nota.area or "(raiz)"
        por_materia[chave] = por_materia.get(chave, 0) + 1

    return {
        "vault": str(vault),
        "gerado_em": date.today().isoformat(),
        "versao_schema": 1,
        "estatisticas": {
            "notas": len(notas),
            "palavras": sum(n.palavras for n in notas),
            "tags_distintas": len(contagem_tags),
            "puladas": puladas,
        },
        "tags": dict(sorted(contagem_tags.items(), key=lambda x: (-x[1], x[0]))),
        "notas_por_materia": dict(sorted(por_materia.items(), key=lambda x: -x[1])),
        "notas": [asdict(n) for n in notas],
    }


def imprimir_preview(inv: dict, limite: int = 5) -> None:
    est = inv["estatisticas"]
    print("\n" + "=" * 72)
    print("  Inventario do vault: " + inv["vault"])
    print(
        "  {0} notas | {1} palavras | {2} tags distintas".format(
            est["notas"], est["palavras"], est["tags_distintas"]
        )
    )
    print("  puladas: " + json.dumps(est["puladas"], ensure_ascii=False))
    print("=" * 72)

    print("\n-- Notas por materia --")
    for materia, qtd in list(inv["notas_por_materia"].items())[:20]:
        print("   {0:>4}  {1}".format(qtd, materia))

    print("\n-- Top tags --")
    for tag, qtd in list(inv["tags"].items())[:20]:
        print("   {0:>4}  #{1}".format(qtd, tag))

    print("\n-- Amostra de resumo_semantico (o que o LLM vai ler) --")
    for nota in inv["notas"][:limite]:
        print("\n   * " + nota["caminho"])
        print("     " + nota["resumo_semantico"][:300])
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Inventaria as notas .md de um vault Obsidian (somente leitura)"
    )
    p.add_argument("--vault", required=True, help="pasta raiz do vault")
    p.add_argument("-o", "--output", default="data/inventario.json", help="saida .json")
    p.add_argument(
        "--somente", nargs="*", default=[],
        help="limita a subpastas (ex.: --somente \"02 - Concursos\")",
    )
    p.add_argument(
        "--ignorar", nargs="*", default=[],
        help="pastas extras a ignorar (somadas ao padrao)",
    )
    p.add_argument(
        "--max-chars", type=int, default=900,
        help="tamanho maximo do resumo_semantico por nota (default: 900)",
    )
    p.add_argument(
        "--min-palavras", type=int, default=30,
        help="ignora notas com menos palavras que isso (default: 30)",
    )
    p.add_argument("--incluir-mocs", action="store_true",
                   help="inclui arquivos MOC/indice no inventario")
    p.add_argument("--preview", action="store_true", help="imprime relatorio")
    p.add_argument(
        "--permitir-saida-no-vault", action="store_true",
        help="autoriza gravar o JSON dentro do vault (bloqueado por seguranca)",
    )
    args = p.parse_args(argv)

    # O console do Windows (cp1252) quebra ao imprimir emojis dos titulos.
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        print("ERRO: vault nao encontrado: " + str(vault), file=sys.stderr)
        return 1

    saida = Path(args.output).expanduser().resolve()
    # Guarda: o JSON pode ficar dentro do vault SOMENTE se estiver na pasta
    # deste projeto (_edital-tracker). Assim nunca poluimos as pastas de estudo.
    raiz_projeto = Path(__file__).resolve().parent
    dentro_do_vault = vault == saida.parent or vault in saida.parents
    dentro_do_projeto = raiz_projeto == saida.parent or raiz_projeto in saida.parents
    if dentro_do_vault and not dentro_do_projeto and not args.permitir_saida_no_vault:
        print(
            "ERRO: a saida gravaria dentro do vault, fora da pasta do projeto:\n"
            "      " + str(saida) + "\n"
            "      Escolha um caminho dentro de " + str(raiz_projeto) + "\n"
            "      ou passe --permitir-saida-no-vault se tiver certeza.",
            file=sys.stderr,
        )
        return 1
    if saida.suffix.lower() == ".md":
        print("ERRO: a saida nao pode ser um arquivo .md.", file=sys.stderr)
        return 1

    ignorar = IGNORAR_PADRAO + list(args.ignorar)
    print("Varrendo (somente leitura): " + str(vault))
    notas, puladas = varrer(
        vault,
        ignorar=ignorar,
        somente=args.somente,
        max_chars=args.max_chars,
        min_palavras=args.min_palavras,
        incluir_mocs=args.incluir_mocs,
    )
    inventario = montar_inventario(vault, notas, puladas)

    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(
        json.dumps(inventario, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OK -> {0}  ({1} notas, {2} tags distintas)".format(
            saida, inventario["estatisticas"]["notas"],
            inventario["estatisticas"]["tags_distintas"],
        )
    )
    if args.preview:
        imprimir_preview(inventario)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
