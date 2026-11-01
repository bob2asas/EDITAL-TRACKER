# -*- coding: utf-8 -*-
"""
semantic_matcher.py - Passo 3 do pipeline Edital Tracker.

Cruza os subtopicos do edital.json com o inventario de notas do Obsidian
usando a Messages API da Anthropic, e marca `concluido: true` onde a nota
realmente cobre o subtopico.

Instalacao (uma vez):
    pip install anthropic
    setx ANTHROPIC_API_KEY "sk-ant-..."     (ou use `ant auth login`)

Uso:
    # 1) ensaio gratuito, sem gastar API: so o pre-filtro lexico
    python semantic_matcher.py --dry-run --preview

    # 2) teste pago curto (2 lotes) antes de rodar tudo
    python semantic_matcher.py --limite-lotes 2

    # 3) rodada completa
    python semantic_matcher.py

Como funciona:
    1. Pre-filtro lexico local escolhe as notas candidatas de cada subtopico
       (barato, reduz o prompt em ~90%).
    2. Os subtopicos vao em lotes por disciplina; as notas candidatas do lote
       entram no system prompt COM prompt caching (reuso entre lotes).
    3. O modelo responde em JSON validado por schema (structured outputs).
    4. O edital.json e atualizado preservando o que ja estava concluido.

Seguranca: le e escreve apenas edital.json / inventario.json / cache local.
Nunca toca nos arquivos .md do vault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None

MODELO_PADRAO = "claude-opus-5-5"

# Precos por 1M de tokens (USD) - usados so para estimar custo no log
PRECOS = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Palavras sem valor discriminativo no pre-filtro lexico
STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "das", "dos", "em", "no", "na", "nos",
    "nas", "por", "para", "com", "sem", "sob", "ante", "apos", "ate", "entre",
    "que", "se", "os", "as", "um", "uma", "uns", "umas", "ao", "aos", "pelo",
    "pela", "seu", "sua", "seus", "suas", "este", "esta", "isso", "the", "of",
    "e-", "ou", "nao", "sao", "ser", "tem", "suas", "sobre", "seus", "geral",
    "gerais", "aspectos", "nocoes", "conceito", "conceitos", "principios",
    "introducao", "parte", "topico", "topicos", "estudo", "analise",
}

SCHEMA_RESPOSTA = {
    "type": "object",
    "properties": {
        "resultados": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subtopico_id": {"type": "string"},
                    "coberto": {"type": "boolean"},
                    "notas": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "ids das notas que cobrem o subtopico",
                    },
                    "confianca": {
                        "type": "number",
                        "description": "0.0 a 1.0",
                    },
                    "justificativa": {
                        "type": "string",
                        "description": "uma frase curta",
                    },
                },
                "required": [
                    "subtopico_id", "coberto", "notas", "confianca", "justificativa",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["resultados"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCOES = """Voce e um avaliador rigoroso de cobertura de conteudo \
programatico para concursos publicos brasileiros da area fiscal.

Recebe (1) um CATALOGO de notas de estudo de um candidato e (2) uma lista de \
SUBTOPICOS de um edital. Para cada subtopico, decida se o candidato JA ESTUDOU \
aquele conteudo, com base no que as notas demonstram cobrir.

REGRAS DE DECISAO (siga com rigor - inflar a cobertura prejudica o candidato):
1. `coberto: true` SOMENTE se alguma nota trata especificamente daquele \
subtopico - pelo titulo, pelo TL;DR, por uma secao dedicada ou por conceitos \
listados. Tratamento direto, nao tangencial.
2. Pertencer a mesma disciplina NAO basta. Uma nota de "Credito Tributario" \
nao cobre "Fato Gerador" so por ser de Direito Tributario.
3. Mencao de passagem, sinonimo isolado ou um conceito solto NAO e cobertura.
4. Se o subtopico tem varias partes ("Suspensao, extincao e exclusao") e as \
notas cobrem apenas uma, marque `coberto: false` e explique na justificativa.
5. Na duvida, marque `coberto: false`. Falso negativo custa uma revisao; \
falso positivo faz o candidato ir para a prova achando que sabe.

