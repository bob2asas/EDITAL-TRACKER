# -*- coding: utf-8 -*-
"""
edital_parser.py - Passo 1 do pipeline Edital Tracker.

Le o texto bruto do conteudo programatico de um edital (.txt) e produz um
edital.json com a hierarquia Disciplina -> Topico -> Subtopico.

Uso basico:
    python edital_parser.py --input data/edital_raw.txt --output data/edital.json

Uso com revisao (recomendado na primeira vez):
    python edital_parser.py -i data/edital_raw.txt -o data/edital.json --preview

O parser e 100% local e deterministico (sem LLM). Ele nao escreve em nenhum
lugar alem do arquivo de saida informado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuracao de deteccao
# ---------------------------------------------------------------------------

# Disciplinas tipicas da area fiscal. Serve como dica forte: se a linha bate
# com um desses nomes (ignorando acentos/caixa), e cabecalho de disciplina.
DISCIPLINAS_CONHECIDAS = [
    "lingua portuguesa", "portugues", "redacao", "redacao discursiva",
    "raciocinio logico", "raciocinio logico e matematico", "matematica",
    "matematica financeira", "estatistica",
    "lingua inglesa", "ingles",
    "nocoes de informatica", "informatica", "tecnologia da informacao",
    "direito constitucional", "nocoes de direito constitucional",
    "direito administrativo", "nocoes de direito administrativo",
    "direito tributario", "legislacao tributaria", "legislacao tributaria municipal",
    "direito civil", "direito empresarial", "direito penal",
    "direito financeiro", "financas publicas",
    "administracao financeira e orcamentaria",
    "contabilidade geral", "contabilidade publica", "contabilidade de custos",
    "auditoria", "controle externo", "responsabilidade fiscal",
    "administracao publica", "administracao geral", "gestao de pessoas",
    "economia", "etica no servico publico", "etica",
    "legislacao municipal", "conhecimentos especificos", "conhecimentos gerais",
    "conhecimentos basicos", "ciencia de dados", "analise de dados",
    "banco de dados", "seguranca da informacao", "lei geral de protecao de dados",
    "reforma tributaria", "ciencia politica", "inteligencia emocional",
]

# Marcadores de topico no inicio da linha: "1.", "1 -", "1)", "I -", etc.
RE_TOPICO_NUM = re.compile(
    r"^\s*(?P<num>\d{1,2}(?:\.\d{1,2})*)\s*[\.\)\-–—:]\s+(?P<texto>\S.*)$"
)
RE_TOPICO_ROMANO = re.compile(
    r"^\s*(?P<num>[IVXLC]{1,6})\s*[\.\)\-–—:]\s+(?P<texto>\S.*)$"
)
# Marcadores inline: "... texto. 2. Proximo topico. 3. Outro."
RE_SPLIT_INLINE = re.compile(
    r"(?<=[\.;])\s+(?=\d{1,2}(?:\.\d{1,2})*\s*[\.\)\-]\s+\S)"
)
# Cabecalho explicito: "DISCIPLINA: X"
RE_CABECALHO_EXPL = re.compile(
    r"^\s*(?:disciplina|materia|matéria)\s*[:\-]\s*(?P<nome>.+?)\s*$",
    re.IGNORECASE,
)
# Cabecalho embutido, formato comum em editais de banca:
#   "Nocoes de Direito Administrativo: 1. Administracao publica. 2. ..."
# O nome vem antes dos dois-pontos e os topicos seguem na MESMA linha.
RE_CABECALHO_INLINE = re.compile(
    r"^\s*(?P<nome>[A-Za-zÀ-ÿ][^:\n]{3,85}?)\s*:\s+"
    r"(?P<resto>\d{1,2}\s*[\.\)\-]\s+\S.*)$"
)
# Lixo de PDF: numeros de pagina, rodapes, linhas de separacao
RE_LIXO = re.compile(
    r"^\s*(?:p[áa]g(?:ina)?\.?\s*\d+(?:\s*(?:de|/)\s*\d+)?"
    r"|\d{1,3}|[-–—_=\.·•\s]+)\s*$",
    re.IGNORECASE,
)
# Hifenizacao de quebra de linha vinda de PDF: "tribu-\ntario"
RE_HIFEN_QUEBRA = re.compile(r"(\w+)-[ \t]*\n[ \t]*(\w+)")

# Primeiras partes que indicam composto real, nao quebra silabica
COMPOSTOS_PREFIXO = (
    "tabelas", "juridico", "politico", "economico", "social", "teorico",
    "pratico", "administrativo", "tributario", "civil", "penal", "medio",
    "longo", "curto", "pos", "pre", "ex", "vice", "sub", "super", "auto",
)


def _resolver_hifen(m: "re.Match[str]") -> str:
    esquerda, direita = m.group(1), m.group(2)
    chave = sem_acento(esquerda).lower()
    if chave.endswith("s") or chave in COMPOSTOS_PREFIXO:
        return esquerda + "-" + direita
    return esquerda + direita

# Terminacoes que NAO devem encerrar um subtopico (abreviacoes juridicas)
ABREVIACOES = (
    "art", "arts", "inc", "incs", "par", "n", "no", "lc", "ec", "cf",
    "ctn", "lei", "dec", "res", "seg", "cap", "ed", "pag", "ss", "etc",
    "dr", "sr", "sra", "jr", "obs",
)

PROTETOR = "\u0001"  # marcador temporario para pontos que nao quebram frase


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------


@dataclass
class Subtopico:
    id: str
    texto: str
    concluido: bool = False
    notas: list = field(default_factory=list)   # preenchido no Passo 3
    confianca: float = 0.0                      # preenchido no Passo 3
    justificativa: str = ""                     # preenchido no Passo 3


@dataclass
class Topico:
    id: str
    numero: str
    titulo: str
    subtopicos: list = field(default_factory=list)


@dataclass
class Disciplina:
    id: str
    nome: str
    peso: float = 1.0
    topicos: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slug(texto: str, tamanho: int = 48) -> str:
    base = sem_acento(texto).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:tamanho].strip("-") or "item"


def gerar_id(prefixo: str, *partes: str) -> str:
    """ID estavel e legivel: 'direito-tributario::t::a1b2c3d4'."""
    digest = hashlib.sha1("::".join(partes).encode("utf-8")).hexdigest()[:8]
    return prefixo + "::" + digest


def e_cabecalho_disciplina(linha: str) -> str | None:
    """Retorna o nome da disciplina se a linha parecer um cabecalho."""
    bruta = linha.strip().rstrip(":").strip()
    if not bruta or len(bruta) > 90:
        return None

    m = RE_CABECALHO_EXPL.match(linha)
    if m:
        return m.group("nome").strip().rstrip(":")

    # Remove numeracao de bloco ("1. LINGUA PORTUGUESA")
    bruta_sem_num = re.sub(r"^\s*\d{1,2}\s*[\.\)\-–—]\s*", "", bruta).strip()
    normalizada = re.sub(r"\s+", " ", sem_acento(bruta_sem_num).lower())
    normalizada = normalizada.strip(" .:-")

    if normalizada in DISCIPLINAS_CONHECIDAS:
        return bruta_sem_num

    # Heuristica: linha curta, toda em CAIXA ALTA, sem ponto final
    letras = [c for c in bruta_sem_num if c.isalpha()]
    if (
        len(letras) >= 4
        and len(bruta_sem_num.split()) <= 10
        and all(c.isupper() for c in letras)
        and not bruta_sem_num.endswith(".")
    ):
        return bruta_sem_num.title()

    return None


def limpar_texto(bruto: str) -> list[str]:
    """Normaliza o texto bruto e devolve as linhas uteis."""
    texto = bruto.replace("\r\n", "\n").replace("\r", "\n")
    texto = texto.replace(" ", " ").replace("﻿", "")
    # Junta palavra quebrada por hifen no fim da linha. Mantem o hifen quando
    # o resultado e um composto legitimo ("tabelas-verdade", "juridico-penal");
    # so remove quando a quebra foi silabica ("tribu-\ntario").
    texto = RE_HIFEN_QUEBRA.sub(_resolver_hifen, texto)

    linhas: list[str] = []
    for linha in texto.split("\n"):
        linha = re.sub(r"[ \t]+", " ", linha).strip()
        if not linha or RE_LIXO.match(linha):
            continue
        linhas.append(linha)

    # Remove linhas repetidas muitas vezes (cabecalho/rodape de PDF)
    contagem: dict[str, int] = {}
    for linha in linhas:
        if len(linha) < 80:
            contagem[linha] = contagem.get(linha, 0) + 1
    repetidas = {l for l, n in contagem.items() if n >= 4}
    return [l for l in linhas if l not in repetidas]


def juntar_continuacoes(linhas: Iterable[str]) -> list[str]:
    """Junta linhas quebradas no meio de uma frase (comum em PDF)."""
    saida: list[str] = []
    for linha in linhas:
        if (
            saida
            and not RE_TOPICO_NUM.match(linha)
            and not RE_TOPICO_ROMANO.match(linha)
            and e_cabecalho_disciplina(linha) is None
            and not saida[-1].endswith((".", ";", ":"))
            and (linha[:1].islower() or linha[:1] in "(“\"")
        ):
            saida[-1] = saida[-1] + " " + linha
        else:
            saida.append(linha)
    return saida


# ---------------------------------------------------------------------------
# Quebra de subtopicos
# ---------------------------------------------------------------------------


def quebrar_virgulas(texto: str, min_len: int) -> list[str]:
    """
    Quebra uma enumeracao longa por virgulas, ignorando as que estao dentro de
    parenteses. Usado so em subtopicos gigantes - editais costumam enfileirar
    uma dezena de itens numa frase so, e cada um deles e um assunto de estudo.
    """
    cabeca = ""
    corpo = texto
    if ":" in texto:
        cabeca, _, corpo = texto.partition(":")
        cabeca, corpo = cabeca.strip(), corpo.strip()

    partes: list[str] = []
    atual = ""
    nivel = 0
    for ch in corpo:
        if ch in "([":
            nivel += 1
        elif ch in ")]":
            nivel = max(0, nivel - 1)
        if ch == "," and nivel == 0:
            partes.append(atual)
            atual = ""
        else:
            atual += ch
    partes.append(atual)

    itens: list[str] = []
    if cabeca and len(cabeca) >= min_len:
        itens.append(cabeca)
    for parte in partes:
        item = re.sub(r"^\s*e\s+", "", parte.strip(" .;:–—-\t"))
        item = re.sub(r"\s+", " ", item)
        if len(item) < min_len:
            if itens:
                itens[-1] = itens[-1] + ", " + item
            continue
        itens.append(item[0].upper() + item[1:])
    return itens or [texto]


def dividir_subtopicos(texto: str, min_len: int, max_len: int = 0) -> list[str]:
    """
    Editais escrevem tudo corrido:
      "Crase. Regencia verbal e nominal; concordancia. Pontuacao."
    Esta funcao quebra em itens atomicos por ';' e por '.' respeitando
    abreviacoes juridicas e numeros de artigos/leis.
    """
    texto = texto.strip()
    if not texto:
        return []

    # Protege numeros com ponto: "Lei n. 5.986/2003", "art. 1.o", "LC 116/2003"
    protegido = re.sub(r"(?<=\d)\.(?=\d)", PROTETOR, texto)
    for abrev in ABREVIACOES:
        protegido = re.sub(
            r"\b(" + re.escape(abrev) + r")\.",
            r"\1" + PROTETOR,
            protegido,
            flags=re.IGNORECASE,
        )

    partes = re.split(r"\s*;\s*|\.\s+|\.$", protegido)

    itens: list[str] = []
    pendente = ""  # fragmento curto que ainda nao tem onde se encaixar
    for parte in partes:
        item = parte.replace(PROTETOR, ".").strip(" .;:–—-\t")
        item = re.sub(r"\s+", " ", item)
        # "; e Municipios" vira "Municipios", nao "E Municipios"
        item = re.sub(r"^e\s+(?=\S)", "", item, flags=re.IGNORECASE)
        if not item:
            continue
        if pendente:
            item = pendente + "; " + item
            pendente = ""
        if len(item) < min_len:
            # Siglas ("IPTU", "ISS", "CBS") sao itens legitimos do edital.
            if item.isupper() and len(item) >= 2:
                itens.append(item)
                continue
            # Fragmento solto ("e nominal") -> cola no anterior; se nao houver
            # anterior, guarda para colar no proximo (nunca descarta conteudo).
            if itens:
                itens[-1] = itens[-1] + "; " + item
            else:
                pendente = item
            continue
        itens.append(item[0].upper() + item[1:])
    if pendente:
        itens.append(pendente)

    # Enumeracoes gigantes viram varios subtopicos, senao uma lista de 15
    # assuntos contaria como um unico item no percentual.
    if max_len > 0:
        refinados: list[str] = []
        for item in itens:
            if len(item) > max_len and item.count(",") >= 2:
                refinados.extend(quebrar_virgulas(item, min_len))
            else:
                refinados.append(item)
        return refinados
    return itens


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------


def parsear(
    bruto: str,
    concurso: str,
    cargo: str,
    banca: str,
    min_len: int,
    disciplina_padrao: str,
    disciplinas_extras: list | None = None,
    max_len: int = 0,
) -> dict:
    linhas = juntar_continuacoes(limpar_texto(bruto))

    disciplinas: list[Disciplina] = []
    atual_disc: Disciplina | None = None
    buffer_topicos: list[tuple[str, str]] = []  # (numero, texto)

    def garantir_disciplina(nome: str) -> Disciplina:
        nonlocal atual_disc
        for d in disciplinas:
            if sem_acento(d.nome).lower() == sem_acento(nome).lower():
                atual_disc = d
                return d
        nova = Disciplina(id=slug(nome), nome=nome.strip())
        disciplinas.append(nova)
        atual_disc = nova
        return nova

    def gravar_topicos() -> None:
        nonlocal buffer_topicos
        if not buffer_topicos:
            return
        disc = atual_disc or garantir_disciplina(disciplina_padrao)
        for numero, texto in buffer_topicos:
            itens = dividir_subtopicos(texto, min_len, max_len)
            if not itens:
                continue
            titulo = itens[0]
            top_id = gerar_id(disc.id + "::t", disc.nome, numero, titulo)
            topico = Topico(id=top_id, numero=numero, titulo=titulo)
            for posicao_sub, item in enumerate(itens):
                # a posicao entra no hash porque um edital pode repetir o mesmo
                # texto dentro do mesmo topico (ex.: "Poder vinculado" em 2.1 e 2.3)
                topico.subtopicos.append(
                    Subtopico(
                        id=gerar_id(top_id + "::s", str(posicao_sub), item),
                        texto=item,
                    )
                )
            disc.topicos.append(topico)
        buffer_topicos = []

    declaradas = {
        re.sub(r"\s+", " ", sem_acento(d).lower().strip(" .:-"))
        for d in (disciplinas_extras or [])
    }

    contador_sem_num = 0
    for posicao, linha in enumerate(linhas):
        # (a) nome declarado pelo usuario em --disciplinas
        chave = re.sub(r"\s+", " ", sem_acento(linha).lower().strip(" .:-"))
        if chave in declaradas:
            gravar_topicos()
            garantir_disciplina(linha.strip().rstrip(":"))
            continue

        # (b) cabecalho pelo formato ou pela caixa alta
        nome_disc = e_cabecalho_disciplina(linha)
        if nome_disc:
            gravar_topicos()
            garantir_disciplina(nome_disc)
            continue

        # (c) titulo curto cuja linha seguinte abre com "1." -> e um cabecalho
        if (
            len(linha) <= 85
            and not linha.endswith((".", ";"))
            and not RE_TOPICO_NUM.match(linha)
            and posicao + 1 < len(linhas)
            and re.match(r"^\s*1\s*[\.\)\-]\s+\S", linhas[posicao + 1])
        ):
            gravar_topicos()
            garantir_disciplina(linha.strip().rstrip(":"))
            continue

        # (d) "Nome da Disciplina: 1. topico. 2. topico." na mesma linha
        inline = RE_CABECALHO_INLINE.match(linha)
        if inline:
            gravar_topicos()
            garantir_disciplina(inline.group("nome"))
            linha = inline.group("resto")

        # Uma mesma linha pode conter varios topicos numerados inline
        for pedaco in RE_SPLIT_INLINE.split(linha):
            pedaco = pedaco.strip()
            if not pedaco:
                continue
            m = RE_TOPICO_NUM.match(pedaco) or RE_TOPICO_ROMANO.match(pedaco)
            if m:
                buffer_topicos.append((m.group("num"), m.group("texto").strip()))
            elif buffer_topicos:
                # continuacao do topico anterior
                num, texto = buffer_topicos[-1]
                buffer_topicos[-1] = (num, texto + " " + pedaco)
            else:
                contador_sem_num += 1
                buffer_topicos.append((str(contador_sem_num), pedaco))

    gravar_topicos()

    # Descarta disciplinas vazias (cabecalhos falso-positivos)
    disciplinas = [d for d in disciplinas if d.topicos]

    total_sub = sum(len(t.subtopicos) for d in disciplinas for t in d.topicos)
    return {
        "concurso": concurso,
        "cargo": cargo,
        "banca": banca,
        "gerado_em": date.today().isoformat(),
        "versao_schema": 1,
        "estatisticas": {
            "disciplinas": len(disciplinas),
            "topicos": sum(len(d.topicos) for d in disciplinas),
            "subtopicos": total_sub,
            "subtopicos_concluidos": 0,
        },
        "disciplinas": [asdict(d) for d in disciplinas],
    }


# ---------------------------------------------------------------------------
# Relatorio de conferencia
# ---------------------------------------------------------------------------


def imprimir_preview(dados: dict, limite: int = 4) -> None:
    est = dados["estatisticas"]
    print("\n" + "=" * 72)
    print("  " + dados["concurso"] + " - " + dados["cargo"])
    print(
        "  {0} disciplinas | {1} topicos | {2} subtopicos".format(
            est["disciplinas"], est["topicos"], est["subtopicos"]
        )
    )
    print("=" * 72)
    for disc in dados["disciplinas"]:
        n_sub = sum(len(t["subtopicos"]) for t in disc["topicos"])
        print(
            "\n[{0}]  ({1} topicos / {2} subtopicos)".format(
                disc["nome"], len(disc["topicos"]), n_sub
            )
        )
        for topico in disc["topicos"][:limite]:
            print("   {0}. {1}".format(topico["numero"], topico["titulo"][:70]))
            for sub in topico["subtopicos"][:limite]:
                print("       - " + sub["texto"][:68])
            if len(topico["subtopicos"]) > limite:
                print("       ... +{0} subtopicos".format(
                    len(topico["subtopicos"]) - limite))
        if len(disc["topicos"]) > limite:
            print("   ... +{0} topicos".format(len(disc["topicos"]) - limite))
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Converte o conteudo programatico de um edital em edital.json"
    )
    p.add_argument("-i", "--input", required=True, help="arquivo .txt do edital")
    p.add_argument("--id", default="",
                   help="identificador do edital (ex.: iss-gru). "
                        "Grava em editais/<id>/edital.json")
    p.add_argument("-o", "--output", default="",
                   help="saida .json (ignorado quando --id e usado)")
    p.add_argument("--concurso", default="Concurso", help="nome do concurso")
    p.add_argument("--cargo", default="", help="cargo/especialidade")
    p.add_argument("--banca", default="", help="banca organizadora")
    p.add_argument(
        "--min-len", type=int, default=12,
        help="tamanho minimo de um subtopico (default: 12 caracteres)",
    )
    p.add_argument(
        "--disciplina-padrao", default="Conhecimentos Especificos",
        help="disciplina usada quando o texto nao tem cabecalhos",
    )
    p.add_argument("--max-len", type=int, default=220,
                   help="acima disso, uma enumeracao e quebrada por virgulas "
                        "(0 desativa; default: 220)")
    p.add_argument("--disciplinas", nargs="*", default=[],
                   help="nomes de linhas que devem virar disciplinas, quando o "
                        "layout do edital nao deixa isso obvio")
    p.add_argument("--reset", action="store_true",
                   help="zera o progresso em vez de preservar o que ja estava "
                        "marcado como concluido")
    p.add_argument("--preview", action="store_true",
                   help="imprime arvore de conferencia")
    p.add_argument(
        "--encoding", default="utf-8",
        help="encoding do arquivo de entrada (tente cp1252 se der erro)",
    )
    args = p.parse_args(argv)

    entrada = Path(args.input)
    if not entrada.is_file():
        print("ERRO: arquivo nao encontrado: " + str(entrada), file=sys.stderr)
        return 1

    try:
        bruto = entrada.read_text(encoding=args.encoding)
    except UnicodeDecodeError:
        print(
            "ERRO: falha ao ler com encoding '" + args.encoding + "'. "
            "Tente --encoding cp1252 ou latin-1.",
            file=sys.stderr,
        )
        return 1

    dados = parsear(
        bruto,
        concurso=args.concurso,
        cargo=args.cargo,
        banca=args.banca,
        min_len=args.min_len,
        disciplina_padrao=args.disciplina_padrao,
        disciplinas_extras=args.disciplinas,
        max_len=args.max_len,
    )

    if not dados["disciplinas"]:
        print(
            "AVISO: nenhuma disciplina/topico reconhecido. "
            "Confira o arquivo de entrada.",
            file=sys.stderr,
        )

    # Multi-edital: cada concurso mora em editais/<id>/edital.json
    identificador = args.id or slug(args.concurso)
    dados["id"] = identificador
    if args.id or not args.output:
        saida = Path("editais") / identificador / "edital.json"
    else:
        saida = Path(args.output)

    # Se ja existe uma versao marcada deste edital, preserva o que foi concluido
    if saida.is_file() and not args.reset:
        anterior = json.loads(saida.read_text(encoding="utf-8"))
        # Preserva TUDO que ja foi avaliado - nao so o que ficou concluido.
        # Um subtopico marcado como parcial carrega confianca e justificativa,
        # e perde-las ao reprocessar apagaria metade do diagnostico.
        avaliados = {}
        for disc in anterior.get("disciplinas", []):
            for topico in disc.get("topicos", []):
                for sub in topico.get("subtopicos", []):
                    if (sub.get("concluido") or sub.get("confianca")
                            or sub.get("justificativa")):
                        avaliados[sub["id"]] = sub
        if avaliados:
            mantidos = 0
            concluidos = 0
            for disc in dados["disciplinas"]:
                for topico in disc["topicos"]:
                    for sub in topico["subtopicos"]:
                        velho = avaliados.get(sub["id"])
                        if velho:
                            sub.update({
                                "concluido": bool(velho.get("concluido")),
                                "notas": velho.get("notas", []),
                                "confianca": velho.get("confianca", 0.0),
                                "justificativa": velho.get("justificativa", ""),
                            })
                            mantidos += 1
                            if sub["concluido"]:
                                concluidos += 1
            dados["estatisticas"]["subtopicos_concluidos"] = concluidos
            if anterior.get("match"):
                dados["match"] = anterior["match"]
            print("progresso preservado: {0} avaliados, {1} concluidos".format(
                mantidos, concluidos))

    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    est = dados["estatisticas"]
    print(
        "OK -> {0}  ({1} disciplinas, {2} topicos, {3} subtopicos)".format(
            saida, est["disciplinas"], est["topicos"], est["subtopicos"]
        )
    )
    if args.preview:
        imprimir_preview(dados)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