CONFIANCA:
- 0.90-1.00: nota com secao/titulo dedicado ao subtopico
- 0.70-0.89: conteudo claramente presente, mas diluido em nota maior
- 0.50-0.69: indicios razoaveis, porem incompletos
- abaixo de 0.50: nao considere coberto

Em `notas`, use EXATAMENTE os ids do catalogo (o caminho do arquivo). \
Liste no maximo 3, as mais relevantes. Se `coberto` for false, deixe a lista \
vazia. Responda um resultado para CADA subtopico recebido, sem excecao."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + msg, flush=True)


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokenizar(texto: str) -> set[str]:
    bruto = sem_acento(texto).lower()
    bruto = re.sub(r"[^a-z0-9\s]+", " ", bruto)
    return {
        t for t in bruto.split()
        if len(t) >= 4 and t not in STOPWORDS
    }


def radicais(tokens: set[str]) -> set[str]:
    """Corta sufixos para casar genero/numero: portugues ~ portuguesa,
    tributario ~ tributaria, logico ~ logica."""
    return {t[:6] for t in tokens}


def carregar_json(caminho: Path) -> dict:
    if not caminho.is_file():
        print("ERRO: arquivo nao encontrado: " + str(caminho), file=sys.stderr)
        raise SystemExit(1)
    return json.loads(caminho.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pre-filtro lexico (local, gratuito)
# ---------------------------------------------------------------------------


def indexar_notas(notas: list[dict]) -> list[dict]:
    """Pre-computa os tokens de cada nota, com pesos por campo."""
    indice = []
    for nota in notas:
        tokens_fortes = tokenizar(nota.get("titulo", ""))
        tokens_fortes |= tokenizar(" ".join(nota.get("tags", [])))
        tokens_fortes |= tokenizar(nota.get("materia", ""))

        tokens_medios = tokenizar(" ".join(
            h.get("texto", "") for h in nota.get("headings", [])
        ))
        tokens_medios |= tokenizar(" ".join(nota.get("conceitos", [])))

        tokens_fracos = tokenizar(nota.get("resumo", ""))

        indice.append({
            "nota": nota,
            "fortes": tokens_fortes,
            "medios": tokens_medios - tokens_fortes,
            "fracos": tokens_fracos - tokens_fortes - tokens_medios,
            # pasta da nota: usada para o bonus de afinidade com a disciplina
            "materia": radicais(
                tokenizar(nota.get("materia", "") + " " + nota.get("area", ""))
            ),
        })
    return indice


def pontuar(tokens_sub: set[str], entrada: dict) -> float:
    if not tokens_sub:
        return 0.0
    bruto = (
        3.0 * len(tokens_sub & entrada["fortes"])
        + 2.0 * len(tokens_sub & entrada["medios"])
        + 1.0 * len(tokens_sub & entrada["fracos"])
    )
    # normaliza pelo tamanho do subtopico (evita favorecer frases longas)
    return bruto / math.sqrt(len(tokens_sub))


def candidatas(
    texto_sub: str, contexto: str, disciplina: str, indice: list[dict], topk: int
) -> list[dict]:
    tokens = tokenizar(texto_sub) | tokenizar(contexto)
    tokens_disc = radicais(tokenizar(disciplina))

    pontuadas = []
    for entrada in indice:
        nota_pontos = pontuar(tokens, entrada)
        if nota_pontos <= 0:
            continue
        # Nota guardada na pasta da mesma materia do edital vale mais. E so um
        # bonus: uma nota de outra pasta ainda pode vencer se o texto casar bem
        # (ex.: Reforma Tributaria cobrindo topico de Direito Tributario).
        if tokens_disc & entrada["materia"]:
            nota_pontos *= 1.8
        pontuadas.append((nota_pontos, entrada["nota"]))

    pontuadas.sort(key=lambda x: -x[0])
    return [n for _, n in pontuadas[:topk]]


# ---------------------------------------------------------------------------
# Montagem dos lotes
# ---------------------------------------------------------------------------


def montar_lotes(
    edital: dict,
    indice: list[dict],
    tamanho_lote: int,
    topk: int,
    max_notas_lote: int,
    refazer: bool,
) -> list[dict]:
    """Agrupa subtopicos por disciplina e anexa as notas candidatas do lote."""
    lotes: list[dict] = []

    for disc in edital["disciplinas"]:
        pendentes: list[dict] = []
        for topico in disc["topicos"]:
            for sub in topico["subtopicos"]:
                if sub.get("concluido") and not refazer:
                    continue
                pendentes.append({
                    "id": sub["id"],
                    "texto": sub["texto"],
                    "topico": topico["titulo"],
                    "_ref": sub,
                })

        for inicio in range(0, len(pendentes), tamanho_lote):
            fatia = pendentes[inicio: inicio + tamanho_lote]
            vistas: dict[str, dict] = {}
            for item in fatia:
                contexto = disc["nome"] + " " + item["topico"]
                for nota in candidatas(
                    item["texto"], contexto, disc["nome"], indice, topk
                ):
                    vistas.setdefault(nota["id"], nota)
            notas_lote = list(vistas.values())[:max_notas_lote]
            if not notas_lote:
                continue   # sem candidata nenhuma: nao gasta chamada
            lotes.append({
                "disciplina": disc["nome"],
                "subtopicos": fatia,
                "notas": notas_lote,
            })
    return lotes


def render_catalogo(notas: list[dict]) -> str:
    linhas = []
    for nota in notas:
        linhas.append(
            "- id: " + nota["id"] + "\n  " + nota.get("resumo_semantico", nota["titulo"])
        )
    return "\n".join(linhas)


def render_subtopicos(disciplina: str, subtopicos: list[dict]) -> str:
    linhas = ["DISCIPLINA: " + disciplina, "", "SUBTOPICOS A AVALIAR:"]
    for item in subtopicos:
        linhas.append(
            "- subtopico_id: " + item["id"]
            + "\n  topico: " + item["topico"]
            + "\n  subtopico: " + item["texto"]
        )
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Cache em disco (evita repagar a mesma pergunta)
# ---------------------------------------------------------------------------


class Cache:
    def __init__(self, pasta: Path, ativo: bool) -> None:
        self.pasta = pasta
        self.ativo = ativo
        if ativo:
            pasta.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def chave(modelo: str, catalogo: str, pergunta: str) -> str:
        bruto = modelo + "\x00" + catalogo + "\x00" + pergunta
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]

    def ler(self, chave: str):
        if not self.ativo:
            return None
        arq = self.pasta / (chave + ".json")
        if arq.is_file():
            try:
                return json.loads(arq.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def gravar(self, chave: str, dados) -> None:
        if not self.ativo:
            return
        (self.pasta / (chave + ".json")).write_text(
            json.dumps(dados, ensure_ascii=False), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Chamada a API
# ---------------------------------------------------------------------------


def chamar_modelo(
    client, modelo: str, esforco: str, catalogo: str, pergunta: str,
    max_tentativas: int, uso: dict,
):
    """Uma chamada com structured output + prompt caching do catalogo."""
    ultima_excecao = None

    for tentativa in range(1, max_tentativas + 1):
        try:
            resposta = client.messages.create(
                model=modelo,
                max_tokens=16000,
                output_config={
                    "effort": esforco,
                    "format": {"type": "json_schema", "schema": SCHEMA_RESPOSTA},
                },
                system=[
                    {"type": "text", "text": SYSTEM_INSTRUCOES},
                    {
                        "type": "text",
                        "text": "CATALOGO DE NOTAS DO CANDIDATO:\n" + catalogo,
                        # o catalogo se repete entre lotes -> cache de 1h
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    },
                ],
                messages=[{"role": "user", "content": pergunta}],
            )

            if resposta.stop_reason == "refusal":
                detalhe = getattr(resposta, "stop_details", None)
                raise RuntimeError(
                    "modelo recusou a requisicao: "
                    + str(getattr(detalhe, "category", "?"))
                )
            if resposta.stop_reason == "max_tokens":
                raise RuntimeError("resposta truncada (max_tokens) - reduza --lote")

            uso["entrada"] += resposta.usage.input_tokens
            uso["saida"] += resposta.usage.output_tokens
            uso["cache_escrita"] += getattr(
                resposta.usage, "cache_creation_input_tokens", 0) or 0
            uso["cache_leitura"] += getattr(
                resposta.usage, "cache_read_input_tokens", 0) or 0
            uso["chamadas"] += 1

            texto = "".join(
                bloco.text for bloco in resposta.content if bloco.type == "text"
            )
            return json.loads(texto)

        except anthropic.RateLimitError as erro:
            ultima_excecao = erro
            espera = float(erro.response.headers.get("retry-after", 0) or 0)
            if espera <= 0:
                espera = min(60.0, 2.0 ** tentativa)
            log("  ! rate limit (429). aguardando {0:.0f}s "
                "(tentativa {1}/{2})".format(espera, tentativa, max_tentativas))
            time.sleep(espera)

        except anthropic.APIStatusError as erro:
            ultima_excecao = erro
            if erro.status_code >= 500:
                espera = min(60.0, 2.0 ** tentativa)
                log("  ! erro {0} no servidor. aguardando {1:.0f}s".format(
                    erro.status_code, espera))
                time.sleep(espera)
            else:
                raise   # 400/401/404: erro nosso, nao adianta repetir

        except anthropic.APIConnectionError as erro:
            ultima_excecao = erro
            espera = min(30.0, 2.0 ** tentativa)
            log("  ! falha de conexao. aguardando {0:.0f}s".format(espera))
            time.sleep(espera)

        except json.JSONDecodeError as erro:
            ultima_excecao = erro
            log("  ! resposta nao era JSON valido. repetindo.")

    raise RuntimeError("falhou apos " + str(max_tentativas) +
                       " tentativas: " + str(ultima_excecao))


# ---------------------------------------------------------------------------
# Aplicacao dos resultados
# ---------------------------------------------------------------------------


def aplicar(
    lote: dict, resultados: list[dict], min_confianca: float, estat: dict
) -> None:
    por_id = {item["id"]: item["_ref"] for item in lote["subtopicos"]}
    ids_validos = {nota["id"] for nota in lote["notas"]}
    respondidos = set()

    for res in resultados:
        ref = por_id.get(res.get("subtopico_id", ""))
        if ref is None:
            estat["ids_desconhecidos"] += 1
            continue
        respondidos.add(res["subtopico_id"])

        confianca = float(res.get("confianca", 0.0) or 0.0)
        coberto = bool(res.get("coberto")) and confianca >= min_confianca
        # so aceita ids de nota que realmente existem no lote (anti-alucinacao)
        notas = [n for n in res.get("notas", []) if n in ids_validos]
        if coberto and not notas:
            coberto = False
            estat["sem_nota_valida"] += 1

        ref["concluido"] = coberto
        ref["confianca"] = round(confianca, 2)
        ref["notas"] = notas[:3]
        ref["justificativa"] = str(res.get("justificativa", ""))[:300]

        if coberto:
            estat["cobertos"] += 1
        else:
            estat["nao_cobertos"] += 1

    faltantes = set(por_id) - respondidos
    estat["sem_resposta"] += len(faltantes)


def importar_decisoes(edital: dict, caminho: Path, arquivo: Path, args) -> int:
    """
    Modo sem API: aplica um mapa de decisoes ja pronto.

    Formato aceito (dois sabores):
      {"<subtopico_id>": true}
      {"<subtopico_id>": {"coberto": true, "notas": ["..."],
                          "confianca": 0.9, "justificativa": "..."}}
    """
    if not arquivo.is_file():
        print("ERRO: arquivo de decisoes nao encontrado: " + str(arquivo),
              file=sys.stderr)
        return 1

    decisoes = json.loads(arquivo.read_text(encoding="utf-8"))
    if isinstance(decisoes, dict) and "decisoes" in decisoes:
        decisoes = decisoes["decisoes"]
    # chaves iniciadas por "_" sao metadados do arquivo, nao subtopicos
    decisoes = {k: v for k, v in decisoes.items() if not str(k).startswith("_")}

    # Permite citar a nota por um pedaco do titulo em vez do caminho inteiro.
    catalogo: dict[str, str] = {}
    inventario_path = Path(args.inventario)
    if inventario_path.is_file():
        for nota in json.loads(
            inventario_path.read_text(encoding="utf-8")
        ).get("notas", []):
            catalogo[sem_acento(nota["titulo"]).lower()] = nota["caminho"]
            catalogo[sem_acento(Path(nota["caminho"]).stem).lower()] = nota["caminho"]

    def resolver(referencia: str) -> str:
        if "/" in referencia:
            return referencia
        alvo = sem_acento(referencia).lower()
        if alvo in catalogo:
            return catalogo[alvo]
        for titulo, caminho in catalogo.items():
            if alvo in titulo:
                return caminho
        return referencia

    # As chaves podem ser o id do subtopico ou o proprio texto dele - escrever
    # decisoes a mao fica muito mais simples usando o texto.
    def chave_normal(texto: str) -> str:
        return re.sub(r"\s+", " ", sem_acento(str(texto)).lower().strip(" .;:"))

    # Uma chave pode ser "Disciplina :: subtopico" para desfazer ambiguidade -
    # o mesmo texto ("Caracteristicas") aparece em disciplinas diferentes.
    por_texto: dict[str, str] = {}
    por_disc_texto: dict[tuple[str, str], str] = {}
    colisoes: list[tuple[str, str]] = []
    for k in decisoes:
        if "::" in str(k):
            disc_k, _, texto_k = str(k).partition("::")
            por_disc_texto[(chave_normal(disc_k), chave_normal(texto_k))] = k
        else:
            alvo = chave_normal(k)
            if alvo in por_texto:
                # duas chaves que so diferem por acento/caixa - a segunda
                # apagaria a primeira sem aviso
                colisoes.append((por_texto[alvo], str(k)))
            por_texto[alvo] = k

    # Avisa quando uma chave simples atinge mais de uma disciplina
    alcance: dict[str, set] = {}
    for disc in edital["disciplinas"]:
        for topico in disc["topicos"]:
            for sub in topico["subtopicos"]:
                alvo = chave_normal(sub["texto"])
                if alvo in por_texto:
                    alcance.setdefault(alvo, set()).add(disc["nome"])
    ambiguas = {k: v for k, v in alcance.items() if len(v) > 1}

    aplicados = 0
    desconhecidos = set(decisoes)
    for disc in edital["disciplinas"]:
        for topico in disc["topicos"]:
            for sub in topico["subtopicos"]:
                chave = sub["id"]
                bruto = decisoes.get(chave)
                if bruto is None:
                    normalizado = chave_normal(sub["texto"])
                    chave = por_disc_texto.get(
                        (chave_normal(disc["nome"]), normalizado)
                    ) or por_texto.get(normalizado)
                    bruto = decisoes.get(chave) if chave else None
                if bruto is None:
                    continue
                desconhecidos.discard(chave)
                if isinstance(bruto, bool):
                    bruto = {"coberto": bruto, "confianca": 1.0 if bruto else 0.0}
                confianca = float(bruto.get("confianca", 1.0) or 0.0)
                coberto = bool(bruto.get("coberto")) and confianca >= args.min_confianca
                sub["concluido"] = coberto
                sub["confianca"] = round(confianca, 2)
                sub["notas"] = [resolver(x) for x in bruto.get("notas", [])][:3]
                sub["justificativa"] = str(bruto.get("justificativa", ""))[:300]
                aplicados += 1

    recalcular(edital)
    edital["match"] = {
        "modelo": "importado (" + arquivo.name + ")",
        "min_confianca": args.min_confianca,
        "executado_em": datetime.now().isoformat(timespec="seconds"),
    }

    saida = Path(args.output) if args.output else caminho
    saida.write_text(json.dumps(edital, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    est = edital["estatisticas"]
    log("decisoes aplicadas: {0}".format(aplicados))
    for primeira, segunda in colisoes:
        log("  ! duas chaves viram a mesma depois de normalizar: '"
            + primeira[:40] + "' e '" + segunda[:40] + "'")
        log("    a segunda venceu; qualifique com \"<Disciplina> :: <texto>\"")
    for texto, nomes in sorted(ambiguas.items()):
        log("  ! chave ambigua '" + texto[:48] + "' atinge "
            + str(len(nomes)) + " disciplinas: " + ", ".join(sorted(nomes))[:90])
        log("    use \"<Disciplina> :: <subtopico>\" para separar")
    if desconhecidos:
        log("ids do arquivo que nao existem neste edital: " + str(len(desconhecidos)))
    log("RESULTADO: {0}/{1} subtopicos cobertos ({2}%)".format(
        est["subtopicos_concluidos"], est["subtopicos"], est["percentual"]))
    log("-> " + str(saida))
    return 0


def recalcular(edital: dict) -> None:
    total = 0
    concluidos = 0
    for disc in edital["disciplinas"]:
        for topico in disc["topicos"]:
            for sub in topico["subtopicos"]:
                total += 1
                if sub.get("concluido"):
                    concluidos += 1
    edital.setdefault("estatisticas", {})
    edital["estatisticas"]["subtopicos"] = total
    edital["estatisticas"]["subtopicos_concluidos"] = concluidos
    edital["estatisticas"]["percentual"] = (
        round(100.0 * concluidos / total, 2) if total else 0.0
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Cruza edital.json com o inventario do Obsidian via LLM"
    )
    p.add_argument("--id", default="",
                   help="identificador do edital em editais/<id>/edital.json")
    p.add_argument("--todos", action="store_true",
                   help="processa todos os editais da pasta editais/")
    p.add_argument("-e", "--edital", default="",
                   help="caminho direto do edital.json (alternativa a --id)")
    p.add_argument("-n", "--inventario", default="data/inventario.json")
    p.add_argument("-o", "--output", default="", help="default: sobrescreve o edital")
    p.add_argument("--importar", default="",
                   help="aplica decisoes de um JSON pronto, SEM chamar a API "
                        "(formato: {\"subtopico_id\": {\"coberto\":true,...}})")
    p.add_argument("--modelo", default=MODELO_PADRAO,
                   help="default: " + MODELO_PADRAO)
    p.add_argument("--esforco", default="medium",
                   choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--lote", type=int, default=20,
                   help="subtopicos por chamada (default: 20)")
    p.add_argument("--topk", type=int, default=10,
                   help="notas candidatas por subtopico (default: 10)")
    p.add_argument("--max-notas-lote", type=int, default=45,
                   help="teto de notas no catalogo de cada lote (default: 45)")
    p.add_argument("--min-confianca", type=float, default=0.70,
                   help="confianca minima para marcar concluido (default: 0.70)")
    p.add_argument("--max-tentativas", type=int, default=5)
    p.add_argument("--pausa", type=float, default=0.0,
                   help="segundos de pausa entre lotes (default: 0)")
    p.add_argument("--limite-lotes", type=int, default=0,
                   help="processa so os N primeiros lotes (teste)")
    p.add_argument("--refazer", action="store_true",
                   help="reavalia tambem os subtopicos ja concluidos")
    p.add_argument("--sem-cache", action="store_true",
                   help="ignora o cache local de respostas")
    p.add_argument("--dry-run", action="store_true",
                   help="so o pre-filtro lexico, sem chamar a API (gratuito)")
    p.add_argument("--preview", action="store_true",
                   help="mostra os lotes montados")
    args = p.parse_args(argv)

    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    alvos = resolver_editais(args)
    if not alvos:
        print("ERRO: nenhum edital encontrado. Use --id, --todos ou -e.\n"
              "      Crie um com:  python edital_parser.py --id <nome> -i <txt>",
              file=sys.stderr)
        return 1

    inventario = carregar_json(Path(args.inventario))
    notas = inventario["notas"]
    log("indexando {0} notas para o pre-filtro lexico...".format(len(notas)))
    indice = indexar_notas(notas)

    codigo = 0
    for caminho in alvos:
        log("=" * 60)
        log("EDITAL: " + str(caminho))
        codigo |= processar_edital(caminho, indice, args)
    return codigo


def resolver_editais(args) -> list[Path]:
    """Decide quais edital.json serao processados (--id, --todos ou -e)."""
    if args.edital:
        return [Path(args.edital)]
    if args.id:
        return [Path("editais") / args.id / "edital.json"]
    encontrados = sorted(Path("editais").glob("*/edital.json"))
    if args.todos:
        return encontrados
    # sem flag nenhuma: so segue se houver exatamente um edital
    if len(encontrados) == 1:
        return encontrados
    if len(encontrados) > 1:
        print("Ha {0} editais. Use --id <nome> ou --todos:".format(len(encontrados)),
              file=sys.stderr)
        for caminho in encontrados:
            print("  - " + caminho.parent.name, file=sys.stderr)
        return []
    legado = Path("data/edital.json")
    return [legado] if legado.is_file() else []


def processar_edital(caminho: Path, indice: list[dict], args) -> int:
    edital = carregar_json(caminho)

    log("{0} disciplinas, {1} subtopicos".format(
        len(edital["disciplinas"]),
        edital.get("estatisticas", {}).get("subtopicos", "?"),
    ))

    # Modo sem API: aplica um JSON de decisoes ja pronto
    if args.importar:
        return importar_decisoes(edital, caminho, Path(args.importar), args)

    lotes = montar_lotes(
        edital, indice,
        tamanho_lote=args.lote, topk=args.topk,
        max_notas_lote=args.max_notas_lote, refazer=args.refazer,
    )
    if args.limite_lotes > 0:
        lotes = lotes[: args.limite_lotes]

    total_sub = sum(len(l["subtopicos"]) for l in lotes)
    log("{0} lotes montados | {1} subtopicos a avaliar".format(len(lotes), total_sub))

    if args.preview:
        for i, lote in enumerate(lotes[:3], 1):
            print("\n--- lote {0}/{1} | {2} ---".format(i, len(lotes), lote["disciplina"]))
            print("  subtopicos: " + str(len(lote["subtopicos"]))
                  + " | notas candidatas: " + str(len(lote["notas"])))
            for item in lote["subtopicos"][:4]:
                print("    . " + item["texto"][:64])
            for nota in lote["notas"][:5]:
                print("    > " + nota["id"][:88])

    if not lotes:
        log("nada a fazer (use --refazer para reavaliar o que ja esta concluido)")
        return 0

    if args.dry_run:
        chars = sum(
            len(render_catalogo(l["notas"])) + len(render_subtopicos(
                l["disciplina"], l["subtopicos"])) for l in lotes
        )
        log("DRY-RUN: nenhuma chamada feita. ~{0} mil chars de prompt "
            "(~{1} mil tokens sem contar o cache)".format(
                round(chars / 1000), round(chars / 3800)))
        return 0

    if anthropic is None:
        print("ERRO: o pacote 'anthropic' nao esta instalado.\n"
              "      Rode:  pip install anthropic", file=sys.stderr)
        return 1
    if not (os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        log("aviso: ANTHROPIC_API_KEY nao esta no ambiente; "
            "o SDK tentara o perfil do 'ant auth login'.")

    client = anthropic.Anthropic(max_retries=3)
    cache = Cache(Path(".cache_matcher"), ativo=not args.sem_cache)

    uso = {"entrada": 0, "saida": 0, "cache_escrita": 0,
           "cache_leitura": 0, "chamadas": 0}
    estat = defaultdict(int)
    acertos_cache = 0

    for i, lote in enumerate(lotes, 1):
        catalogo = render_catalogo(lote["notas"])
        pergunta = render_subtopicos(lote["disciplina"], lote["subtopicos"])
        chave = Cache.chave(args.modelo, catalogo, pergunta)

        dados = cache.ler(chave)
        origem = "cache"
        if dados is None:
            origem = "api"
            try:
                dados = chamar_modelo(
                    client, args.modelo, args.esforco, catalogo, pergunta,
                    args.max_tentativas, uso,
                )
            except Exception as erro:                      # noqa: BLE001
                log("  X lote {0} falhou: {1}".format(i, erro))
                estat["lotes_falhos"] += 1
                continue
            cache.gravar(chave, dados)
        else:
            acertos_cache += 1

        resultados = dados.get("resultados", [])
        aplicar(lote, resultados, args.min_confianca, estat)

        log("lote {0}/{1} [{2}] {3} | {4} subtopicos, {5} notas | "
            "cobertos ate agora: {6}".format(
                i, len(lotes), origem, lote["disciplina"][:28],
                len(lote["subtopicos"]), len(lote["notas"]), estat["cobertos"]))

        if args.pausa > 0 and i < len(lotes):
            time.sleep(args.pausa)

    recalcular(edital)
    edital["match"] = {
        "modelo": args.modelo,
        "esforco": args.esforco,
        "min_confianca": args.min_confianca,
        "executado_em": datetime.now().isoformat(timespec="seconds"),
    }

    saida = Path(args.output) if args.output else caminho
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(edital, ensure_ascii=False, indent=2),
                     encoding="utf-8")

    est = edital["estatisticas"]
    log("-" * 60)
    log("RESULTADO: {0}/{1} subtopicos cobertos ({2}%)".format(
        est["subtopicos_concluidos"], est["subtopicos"], est["percentual"]))
    log("cobertos nesta rodada: {0} | nao cobertos: {1}".format(
        estat["cobertos"], estat["nao_cobertos"]))
    if estat["sem_nota_valida"]:
        log("rejeitados por citar nota inexistente: " + str(estat["sem_nota_valida"]))
    if estat["sem_resposta"]:
        log("subtopicos sem resposta do modelo: " + str(estat["sem_resposta"]))
    if estat["lotes_falhos"]:
        log("lotes que falharam: " + str(estat["lotes_falhos"]))
    log("chamadas a API: {0} | acertos de cache local: {1}".format(
        uso["chamadas"], acertos_cache))
    log("tokens  entrada: {0} | saida: {1} | cache escrito: {2} | cache lido: {3}"
        .format(uso["entrada"], uso["saida"], uso["cache_escrita"],
                uso["cache_leitura"]))

    if args.modelo in PRECOS:
        p_in, p_out = PRECOS[args.modelo]
        custo = (
            uso["entrada"] * p_in
            + uso["cache_escrita"] * p_in * 1.25
            + uso["cache_leitura"] * p_in * 0.05
            + uso["saida"] * p_out
        ) / 1_000_000
        log("custo estimado desta rodada: US$ {0:.4f}".format(custo))

    log("-> " + str(saida))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
