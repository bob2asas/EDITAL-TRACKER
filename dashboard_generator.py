# -*- coding: utf-8 -*-
"""
dashboard_generator.py - Passo 4 do pipeline Edital Tracker.

Gera um index.html unico que atende VARIOS editais: um seletor no topo troca de
concurso sem recarregar nada. Tambem traz a aba "Acervo", que lista o que existe
no vault independentemente de edital.

Uso:
    python dashboard_generator.py --abrir
    python dashboard_generator.py --editais editais --inventario data/inventario.json

Le editais/<id>/edital.json (todos) e data/inventario.json.
Escreve apenas o arquivo de saida. Nunca toca no vault.
"""

from __future__ import annotations

import argparse
import base64
import re
import json
import sys
import unicodedata
import webbrowser
from datetime import datetime
from pathlib import Path

FAIXAS = [
    (85.0, "Dominado", "A"),
    (60.0, "Avancado", "B"),
    (35.0, "Em curso", "C"),
    (0.0, "Inicial", "D"),
]


def faixa(pct: float) -> tuple[str, str]:
    for limite, rotulo, chave in FAIXAS:
        if pct >= limite:
            return rotulo, chave
    return "Inicial", "D"


# ---------------------------------------------------------------------------
# Leitura e calculo
# ---------------------------------------------------------------------------


def calcular_edital(edital: dict, identificador: str) -> dict:
    disciplinas = []
    total = 0
    feitos = 0
    parciais = 0

    for disc in edital.get("disciplinas", []):
        d_total = 0
        d_feitos = 0
        topicos = []
        for topico in disc.get("topicos", []):
            subs = topico.get("subtopicos", [])
            t_feitos = sum(1 for s in subs if s.get("concluido"))
            d_total += len(subs)
            d_feitos += t_feitos
            topicos.append({
                "numero": topico.get("numero", ""),
                "titulo": topico.get("titulo", ""),
                "feitos": t_feitos,
                "total": len(subs),
                "subtopicos": [
                    {
                        "id": s.get("id", ""),
                        "texto": s.get("texto", ""),
                        "ok": bool(s.get("concluido")),
                        # parcial = ha base no vault, mas nao cobre o que o
                        # edital pede. E a fila de estudo mais rentavel.
                        "parcial": (
                            not s.get("concluido")
                            and float(s.get("confianca", 0) or 0) >= 0.5
                        ),
                        "conf": s.get("confianca", 0),
                        "notas": s.get("notas", [])[:3],
                        "just": s.get("justificativa", ""),
                    }
                    for s in subs
                ],
            })

        d_parciais = sum(
            1 for t in topicos for sub in t["subtopicos"] if sub["parcial"]
        )
        total += d_total
        feitos += d_feitos
        parciais += d_parciais
        pct = round(100.0 * d_feitos / d_total, 1) if d_total else 0.0
        rotulo, chave = faixa(pct)
        disciplinas.append({
            "nome": disc.get("nome", "(sem nome)"),
            "feitos": d_feitos, "total": d_total,
            "pendentes": d_total - d_feitos, "pct": pct,
            "parciais": d_parciais,
            "faixa": rotulo, "chave": chave, "topicos": topicos,
        })

    return {
        "id": edital.get("id", identificador),
        "concurso": edital.get("concurso", identificador),
        "cargo": edital.get("cargo", ""),
        "banca": edital.get("banca", ""),
        "total": total, "feitos": feitos, "pendentes": total - feitos,
        "parciais": parciais,
        "pct": round(100.0 * feitos / total, 1) if total else 0.0,
        "disciplinas": disciplinas,
        "match": edital.get("match", {}),
    }


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


LOGOS = {
    "municipio": ["logo-municipio", "logo", "brasao", "municipio"],
    "banca": ["logo-banca", "banca"],
}
EXTENSOES = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]
TIPOS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}


def reduzir(caminho: Path, ext: str, altura_max: int = 104) -> bytes:
    """
    Reduz o logo antes de embutir. Ele aparece com 52px de altura na tela; sem
    isso, quatro editais com dois logos cada deixavam o index.html com quase
    1 MB. Se o Pillow nao estiver instalado, embute o arquivo como esta.
    """
    if ext == ".svg":
        return caminho.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        return caminho.read_bytes()
    try:
        import io as _io
        with Image.open(caminho) as img:
            if img.height <= altura_max:
                return caminho.read_bytes()
            largura = round(img.width * altura_max / img.height)
            menor = img.convert("RGB").resize((largura, altura_max),
                                              Image.LANCZOS)
            # paleta de 128 cores: logo nao precisa de cor real e o arquivo
            # embutido cai para uma fracao do tamanho
            menor = menor.quantize(colors=128, method=Image.MEDIANCUT)
            buf = _io.BytesIO()
            menor.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:                                   # noqa: BLE001
        return caminho.read_bytes()


def carregar_logos(pasta_edital: Path, limite_kb: int = 400) -> dict:
    """
    Procura o brasao do municipio e o logo da banca na pasta do edital e
    devolve cada um como data URI - assim o index.html continua sendo um
    arquivo unico que abre offline.

    Convencao, valida para qualquer edital futuro:
        editais/<id>/logo-municipio.png
        editais/<id>/logo-banca.png
    """
    achados = {}
    for papel, nomes in LOGOS.items():
        for nome in nomes:
            for ext in EXTENSOES:
                caminho = pasta_edital / (nome + ext)
                if not caminho.is_file():
                    continue
                tamanho = caminho.stat().st_size
                if tamanho > limite_kb * 1024:
                    print("  ! " + caminho.name + " tem "
                          + str(round(tamanho / 1024)) + " KB e foi ignorado "
                          "(limite " + str(limite_kb) + " KB)", file=sys.stderr)
                    continue
                bytes_img = reduzir(caminho, ext)
                dados = base64.b64encode(bytes_img).decode("ascii")
                achados[papel] = ("data:" + TIPOS.get(ext, "image/png")
                                  + ";base64," + dados)
                break
            if papel in achados:
                break
    return achados


def carregar_json_opcional(caminho: Path, padrao):
    """Le um arquivo auxiliar do edital (info.json, ocultas.json) se existir."""
    if not caminho.is_file():
        return padrao
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as erro:
        print("  ! " + caminho.name + " ignorado (" + str(erro) + ")",
              file=sys.stderr)
        return padrao
    if isinstance(dados, dict):
        return {k: v for k, v in dados.items() if not str(k).startswith("_")}
    return dados


def aplicar_pesos(calculado: dict, info: dict) -> None:
    """
    Converte o quadro de pontuacao do edital em um peso por disciplina.

    Varias disciplinas costumam cair sob o mesmo componente da prova
    ("Conhecimentos Especificos" = 60 pontos para quatro disciplinas). Nesse
    caso os pontos sao repartidos na proporcao do numero de subtopicos, que e
    a melhor aproximacao disponivel: o edital nao diz quantas questoes cada
    disciplina recebe.
    """
    pontuacao = {k: v for k, v in (info.get("pontuacao") or {}).items()
                 if isinstance(v, dict)}
    mapa = {k: v for k, v in (info.get("mapa_pontuacao") or {}).items()
            if isinstance(v, str) and not k.startswith("_")}
    if not pontuacao or not mapa:
        return

    # a discursiva nao e uma disciplina: seus pontos reforcam um componente
    extra: dict[str, float] = {}
    alvo = info.get("discursiva_sobre")
    for nome, dados in pontuacao.items():
        if "dissertativ" in nome.lower() or "discursiv" in nome.lower():
            if alvo:
                extra[alvo] = extra.get(alvo, 0) + float(dados.get("maximo", 0))

    # subtopicos por componente, para repartir os pontos
    sub_por_componente: dict[str, int] = {}
    for disc in calculado["disciplinas"]:
        comp = mapa.get(disc["nome"])
        if comp:
            sub_por_componente[comp] = sub_por_componente.get(comp, 0) + disc["total"]

    total_pontos = 0.0
    for disc in calculado["disciplinas"]:
        comp = mapa.get(disc["nome"])
        if not comp or comp not in pontuacao:
            disc["pontos"] = 0.0
            continue
        pontos_comp = float(pontuacao[comp].get("maximo", 0)) + extra.get(comp, 0)
        denominador = sub_por_componente.get(comp, 0)
        fatia = (disc["total"] / denominador) if denominador else 0
        disc["pontos"] = round(pontos_comp * fatia, 2)
        disc["componente"] = comp
        total_pontos += disc["pontos"]

    calculado["pontos_total"] = round(total_pontos, 2)
    calculado["tem_pesos"] = total_pontos > 0


def carregar_manual(pasta_edital: Path) -> dict:
    """
    Marcacoes feitas a mao no dashboard, salvas em editais/<id>/manual.json.
    Elas vencem o resultado do match: quem sabe o que estudou e o candidato.
    """
    arquivo = pasta_edital / "manual.json"
    if not arquivo.is_file():
        return {}
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as erro:
        print("  ! manual.json ignorado (" + str(erro) + ")", file=sys.stderr)
        return {}
    return {k: bool(v) for k, v in dados.items() if not k.startswith("_")}


def chave_marca(sub: dict) -> str:
    """Mesma regra do painel: assunto especifico vale em todos os editais;
    texto curto ou generico fica preso ao subtopico onde foi marcado."""
    texto = sem_acento(sub.get("texto", "")).lower()
    texto = re.sub(r"\s+", " ", texto).strip(" .;:")
    if len(texto) < 20 or len(texto.split()) < 3:
        return "i:" + sub.get("id", "")
    return "t:" + texto


def aplicar_marcas(bruto: dict, marcas: dict, manual: dict) -> None:
    """Marcacoes feitas a mao vencem o resultado do match."""
    if not marcas and not manual:
        return
    for disc in bruto.get("disciplinas", []):
        for topico in disc.get("topicos", []):
            for sub in topico.get("subtopicos", []):
                if sub.get("id") in manual:
                    sub["concluido"] = bool(manual[sub["id"]])
                chave = chave_marca(sub)
                if chave in marcas:
                    sub["concluido"] = bool(marcas[chave])


def carregar_editais(pasta: Path, marcas: dict | None = None) -> list[dict]:
    editais = []
    for caminho in sorted(pasta.glob("*/edital.json")):
        try:
            bruto = json.loads(caminho.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as erro:
            print("  ! ignorando " + str(caminho) + ": " + str(erro),
                  file=sys.stderr)
            continue
        manual_edital = carregar_manual(caminho.parent)
        aplicar_marcas(bruto, marcas or {}, manual_edital)
        calculado = calcular_edital(bruto, caminho.parent.name)
        calculado["manual"] = carregar_manual(caminho.parent)
        calculado["info"] = carregar_json_opcional(caminho.parent / "info.json", {})
        calculado["ocultas"] = carregar_json_opcional(
            caminho.parent / "ocultas.json", [])
        calculado["logos"] = carregar_logos(caminho.parent)
        aplicar_pesos(calculado, calculado["info"])
        editais.append(calculado)

    legado = pasta.parent / "data" / "edital.json"
    if not editais and legado.is_file():
        editais.append(calcular_edital(
            json.loads(legado.read_text(encoding="utf-8")), "edital"))
    return editais


def montar_acervo(inventario: dict) -> dict:
    """Agrupa as notas do vault por materia - serve a qualquer edital futuro."""
    materias: dict[str, list[dict]] = {}
    for nota in inventario.get("notas", []):
        chave = nota.get("materia") or nota.get("area") or "(outros)"
        materias.setdefault(chave, []).append({
            "titulo": nota.get("titulo", ""),
            "caminho": nota.get("caminho", ""),
            "aula": nota.get("aula", ""),
            "tags": nota.get("tags", [])[:6],
            "secoes": [
                h["texto"] for h in nota.get("headings", [])
                if h.get("nivel") == 3
            ][:12],
            "palavras": nota.get("palavras", 0),
        })
    ordenadas = sorted(materias.items(), key=lambda x: (-len(x[1]), x[0]))
    return {
        "materias": [
            {"nome": nome, "notas": sorted(notas, key=lambda n: n["caminho"])}
            for nome, notas in ordenadas
        ],
        "total_notas": sum(len(v) for v in materias.values()),
        "vault": inventario.get("vault", ""),
        "gerado_em": inventario.get("gerado_em", ""),
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{
  color-scheme: light;
  --plano:#f9f9f7; --superficie:#fcfcfb; --tinta:#0b0b0b; --tinta2:#52514e;
  --mudo:#898781; --linha:#e1e0d9; --borda:rgba(11,11,11,.10);
  --serie:#2a78d6; --trilho:#e1e0d9;
  --bom:#0ca30c; --aviso:#fab219; --serio:#ec835a; --critico:#d03b3b;
  /* semaforo das faixas: A dominado, B avancado, C em curso, D inicial */
  --faixa-A:#0ca30c; --faixa-B:#fab219; --faixa-C:#ec835a; --faixa-D:#d03b3b;
  /* versao para TEXTO: amarelo e laranja nao tem contraste em fundo claro */
  --tinta-A:#0a7a0a; --tinta-B:#8a6000; --tinta-C:#a34a22; --tinta-D:#b02a2a;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --plano:#0d0d0d; --superficie:#1a1a19; --tinta:#f0efec; --tinta2:#c3c2b7;
    --mudo:#898781; --linha:#2c2c2a; --borda:rgba(255,255,255,.10);
    --serie:#3987e5; --trilho:#2c2c2a;
    --faixa-A:#0ca30c; --faixa-B:#fab219; --faixa-C:#ec835a; --faixa-D:#e66767;
    --tinta-A:#0ca30c; --tinta-B:#fab219; --tinta-C:#ec835a; --tinta-D:#e66767;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --plano:#0d0d0d; --superficie:#1a1a19; --tinta:#f0efec; --tinta2:#c3c2b7;
  --mudo:#898781; --linha:#2c2c2a; --borda:rgba(255,255,255,.10);
  --serie:#3987e5; --trilho:#2c2c2a;
  --faixa-A:#0ca30c; --faixa-B:#fab219; --faixa-C:#ec835a; --faixa-D:#e66767;
  --tinta-A:#0ca30c; --tinta-B:#fab219; --tinta-C:#ec835a; --tinta-D:#e66767;
}
*{box-sizing:border-box}
body{margin:0; background:var(--plano); color:var(--tinta);
  font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px; margin:0 auto; padding:28px 16px 72px}
header{display:flex; justify-content:space-between; align-items:center; gap:14px;
  margin-bottom:18px; flex-wrap:wrap}
.marca{font-size:13px; font-weight:650; letter-spacing:.02em; margin:0}
.marca span{color:var(--mudo); font-weight:400}
.btn{background:var(--superficie); color:var(--tinta2); cursor:pointer;
  border:1px solid var(--borda); border-radius:8px; padding:7px 12px;
  font-size:13px; font-family:inherit}
.btn:hover{color:var(--tinta)}
.btn[aria-selected="true"]{color:var(--tinta); border-color:var(--tinta2)}
.abas{display:flex; gap:8px}

select,input[type=search]{background:var(--superficie); color:var(--tinta);
  border:1px solid var(--borda); border-radius:8px; padding:9px 12px;
  font-size:13.5px; font-family:inherit}

/* navegacao entre editais */
#nav-editais{display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap;
  align-items:center}
.chip{flex:1 1 190px; min-width:170px; max-width:300px; cursor:pointer;
  background:var(--superficie); border:1px solid var(--borda);
  border-radius:10px; padding:10px 13px; font-family:inherit; text-align:left;
  color:var(--tinta2); display:block}
.chip:hover{border-color:var(--tinta2)}
.chip.ativo{border-color:var(--serie); color:var(--tinta)}
.chip-nome{display:block; font-size:12.5px; font-weight:600;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.chip-pct{display:block; font-size:17px; font-weight:650; margin:2px 0 6px;
  font-variant-numeric:tabular-nums}
.chip-barra{display:block; height:4px; background:var(--trilho);
  border-radius:2px; overflow:hidden}
.chip-barra i{display:block; height:100%; background:var(--serie);
  border-radius:2px}
.chip.ativo .chip-pct{color:var(--serie)}

/* concurso ja realizado: vermelho translucido por cima do cartao inteiro */
.chip.passado{position:relative; border-color:rgba(208,59,59,.40)}
.chip.passado::after{content:""; position:absolute; inset:0; border-radius:10px;
  background:rgba(208,59,59,.13); pointer-events:none}
.chip.passado .chip-nome{color:var(--mudo)}
.chip.passado .chip-logo{filter:grayscale(.7); opacity:.75}
.chip.passado.ativo{border-color:rgba(208,59,59,.75)}
.selo-fim{display:block; margin-top:6px; font-size:10px; letter-spacing:.07em;
  text-transform:uppercase; color:var(--tinta-D)}
.hero.passado{position:relative; border-color:rgba(208,59,59,.35)}
.hero.passado::after{content:""; position:absolute; inset:0; border-radius:16px;
  background:rgba(208,59,59,.09); pointer-events:none}
.pill.encerrado{border-color:var(--faixa-D); color:var(--tinta-D)}
.pill.encerrado b{color:var(--tinta-D)}
.nav-unico{font-size:13px; font-weight:600; color:var(--tinta)}
.nav-dica{font-size:12px; color:var(--mudo)}

.acoes{display:flex; gap:8px; align-items:center}
.acoes[hidden]{display:none}   /* o atributo hidden perde para display:flex */
.acoes .conta{font-size:12px; color:var(--mudo)}
.ajuda{font-size:12px; color:var(--mudo); margin:0 0 12px}
.ajuda.alerta{color:var(--tinta-D); border:1px solid var(--faixa-D);
  border-radius:8px; padding:9px 12px}
.btn-mini{padding:4px 9px; font-size:12px}
#caixa-export{margin-bottom:12px}
#caixa-export[hidden]{display:none}
#caixa-export textarea{width:100%; background:var(--superficie);
  color:var(--tinta); border:1px solid var(--borda); border-radius:8px;
  padding:10px; font-family:ui-monospace,Consolas,monospace; font-size:11.5px}
.modos{display:flex; gap:7px; align-items:center; margin-bottom:12px}
.modos[hidden]{display:none}
.btn.sel{color:var(--tinta); border-color:var(--serie)}
.modo-dica{font-size:12px; color:var(--mudo)}
.pts{font-size:11px; color:var(--tinta2); background:var(--plano);
  border:1px solid var(--borda); border-radius:999px; padding:1px 8px;
  font-variant-numeric:tabular-nums; white-space:nowrap}

/* informacoes do concurso */
.info{display:flex; gap:7px; flex-wrap:wrap; margin:0 0 12px}
.pill{font-size:12px; color:var(--tinta2); background:var(--plano);
  border:1px solid var(--borda); border-radius:999px; padding:3px 11px}
.pill b{color:var(--tinta); font-weight:650}
.pill.urgente{border-color:var(--critico)}
.pill.urgente b{color:var(--critico)}
.pill-disc{border-color:var(--serie)}
.escopo{font-size:12px; color:var(--tinta2); margin:0 0 12px;
  padding:7px 11px; background:var(--plano); border:1px solid var(--borda);
  border-left:3px solid var(--serio); border-radius:7px}

/* disciplinas fora da conta */
.ocultas{display:flex; gap:7px; align-items:center; flex-wrap:wrap;
  margin-bottom:12px}
.ocultas[hidden]{display:none}
.ocultas-rot{font-size:12px; color:var(--mudo)}
.voltar{font-size:12px; cursor:pointer; font-family:inherit;
  color:var(--tinta2); background:var(--superficie);
  border:1px dashed var(--borda); border-radius:999px; padding:4px 11px}
.voltar:hover{color:var(--serie); border-color:var(--serie)}
.voltar b{color:var(--mudo); font-weight:500}
.ocultar{cursor:pointer; background:none; border:1px solid transparent;
  border-radius:5px; color:var(--mudo); font-size:14px; line-height:1;
  padding:2px 5px; font-family:inherit}
.ocultar:hover{color:var(--critico); border-color:var(--critico)}

.hero{display:flex; gap:28px; align-items:center; flex-wrap:wrap;
  background:var(--superficie); border:1px solid var(--borda);
  border-radius:16px; padding:22px 26px; margin-bottom:18px}
.donut-num{fill:var(--tinta); font-size:42px; font-weight:650;
  letter-spacing:-.02em}
.donut-sub{fill:var(--mudo); font-size:13px}
.hero-txt{flex:1; min-width:220px}
/* brasao do municipio e logo da banca, recortados do proprio edital */
.titulo-linha{display:flex; align-items:center; gap:14px; margin-bottom:12px}
.titulo-linha > div{flex:1; min-width:0}
.brasao{height:52px; width:auto; flex:0 0 auto}
.logo-banca{height:26px; width:auto; flex:0 0 auto; opacity:.9}
.chip-topo{display:flex; align-items:center; gap:7px}
.chip-logo{height:18px; width:auto; flex:0 0 auto}
.hero-tit{font-size:19px; font-weight:650; margin:0 0 2px; letter-spacing:-.01em}
.hero-sub{color:var(--tinta2); font-size:13px; margin:0 0 14px}
.nums{display:flex; gap:26px; flex-wrap:wrap}
.num b{display:block; font-size:24px; font-weight:650; letter-spacing:-.01em}
.num span{font-size:11px; color:var(--mudo); text-transform:uppercase;
  letter-spacing:.06em}

.filtros{display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap}
.filtros input{flex:1; min-width:170px}

.disc{background:var(--superficie); border:1px solid var(--borda);
  border-radius:12px; margin-bottom:9px; overflow:hidden}
summary{list-style:none; cursor:pointer; padding:15px 18px}
summary::-webkit-details-marker{display:none}
.disc-topo{display:flex; justify-content:space-between; align-items:center;
  gap:12px; margin-bottom:9px}
.disc-nome{font-size:14.5px; font-weight:600; margin:0}
.disc-meta{display:flex; align-items:center; gap:10px}
.disc-cont{color:var(--mudo); font-size:12px; font-variant-numeric:tabular-nums}
.disc-pct{font-size:15px; font-weight:650; min-width:52px; text-align:right;
  font-variant-numeric:tabular-nums}
.badge{font-size:11px; padding:2px 8px; border-radius:999px;
  border:1px solid var(--borda); color:var(--tinta2); white-space:nowrap}
.badge::before{margin-right:5px}
.badge-A{color:var(--tinta-A); border-color:var(--faixa-A)}
.badge-A::before{content:"\\25CF"}
.badge-B{color:var(--tinta-B); border-color:var(--faixa-B)}
.badge-B::before{content:"\\25D0"}
.badge-C{color:var(--tinta-C); border-color:var(--faixa-C)}
.badge-C::before{content:"\\25D1"}
.badge-D{color:var(--tinta-D); border-color:var(--faixa-D)}
.badge-D::before{content:"\\25CB"}
.barra{height:10px; background:var(--trilho); border-radius:5px; overflow:hidden}
.preenchida{height:100%; background:var(--serie); border-radius:5px;
  min-width:3px; transition:width .5s cubic-bezier(.2,.8,.2,1)}

.topicos{padding:0 18px 14px; border-top:1px solid var(--linha)}
.topico{padding:11px 0; border-bottom:1px solid var(--linha)}
.topico:last-child{border-bottom:none}
.topico-cab{display:flex; gap:9px; align-items:baseline; margin-bottom:5px}
.topico-num{color:var(--mudo); font-size:12px; min-width:22px;
  font-variant-numeric:tabular-nums}
.topico-tit{font-weight:560; font-size:13.5px; flex:1}
.topico-cont{color:var(--mudo); font-size:12px;
  font-variant-numeric:tabular-nums}
.subs{list-style:none; margin:0; padding:0 0 0 31px}
.sub{display:flex; align-items:baseline; gap:8px; padding:3px 0; font-size:13px;
  color:var(--tinta2); flex-wrap:wrap}
.marca-ok{color:var(--mudo); font-size:12px; width:20px; height:20px;
  flex:0 0 auto; cursor:pointer; background:none; border:1px solid transparent;
  border-radius:5px; padding:0; font-family:inherit; line-height:1}
.marca-ok:hover{border-color:var(--serie); color:var(--serie)}
.marca-ok.fixa{cursor:default; display:inline-block; text-align:center}
.marca-ok.fixa:hover{border-color:transparent; color:inherit}
.sub.ok .marca-ok{color:var(--bom)}
.sub.parcial .marca-ok{color:var(--serio)}
/* marcado a mao: anel para distinguir do resultado automatico */
.sub.manual .marca-ok{border-color:currentColor}
.sub.parcial .sub-txt{color:var(--tinta2)}
.sub.ok .sub-txt{color:var(--tinta)}
.sub-txt{flex:1; min-width:140px}
.conf{font-size:11px; color:var(--mudo); font-variant-numeric:tabular-nums}
.nota{font-size:11px; color:var(--tinta2); text-decoration:none;
  border:1px solid var(--borda); border-radius:5px; padding:1px 6px;
  max-width:230px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
a.nota:hover{color:var(--serie); border-color:var(--serie)}

.vazio{background:var(--superficie); border:1px dashed var(--borda);
  border-radius:12px; padding:34px 26px; text-align:center; color:var(--tinta2)}
.vazio h2{font-size:16px; color:var(--tinta); margin:0 0 8px}
.vazio code{background:var(--plano); border:1px solid var(--borda);
  border-radius:6px; padding:2px 7px; font-size:12.5px; display:inline-block;
  margin-top:4px}
footer{margin-top:26px; color:var(--mudo); font-size:12px; text-align:center}

/* telas de celular: o painel e usado de pe, para consulta rapida */
@media (max-width:560px){
  .wrap{padding:18px 14px 56px}
  .hero{padding:18px 16px; gap:16px; justify-content:center}
  .hero svg{width:100%; max-width:168px; height:auto}
  .donut-num{font-size:46px}
  .titulo-linha{gap:10px}
  .brasao{height:38px}
  .logo-banca{height:19px}
  .hero-tit{font-size:17px}
  .nums{gap:18px 22px}
  .num b{font-size:21px}
  .chip{flex:1 1 100%; max-width:none}
  .filtros{gap:8px}
  .filtros input{min-width:100%}
  .filtros select{flex:1}
  .disc-topo{flex-wrap:wrap; gap:6px}
  .disc-meta{gap:7px}
  .topicos{padding:0 12px 12px}
  .subs{padding-left:20px}
  .nota{max-width:150px}
  .info .pill{font-size:11.5px}
}
.oculto{display:none}
@media print{.filtros,.btn,.abas,#concurso{display:none}}
"""

JS = r"""
var DADOS = __DADOS__;
var VAULT = __VAULT__;
var ATUAL = 0;

/* Aberto como arquivo (file://) = painel completo, com check manual.
   Publicado na web = somente visualizacao: a marcacao pertence ao ciclo
   local (manual.json), e um clique que nao vai a lugar nenhum confunde. */
var LOCAL = location.protocol === "file:";

/* ---- marcacoes manuais -------------------------------------------------
   Guardadas no navegador (localStorage) e mescladas com editais/<id>/manual.json.
   A marcacao manual SEMPRE vence o resultado do match automatico.          */

function chaveLocal(ed){ return "edital-tracker:" + ed.id; }

/* ---- marcacoes compartilhadas entre editais ----------------------------
   Os quatro editais repetem assunto: "Fato gerador, sujeito ativo e passivo"
   aparece em Caruaru e em Queimados. Marcar duas vezes a mesma coisa seria
   trabalho perdido, entao a marca e guardada pelo TEXTO do subtopico e vale
   em todo edital que peca aquilo.

   Excecao: textos curtos ou genericos ("Caracteristicas", "Normas gerais")
   aparecem em disciplinas diferentes querendo dizer coisas diferentes - esses
   ficam presos ao subtopico onde foram marcados.                          */

var CHAVE_MARCAS = "edital-tracker:marcas";

function normalizar(t){
  return String(t || "")
    .normalize("NFD").replace(/[̀-ͯ]/g, "")
    .toLowerCase().replace(/\s+/g, " ").replace(/^[\s.;:]+|[\s.;:]+$/g, "");
}

function chaveMarca(sub){
  var t = normalizar(sub.texto);
  var generico = t.length < 20 || t.split(" ").length < 3;
  return generico ? ("i:" + sub.id) : ("t:" + t);
}

function lerMarcas(){
  try {
    var bruto = localStorage.getItem(CHAVE_MARCAS);
    if (bruto !== null) return JSON.parse(bruto);
  } catch (e) {}
  return MEM[CHAVE_MARCAS] || {};
}

function gravarMarcas(mapa){
  MEM[CHAVE_MARCAS] = mapa;
  try {
    localStorage.setItem(CHAVE_MARCAS, JSON.stringify(mapa));
    return true;
  } catch (e) { return false; }
}
function chaveOcultas(ed){ return "edital-tracker:ocultas:" + ed.id; }

/* ---- disciplinas ocultas ------------------------------------------------
   Ocultar tira a disciplina da conta: o percentual passa a valer sobre o que
   sobrou. Nada e perdido - a disciplina volta com um clique.              */

function lerOcultas(ed){
  var chave = chaveOcultas(ed);
  try {
    var bruto = localStorage.getItem(chave);
    if (bruto !== null) return JSON.parse(bruto);
  } catch (e) {}
  return MEM[chave] || (ed.ocultas || []).slice();
}
function gravarOcultas(ed, lista){
  var chave = chaveOcultas(ed);
  MEM[chave] = lista;
  try { localStorage.setItem(chave, JSON.stringify(lista)); } catch (e) {}
}
function estaOculta(ed, nome){ return lerOcultas(ed).indexOf(nome) !== -1; }

function alternarDisciplina(ed, nome){
  var lista = lerOcultas(ed);
  var i = lista.indexOf(nome);
  if (i === -1) lista.push(nome); else lista.splice(i, 1);
  gravarOcultas(ed, lista);
}

/* Fallback em memoria: em navegacao privada, ou quando a pagina roda numa
   origem sem storage, o clique precisa funcionar do mesmo jeito - so nao
   sobrevive ao recarregar. */
var MEM = {};


/* mapa efetivo do edital: manual.json do projeto + marcas deste navegador */
function manualDe(ed){
  var base = {}, k;
  for (k in (ed.manual || {})) base[k] = ed.manual[k];   // manual.json do edital

  // marcas.json do projeto + o que foi marcado neste navegador (este vence)
  var marcas = {};
  for (k in (DADOS.marcas || {})) marcas[k] = DADOS.marcas[k];
  var locais = lerMarcas();
  for (k in locais) marcas[k] = locais[k];
  ed.disciplinas.forEach(function(d){
    d.topicos.forEach(function(t){
      t.subtopicos.forEach(function(s){
        var ch = chaveMarca(s);
        if (marcas.hasOwnProperty(ch)) base[s.id] = marcas[ch];
      });
    });
  });
  return base;
}

function estadoDe(sub, manual){
  if (manual.hasOwnProperty(sub.id)) {
    return { ok: !!manual[sub.id], parcial: false, manual: true };
  }
  return { ok: sub.ok, parcial: sub.parcial, manual: false };
}

/* tres estados: automatico -> marcado -> desmarcado -> automatico */
function alternar(ed, sub){
  var manual = manualDe(ed);
  var atual = estadoDe(sub, manual);
  var marcas = lerMarcas();
  var ch = chaveMarca(sub);

  if (atual.manual && atual.ok) {
    marcas[ch] = false;
  } else if (atual.manual && !atual.ok) {
    delete marcas[ch];
  } else {
    marcas[ch] = !sub.ok;
  }
  if (!gravarMarcas(marcas)) avisarSemStorage();
}

/* quantos editais, alem deste, pedem o mesmo subtopico */
function tambemEm(sub, idAtual){
  if (chaveMarca(sub).slice(0, 2) !== "t:") return 0;
  var alvo = normalizar(sub.texto), n = 0;
  DADOS.editais.forEach(function(ed){
    if (ed.id === idAtual) return;
    var achou = ed.disciplinas.some(function(d){
      return d.topicos.some(function(t){
        return t.subtopicos.some(function(s){
          return normalizar(s.texto) === alvo;
        });
      });
    });
    if (achou) n++;
  });
  return n;
}

var avisou = false;
function avisarSemStorage(){
  if (avisou) return;
  avisou = true;
  var cx = document.getElementById("caixa-export");
  cx.hidden = false;
  cx.innerHTML = '<p class="ajuda alerta">Este navegador nao esta guardando as '
    + 'marcacoes (janela anonima ou armazenamento bloqueado). Elas valem '
    + 'enquanto a pagina estiver aberta; use <b>Salvar marcacoes</b> antes de '
    + 'fechar.</p>';
}

/* totais recalculados a cada render, ja com as marcacoes manuais */
function totais(ed){
  var manual = manualDe(ed), ocultas = lerOcultas(ed);
  var t = 0, f = 0, p = 0, tOculto = 0, fOculto = 0;

  ed.disciplinas.forEach(function(d){
    d._oculta = ocultas.indexOf(d.nome) !== -1;
    d._feitos = 0; d._parciais = 0; d._total = 0;
    d.topicos.forEach(function(tp){
      tp._feitos = 0;
      tp.subtopicos.forEach(function(s){
        var e = estadoDe(s, manual);
        d._total++;
        if (e.ok) { d._feitos++; tp._feitos++; }
        else if (e.parcial) { d._parciais++; }
      });
    });
    d._pct = d._total ? Math.round(1000 * d._feitos / d._total) / 10 : 0;

    // uma disciplina oculta sai da conta do percentual geral
    if (d._oculta) { tOculto += d._total; fOculto += d._feitos; }
    else { t += d._total; f += d._feitos; p += d._parciais; }
  });

  // --- peso em pontos da prova -------------------------------------------
  // Cada disciplina vale o que vale na nota, nao o tamanho da sua lista de
  // subtopicos: 48 itens de Informatica valem 5 pontos; 41 de Tributario
  // valem dezenas. O percentual por pontos responde "quanto da NOTA eu ja
  // cobri", que e a pergunta que decide a aprovacao.
  var pts = 0, ptsFeitos = 0, ptsOcultos = 0;
  ed.disciplinas.forEach(function(d){
    var peso = d.pontos || 0;
    d._ptsFeitos = peso * (d._total ? d._feitos / d._total : 0);
    d._ptsPendentes = peso - d._ptsFeitos;
    if (d._oculta) { ptsOcultos += peso; }
    else { pts += peso; ptsFeitos += d._ptsFeitos; }
  });

  return {
    total: t, feitos: f, parciais: p, pendentes: t - f,
    pct: t ? Math.round(1000 * f / t) / 10 : 0,
    manuais: Object.keys(manual).length,
    ocultas: ocultas.length,
    totalOculto: tOculto, feitosOcultos: fOculto,
    totalGeral: t + tOculto,
    pctGeral: (t + tOculto)
      ? Math.round(1000 * (f + fOculto) / (t + tOculto)) / 10 : 0,
    pontos: Math.round(pts * 10) / 10,
    pontosFeitos: Math.round(ptsFeitos * 10) / 10,
    pontosOcultos: Math.round(ptsOcultos * 10) / 10,
    pctPontos: pts ? Math.round(1000 * ptsFeitos / pts) / 10 : 0
  };
}

/* modo de leitura do percentual: por subtopicos ou por pontos da prova */
function modo(){
  try { return localStorage.getItem("edital-tracker:modo") || "pontos"; }
  catch (e) { return MEM.modo || "pontos"; }
}
function gravarModo(m){
  MEM.modo = m;
  try { localStorage.setItem("edital-tracker:modo", m); } catch (e) {}
}
function porPontos(ed){ return modo() === "pontos" && ed.tem_pesos; }

/* ---- concursos ja realizados -------------------------------------------
   Vira encerrado sozinho quando a data da prova passa: nada a marcar a mao.
   Sem data informada, o edital continua tratado como em aberto.           */
function diasAteProva(ed){
  var d = (ed.info || {}).data_prova;
  if (!d) return null;
  var prova = new Date(d + "T00:00:00");
  if (isNaN(prova)) return null;
  var hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  return Math.round((prova - hoje) / 86400000);
}
function jaPassou(ed){
  var dias = diasAteProva(ed);
  return dias !== null && dias < 0;
}
function dataProvaBR(ed){
  var d = (ed.info || {}).data_prova;
  return d ? new Date(d + "T00:00:00").toLocaleDateString("pt-BR") : "";
}

function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"]/g, function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c];
  });
}
/* A cor acompanha o VALOR (semaforo), nunca sozinha: o badge com texto e o
   numero em % vao sempre junto, para quem nao distingue as cores. */
function faixa(p){
  if (p >= 85) return ["Dominado","A"];
  if (p >= 60) return ["Avancado","B"];
  if (p >= 35) return ["Em curso","C"];
  return ["Inicial","D"];
}
function corDaFaixa(p){ return "var(--faixa-" + faixa(p)[1] + ")"; }
function linkNota(caminho){
  if (!VAULT) return "";
  var f = caminho.replace(/\.md$/, "");
  return "obsidian://open?vault=" + encodeURIComponent(VAULT) +
         "&file=" + encodeURIComponent(f);
}
function donut(pct, rotulo){
  rotulo = rotulo || "do edital";
  var r = 86, t = 16, c = r + t, lado = c * 2;
  var circ = 2 * Math.PI * r, feito = circ * Math.min(Math.max(pct,0),100) / 100;
  return '<svg viewBox="0 0 '+lado+' '+lado+'" width="'+lado+'" height="'+lado+'"'
    + ' role="img" aria-label="Progresso: '+pct+'%">'
    + '<circle cx="'+c+'" cy="'+c+'" r="'+r+'" fill="none" stroke="var(--trilho)" stroke-width="'+t+'"/>'
    + '<circle cx="'+c+'" cy="'+c+'" r="'+r+'" fill="none" stroke="'
    + corDaFaixa(pct) + '"'
    + ' stroke-width="'+t+'" stroke-linecap="round" stroke-dasharray="'
    + feito.toFixed(2)+' '+Math.max(circ-feito,0.01).toFixed(2)+'"'
    + ' transform="rotate(-90 '+c+' '+c+')"/>'
    + '<text x="'+c+'" y="'+c+'" class="donut-num" text-anchor="middle"'
    + ' dominant-baseline="central">'+pct+'%</text>'
    + '<text x="'+c+'" y="'+(c+34)+'" class="donut-sub" text-anchor="middle">'
    + rotulo + '</text>'
    + '</svg>';
}

function renderSubs(subs, manual){
  return subs.map(function(s){
    var est = estadoDe(s, manual);
    var chips = (s.notas || []).map(function(cam){
      var nome = esc(cam.split("/").pop().replace(/\.md$/, ""));
      var url = linkNota(cam);
      return url ? '<a class="nota" href="'+esc(url)+'" title="'+esc(cam)+'">'+nome+'</a>'
                 : '<span class="nota">'+nome+'</span>';
    }).join("");
    var conf = (!est.manual && (est.ok || est.parcial) && s.conf)
      ? '<span class="conf">'+Math.round(s.conf*100)+'%</span>' : "";
    var marca = est.ok ? "&#10003;" : (est.parcial ? "&#9681;" : "&#9675;");
    var classe = "sub" + (est.ok ? " ok" : (est.parcial ? " parcial" : ""))
               + (est.manual ? " manual" : "");
    var outros = LOCAL ? tambemEm(s, DADOS.editais[ATUAL].id) : 0;
    var dica = est.manual
      ? (est.ok ? "Marcado por voce como estudado" : "Marcado por voce como nao estudado")
      : (s.just || "");
    if (outros) {
      dica += (dica ? " | " : "") + "Vale tambem em +" + outros + " edital"
            + (outros > 1 ? "is" : "");
    }
    var alvo = LOCAL
      ? '<button class="marca-ok" type="button" data-sub="'+esc(s.id)+'"'
        + ' title="Clique para marcar/desmarcar" aria-label="Alternar '
        + esc(s.texto).slice(0,60)+'">'+marca+'</button>'
      : '<span class="marca-ok fixa" aria-hidden="true">'+marca+'</span>';
    return '<li class="'+classe+'">' + alvo
      + '<span class="sub-txt"'+(dica?' title="'+esc(dica)+'"':"")+'>'+esc(s.texto)+'</span>'
      + conf + chips + '</li>';
  }).join("");
}

function renderDisciplinas(ed, manual){
  var termo = document.getElementById("busca").value.trim().toLowerCase();
  var modo = document.getElementById("ordem").value;
  var abertas = {};
  document.querySelectorAll("#painel-edital details").forEach(function(el){
    if (el.open) abertas[el.dataset.disc] = true;   // nao fecha ao remarcar
  });

  var lista = ed.disciplinas.filter(function(d){
    if (d._oculta) return false;
    return !termo || d.nome.toLowerCase().indexOf(termo) !== -1;
  }).slice();

  if (modo === "pct")    lista.sort(function(a,b){ return b._pct - a._pct; });
  if (modo === "pctasc") lista.sort(function(a,b){ return a._pct - b._pct; });
  if (modo === "pend")   lista.sort(function(a,b){
    return (b._total-b._feitos) - (a._total-a._feitos); });
  // quanta NOTA ainda esta em aberto em cada disciplina
  if (modo === "impacto") lista.sort(function(a,b){
    return (b._ptsPendentes||0) - (a._ptsPendentes||0); });

  if (!lista.length) return '<div class="vazio">Nenhuma disciplina encontrada.</div>';

  return lista.map(function(d){
    var f = faixa(d._pct);
    var topicos = d.topicos.map(function(t){
      return '<div class="topico"><div class="topico-cab">'
        + '<span class="topico-num">'+esc(t.numero)+'</span>'
        + '<span class="topico-tit">'+esc(t.titulo)+'</span>'
        + '<span class="topico-cont">'+t._feitos+'/'+t.total+'</span></div>'
        + '<ul class="subs">'+renderSubs(t.subtopicos, manual)+'</ul></div>';
    }).join("");

    return '<article class="disc"><details data-disc="'+esc(d.nome)+'"'
      + (abertas[d.nome] ? " open" : "") + '><summary>'
      + '<div class="disc-topo"><h3 class="disc-nome">'+esc(d.nome)+'</h3>'
      + '<div class="disc-meta">'
      + '<button class="ocultar" type="button" data-ocultar="'+esc(d.nome)+'"'
      + ' title="Tirar esta disciplina da conta"'
      + ' aria-label="Ocultar '+esc(d.nome)+'">&#8856;</button>'
      + '<span class="badge badge-'+f[1]+'">'+f[0]+'</span>'
      + (d.pontos ? '<span class="pts" title="Peso desta disciplina na nota">'
         + (Math.round(d.pontos*10)/10) + ' pts</span>' : "")
      + '<span class="disc-cont">'+d._feitos+'/'+d._total
      + (d._parciais ? '  &#9681;'+d._parciais : '')+'</span>'
      + '<span class="disc-pct">'+d._pct+'%</span></div></div>'
      + '<div class="barra" role="img" aria-label="'+esc(d.nome)+': '+d._pct
      + ' por cento, '+d._feitos+' de '+d._total+' subtopicos">'
      + '<div class="preenchida" style="width:'+d._pct+'%;background:'
      + corDaFaixa(d._pct)+'"></div></div>'
      + '</summary><div class="topicos">'+topicos+'</div></details></article>';
  }).join("");
}

function renderEdital(){
  var alvo = document.getElementById("painel-edital");
  if (!DADOS.editais.length){
    alvo.innerHTML = '<div class="vazio"><h2>Nenhum edital cadastrado ainda</h2>'
      + '<p>Salve o conteudo programatico em um .txt e rode:</p>'
      + '<code>python edital_parser.py --id meu-concurso -i edital.txt '
      + '--concurso "Nome" --preview</code>'
      + '<p style="margin-top:14px">Depois:</p>'
      + '<code>python semantic_matcher.py --todos</code>'
      + '<p style="margin-top:14px">Enquanto isso, a aba <b>Acervo</b> mostra '
      + 'tudo o que ja existe no seu vault.</p></div>';
    return;
  }
  var ed = DADOS.editais[ATUAL] || DADOS.editais[0];
  var manual = manualDe(ed);
  var tt = totais(ed);
  var sub = [ed.cargo, ed.banca].filter(Boolean).join(" · ");

  renderNav(tt);

  // Com disciplina oculta o percentual deixa de valer para o edital inteiro.
  // Dizer isso em letras e obrigatorio: senao o numero vira uma mentira.
  var escopo = tt.ocultas
    ? '<p class="escopo">Percentual sobre <b>'+tt.total+'</b> subtopicos '
      + '&mdash; '+tt.ocultas+' disciplina'+(tt.ocultas>1?'s':'')+' fora da conta. '
      + 'Com tudo: <b>'+tt.pctGeral+'%</b> de '+tt.totalGeral+'.</p>'
    : "";

  var pp = porPontos(ed);
  var numeros = pp
    ? '<div class="num"><b>'+tt.pontosFeitos+'</b><span>Pontos cobertos</span></div>'
      + '<div class="num"><b>'+(Math.round((tt.pontos-tt.pontosFeitos)*10)/10)
      + '</b><span>Pontos em aberto</span></div>'
      + '<div class="num"><b>'+tt.parciais+'</b><span>Cobertura parcial</span></div>'
      + '<div class="num"><b>'+tt.pontos+'</b><span>'
      + (tt.ocultas ? "Pontos na conta" : "Pontos da prova")+'</span></div>'
      + '<div class="num"><b>'+tt.feitos+'/'+tt.total+'</b><span>Subtopicos</span></div>'
    : '<div class="num"><b>'+tt.feitos+'</b><span>Ja estudados</span></div>'
      + '<div class="num"><b>'+tt.pendentes+'</b><span>Pendentes</span></div>'
      + '<div class="num"><b>'+tt.parciais+'</b><span>Cobertura parcial</span></div>'
      + '<div class="num"><b>'+tt.total+'</b><span>'
      + (tt.ocultas ? "Na conta" : "Total do edital")+'</span></div>'
      + '<div class="num"><b>'+tt.manuais+'</b><span>Marcados por voce</span></div>';

  var logos = ed.logos || {};
  var brasao = logos.municipio
    ? '<img class="brasao" src="'+logos.municipio+'" alt="Brasao do municipio">'
    : "";
  var banca = logos.banca
    ? '<img class="logo-banca" src="'+logos.banca+'" alt="Logo da banca">'
    : "";

  document.getElementById("hero").className = "hero" + (jaPassou(ed) ? " passado" : "");
  document.getElementById("hero").innerHTML =
      donut(pp ? tt.pctPontos : tt.pct, pp ? "da nota" : "do edital")
    + '<div class="hero-txt">'
    + '<div class="titulo-linha">' + brasao
    + '<div><h1 class="hero-tit">'+esc(ed.concurso)+'</h1>'
    + '<p class="hero-sub">'+esc(sub)+'</p></div>' + banca + '</div>'
    + infoHTML(ed) + escopo + '<div class="nums">' + numeros + '</div></div>';

  // alternancia subtopicos <-> pontos
  var alt = document.getElementById("alternar-modo");
  alt.hidden = !ed.tem_pesos;
  if (ed.tem_pesos) {
    alt.innerHTML = '<button class="btn btn-mini'+(pp?"":" sel")+'" type="button"'
      + ' data-modo="subtopicos">Por subtopicos</button>'
      + '<button class="btn btn-mini'+(pp?" sel":"")+'" type="button"'
      + ' data-modo="pontos">Por pontos da prova</button>'
      + (pp ? '<span class="modo-dica">'+tt.pct+'% dos subtopicos</span>'
            : '<span class="modo-dica">'+tt.pctPontos+'% da nota</span>');
  }

  renderOcultas(ed);

  document.getElementById("acoes-manual").hidden = !LOCAL || tt.manuais === 0;
  document.getElementById("conta-manual").textContent = tt.manuais;

  var ajuda = document.querySelector(".ajuda");
  if (ajuda) {
    ajuda.innerHTML = LOCAL
      ? 'Clique no circulo ao lado de qualquer subtopico para marcar o que ja '
        + 'estudou &middot; use o &#8856; da disciplina para tira-la da conta.'
      : 'Somente visualizacao &middot; marque o que estudou no painel do '
        + 'computador &middot; use o &#8856; para tirar uma disciplina da conta.';
  }
  alvo.innerHTML = renderDisciplinas(ed, manual);
  animar();
}

/* faixa com as disciplinas fora da conta, para trazer de volta */
function renderOcultas(ed){
  var alvo = document.getElementById("ocultas");
  var lista = lerOcultas(ed).filter(function(nome){
    return ed.disciplinas.some(function(d){ return d.nome === nome; });
  });
  if (!lista.length) { alvo.hidden = true; alvo.innerHTML = ""; return; }

  alvo.hidden = false;
  alvo.innerHTML = '<span class="ocultas-rot">Fora da conta:</span>'
    + lista.map(function(nome){
        var d = ed.disciplinas.filter(function(x){ return x.nome === nome; })[0];
        return '<button class="voltar" type="button" data-voltar="'+esc(nome)+'"'
          + ' title="Trazer de volta para a conta">'
          + esc(nome) + ' <b>+'+(d ? d._total : 0)+'</b> &#8634;</button>';
      }).join("")
    + '<button class="btn btn-mini" type="button" id="voltar-todas">'
    + 'Mostrar todas</button>';
}

/* informacoes do concurso: data da prova, discursiva, contagem regressiva */
function infoHTML(ed){
  var info = ed.info || {};
  if (!info.data_prova && !info.discursiva && !(info.observacoes || []).length) {
    return "";
  }
  var partes = [];

  if (info.data_prova) {
    var hoje = new Date(); hoje.setHours(0, 0, 0, 0);
    var prova = new Date(info.data_prova + "T00:00:00");
    var dias = Math.round((prova - hoje) / 86400000);
    if (dias < 0) {
      partes.push('<span class="pill encerrado">Prova realizada em '
        + prova.toLocaleDateString("pt-BR") + ' &middot; ha <b>'
        + Math.abs(dias) + '</b> dias</span>');
    } else {
      var txt = dias > 1 ? ("faltam <b>" + dias + "</b> dias")
              : dias === 1 ? "<b>e amanha</b>" : "<b>e hoje</b>";
      var urgente = dias <= 30 ? " urgente" : "";
      partes.push('<span class="pill'+urgente+'">Prova '
        + prova.toLocaleDateString("pt-BR") + ' &middot; ' + txt + '</span>');
    }
    if (dias > 0 && ed.disciplinas.length) {   // so antes da prova
      var pend = 0;
      ed.disciplinas.forEach(function(d){
        if (!d._oculta) pend += (d._total - d._feitos);
      });
      if (pend) partes.push('<span class="pill">'
        + (pend / dias).toFixed(1) + ' subtopicos/dia para fechar o edital</span>');
    }
  }
  if (info.inscricoes && info.inscricoes.fim) {
    var fim = new Date(info.inscricoes.fim + "T00:00:00");
    var hj = new Date(); hj.setHours(0, 0, 0, 0);
    // prazo vencido vira ruido no painel; so avisa enquanto da para se inscrever
    if (fim >= hj) {
      partes.push('<span class="pill">Inscricoes ate '
        + fim.toLocaleDateString("pt-BR") + '</span>');
    }
  }
  if (info.vagas) partes.push('<span class="pill">'+esc(info.vagas)+' vagas</span>');
  if (info.salario) partes.push('<span class="pill">'+esc(info.salario)+'</span>');

  var disc = info.discursiva;
  if (disc && disc.tem) {
    var d = "Discursiva";
    if (disc.tipo) d += ": " + esc(disc.tipo);
    if (disc.materias && disc.materias.length) {
      d += " &mdash; " + disc.materias.map(esc).join(", ");
    }
    if (disc.peso) d += " (" + esc(disc.peso) + ")";
    partes.push('<span class="pill pill-disc">' + d + '</span>');
  } else if (disc && disc.tem === false) {
    partes.push('<span class="pill">Sem prova discursiva</span>');
  }

  (info.observacoes || []).forEach(function(o){
    partes.push('<span class="pill">'+esc(o)+'</span>');
  });

  return '<div class="info">' + partes.join("") + '</div>';
}

/* navegacao entre editais: um cartao por concurso, sempre visivel */
function renderNav(tt){
  var nav = document.getElementById("nav-editais");
  if (DADOS.editais.length < 2) {
    nav.innerHTML = '<span class="nav-unico">'
      + esc(DADOS.editais[0].concurso) + '</span>'
      + '<span class="nav-dica">Rode o parser com outro --id para comparar '
      + 'concursos aqui</span>';
    return;
  }
  // os encerrados vao para o fim: o que importa e o que ainda vai cair
  var ordem = DADOS.editais.map(function(ed, i){ return {ed: ed, i: i}; });
  ordem.sort(function(a, b){
    var pa = jaPassou(a.ed) ? 1 : 0, pb = jaPassou(b.ed) ? 1 : 0;
    if (pa !== pb) return pa - pb;
    var da = diasAteProva(a.ed), db = diasAteProva(b.ed);
    if (da === null && db === null) return a.i - b.i;
    if (da === null) return 1;
    if (db === null) return -1;
    return pa ? (db - da) : (da - db);   // futuros: mais proximo primeiro
  });

  nav.innerHTML = ordem.map(function(item){
    var ed = item.ed, i = item.i;
    var t = (i === ATUAL) ? tt : totais(ed);
    var pct = porPontos(ed) ? t.pctPontos : t.pct;
    var mini = (ed.logos && ed.logos.municipio)
      ? '<img class="chip-logo" src="'+ed.logos.municipio+'" alt="">' : "";
    var fim = jaPassou(ed);
    return '<button class="chip'+(i===ATUAL?" ativo":"")+(fim?" passado":"")
      + '" type="button" data-edital="'+i+'" aria-selected="'+(i===ATUAL)+'"'
      + (fim ? ' title="Prova realizada em '+dataProvaBR(ed)+'"' : "") + '>'
      + '<span class="chip-topo">'+mini
      + '<span class="chip-nome">'+esc(ed.concurso)+'</span></span>'
      + '<span class="chip-pct" style="color:'
      + (fim ? "var(--tinta-D)" : corDaFaixa(pct))+'">'+pct+'%</span>'
      + '<span class="chip-barra"><i style="width:'+pct+'%;background:'
      + (fim ? "var(--faixa-D)" : corDaFaixa(pct))+'"></i></span>'
      + (fim ? '<span class="selo-fim">Prova realizada</span>' : "")
      + '</button>';
  }).join("");
}

/* exporta TODAS as marcacoes num arquivo so, que vale para todos os editais */
function exportar(){
  var marcas = lerMarcas();
  var saida = {
    "_leia-me": "Marcacoes feitas no painel. Salve este arquivo como "
      + "marcas.json na pasta _edital-tracker (ao lado do index.html) e rode "
      + "ATUALIZAR PAINEL. Vale para todos os editais.",
    "_gerado_em": new Date().toISOString().slice(0, 10),
    "marcas": marcas
  };

  var texto = JSON.stringify(saida, null, 2);
  var aviso = document.getElementById("conta-manual");

  // No arquivo local, baixa o manual.json. Dentro de uma pagina publicada o
  // download e bloqueado, entao copia para a area de transferencia.
  var baixou = false;
  try {
    var a = document.createElement("a");
    if (typeof a.download === "string" && location.protocol === "file:") {
      var blob = new Blob([texto], {type: "application/json"});
      a.href = URL.createObjectURL(blob);
      a.download = "marcas.json";
      a.click();
      setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1000);
      baixou = true;
    }
  } catch (e) { baixou = false; }

  if (baixou) return;

  function feito(msg){
    var antes = aviso ? aviso.textContent : "";
    if (aviso) { aviso.textContent = msg; setTimeout(function(){
      aviso.textContent = antes; }, 4000); }
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(texto).then(
      function(){ feito("copiado!"); },
      function(){ mostrarTexto(texto); });
  } else {
    mostrarTexto(texto);
  }
}

/* ultimo recurso: mostra o JSON para copiar a mao */
function mostrarTexto(texto){
  var cx = document.getElementById("caixa-export");
  cx.hidden = false;
  cx.innerHTML = '<p class="ajuda">Copie o conteudo abaixo e salve como '
    + '<b>_edital-tracker/marcas.json</b>:</p>'
    + '<textarea readonly rows="8"></textarea>';
  var ta = cx.querySelector("textarea");
  ta.value = texto;
  ta.focus(); ta.select();
}

function renderAcervo(){
  var ac = DADOS.acervo;
  var termo = document.getElementById("busca-acervo").value.trim().toLowerCase();
  var html = ac.materias.map(function(m){
    var notas = m.notas.filter(function(n){
      if (!termo) return true;
      return (n.titulo + " " + n.caminho + " " + (n.tags||[]).join(" ") + " "
              + (n.secoes||[]).join(" ")).toLowerCase().indexOf(termo) !== -1;
    });
    if (!notas.length) return "";
    var itens = notas.map(function(n){
      var url = linkNota(n.caminho);
      var tit = url ? '<a class="nota" href="'+esc(url)+'">'+esc(n.titulo)+'</a>'
                    : '<span class="nota">'+esc(n.titulo)+'</span>';
      return '<div class="topico"><div class="topico-cab">'
        + '<span class="topico-num">'+esc(n.aula||"")+'</span>'
        + '<span class="topico-tit">'+esc(n.titulo)+'</span>'
        + '<span class="topico-cont">'+n.palavras+' pal.</span></div>'
        + '<ul class="subs"><li class="sub"><span class="sub-txt">'
        + esc((n.secoes||[]).join(" · ")) + '</span>' + tit + '</li></ul></div>';
    }).join("");
    return '<article class="disc"><details><summary><div class="disc-topo">'
      + '<h3 class="disc-nome">'+esc(m.nome)+'</h3><div class="disc-meta">'
      + '<span class="disc-cont">'+notas.length+' notas</span>'
      + '</div></div></summary><div class="topicos">'+itens+'</div>'
      + '</details></article>';
  }).join("");
  document.getElementById("painel-acervo").innerHTML =
    html || '<div class="vazio">Nada encontrado.</div>';
}

function animar(){
  requestAnimationFrame(function(){
    document.querySelectorAll(".preenchida").forEach(function(el){
      var w = el.style.width; el.style.width = "0";
      requestAnimationFrame(function(){ el.style.width = w; });
    });
  });
}

function aba(nome){
  ["edital","acervo"].forEach(function(n){
    document.getElementById("vista-"+n).classList.toggle("oculto", n !== nome);
    document.getElementById("aba-"+n).setAttribute("aria-selected", n === nome);
  });
  if (nome === "acervo") renderAcervo();
}

/* Converte marcacoes do formato antigo (uma lista por edital, por id) para o
   atual (compartilhadas). Roda uma vez e nao apaga nada se algo der errado. */
function migrarMarcasAntigas(){
  var marcas = lerMarcas();
  var mudou = false;
  DADOS.editais.forEach(function(ed){
    var chave = chaveLocal(ed), antigo = null;
    try {
      var bruto = localStorage.getItem(chave);
      if (bruto) antigo = JSON.parse(bruto);
    } catch (e) { return; }
    if (!antigo) return;

    ed.disciplinas.forEach(function(d){
      d.topicos.forEach(function(t){
        t.subtopicos.forEach(function(s){
          if (antigo.hasOwnProperty(s.id)) {
            var ch = chaveMarca(s);
            if (!marcas.hasOwnProperty(ch)) {
              marcas[ch] = antigo[s.id];
              mudou = true;
            }
          }
        });
      });
    });
    try { localStorage.removeItem(chave); } catch (e) {}
  });
  if (mudou) gravarMarcas(marcas);
}

document.addEventListener("DOMContentLoaded", function(){
  migrarMarcasAntigas();
  /* troca de edital pelos cartoes */
  document.getElementById("nav-editais").addEventListener("click", function(ev){
    var botao = ev.target.closest("[data-edital]");
    if (!botao) return;
    ATUAL = parseInt(botao.dataset.edital, 10) || 0;
    try { localStorage.setItem("edital-tracker:ultimo", DADOS.editais[ATUAL].id); }
    catch (e) {}
    renderEdital();
  });

  /* ocultar disciplina / trazer de volta */
  document.getElementById("painel-edital").addEventListener("click", function(ev){
    var botao = ev.target.closest("[data-ocultar]");
    if (!botao) return;
    ev.preventDefault();
    ev.stopPropagation();          // nao deixa o <summary> abrir junto
    alternarDisciplina(DADOS.editais[ATUAL], botao.dataset.ocultar);
    renderEdital();
  }, true);

  document.getElementById("ocultas").addEventListener("click", function(ev){
    var ed = DADOS.editais[ATUAL];
    if (ev.target.closest("#voltar-todas")) {
      gravarOcultas(ed, []);
      renderEdital();
      return;
    }
    var botao = ev.target.closest("[data-voltar]");
    if (!botao) return;
    alternarDisciplina(ed, botao.dataset.voltar);
    renderEdital();
  });

  /* check manual: clique na marca do subtopico */
  document.getElementById("painel-edital").addEventListener("click", function(ev){
    var botao = ev.target.closest("[data-sub]");
    if (!botao) return;
    ev.preventDefault();
    var ed = DADOS.editais[ATUAL], alvo = botao.dataset.sub;
    ed.disciplinas.forEach(function(d){
      d.topicos.forEach(function(t){
        t.subtopicos.forEach(function(s){
          if (s.id === alvo) alternar(ed, s);
        });
      });
    });
    renderEdital();
  });

  document.getElementById("alternar-modo").addEventListener("click", function(ev){
    var botao = ev.target.closest("[data-modo]");
    if (!botao) return;
    gravarModo(botao.dataset.modo);
    renderEdital();
  });

  document.getElementById("exportar").addEventListener("click", exportar);
  document.getElementById("limpar").addEventListener("click", function(){
    if (!confirm("Remover TODAS as marcacoes feitas neste navegador? Elas "
               + "valem para os quatro editais. O que veio do manual.json "
               + "continua valendo.")) return;
    delete MEM[CHAVE_MARCAS];
    try { localStorage.removeItem(CHAVE_MARCAS); } catch (e) {}
    renderEdital();
  });

  /* volta para o ultimo edital aberto */
  try {
    var ultimo = localStorage.getItem("edital-tracker:ultimo");
    DADOS.editais.forEach(function(ed, i){ if (ed.id === ultimo) ATUAL = i; });
  } catch (e) {}

  document.getElementById("busca").addEventListener("input", renderEdital);
  document.getElementById("ordem").addEventListener("change", renderEdital);
  document.getElementById("busca-acervo").addEventListener("input", renderAcervo);
  document.getElementById("aba-edital").addEventListener("click", function(){ aba("edital"); });
  document.getElementById("aba-acervo").addEventListener("click", function(){ aba("acervo"); });
  document.getElementById("tema").addEventListener("click", function(){
    var r = document.documentElement;
    var escuro = getComputedStyle(r).getPropertyValue("--plano").trim() === "#0d0d0d";
    r.setAttribute("data-theme", escuro ? "light" : "dark");
  });
  renderEdital();
});
"""

PAGINA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<title>Progresso de Editais</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="marca">EDITAL TRACKER <span>&middot; {n_editais} edital(is) &middot; {n_notas} notas</span></p>
    <div class="abas">
      <button class="btn" id="aba-edital" type="button" aria-selected="true">Edital</button>
      <button class="btn" id="aba-acervo" type="button" aria-selected="false">Acervo</button>
      <button class="btn" id="tema" type="button">Tema</button>
    </div>
  </header>

  <div id="vista-edital">
    <nav id="nav-editais" aria-label="Concursos"></nav>
    <div id="alternar-modo" class="modos" hidden></div>
    <section class="hero" id="hero"></section>
    <div class="filtros">
      <input id="busca" type="search" placeholder="Filtrar disciplina..."
             aria-label="Filtrar disciplina">
      <select id="ordem" aria-label="Ordenar">
        <option value="edital">Ordem do edital</option>
        <option value="pctasc">Menor cobertura primeiro</option>
        <option value="pct">Maior cobertura primeiro</option>
        <option value="pend">Mais pendencias primeiro</option>
        <option value="impacto">Maior impacto na nota</option>
      </select>
      <span id="acoes-manual" class="acoes" hidden>
        <span class="conta"><b id="conta-manual">0</b> marcados por voce</span>
        <button class="btn" id="exportar" type="button">Salvar marcacoes</button>
        <button class="btn" id="limpar" type="button">Limpar</button>
      </span>
    </div>
    <div id="ocultas" class="ocultas" hidden></div>
    <div id="caixa-export" hidden></div>
    <p class="ajuda">Clique no circulo ao lado de qualquer subtopico para marcar
      o que ja estudou &middot; use o &#8856; da disciplina para tira-la da conta.</p>
    <div id="painel-edital"></div>
  </div>

  <div id="vista-acervo" class="oculto">
    <div class="filtros">
      <input id="busca-acervo" type="search"
             placeholder="Buscar assunto, tag ou nota no vault..."
             aria-label="Buscar no acervo">
    </div>
    <div id="painel-acervo"></div>
  </div>

  <footer>{rodape}</footer>
</div>
<script>{js}</script>
</body>
</html>"""


def bloco_pendencias(caminho: Path) -> str:
    """
    Aviso fixo no topo do painel quando ha notas no vault que ainda nao foram
    avaliadas para a cobertura. A cobertura vem do decisoes.json de cada edital
    e NAO se recalcula sozinha; sem este aviso a nota nova parecia "ignorada".
    Devolve "" quando nao ha pendencia (ou o arquivo nao existe).
    """
    from html import escape
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    n = int(dados.get("quantidade", 0) or 0)
    if n <= 0:
        return ""
    itens = "".join("<li>" + escape(x) + "</li>" for x in dados.get("notas", [])[:60])
    plural = "nota ainda nao foi avaliada" if n == 1 else "notas ainda nao foram avaliadas"
    return (
        '<div role="status" style="margin:0 0 14px;padding:10px 14px;'
        'background:#fdf3e3;color:#1a1a19;border-left:4px solid #fab219;'
        'border-radius:6px;font-size:13.5px;line-height:1.5">'
        "<b>&#9888; " + str(n) + " " + plural + " para a cobertura.</b> "
        "A porcentagem abaixo ainda <b>nao inclui</b> o que voce escreveu nelas: "
        "a cobertura vem das decisoes gravadas e nao se recalcula sozinha. "
        "Peca ao agente: <i>&ldquo;revise a cobertura com as notas novas&rdquo;</i>."
        '<details style="margin-top:6px"><summary style="cursor:pointer">Ver as notas</summary>'
        '<ul style="margin:6px 0 0 18px;padding:0">' + itens + "</ul></details></div>"
    )


def render(dados: dict, vault: str) -> str:
    js = JS.replace("__DADOS__", json.dumps(dados, ensure_ascii=False))
    js = js.replace("__VAULT__", json.dumps(vault, ensure_ascii=False))
    rodape = "Gerado em " + datetime.now().strftime("%d/%m/%Y %H:%M")
    marcados = [e for e in dados["editais"] if e.get("match", {}).get("modelo")]
    if marcados:
        rodape += " &middot; match: " + ", ".join(
            sorted({str(e["match"]["modelo"]) for e in marcados})
        )
    return PAGINA.format(
        css=CSS, js=js, rodape=rodape,
        n_editais=len(dados["editais"]),
        n_notas=dados["acervo"]["total_notas"],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Gera o dashboard multi-edital a partir de editais/*/edital.json"
    )
    p.add_argument("--editais", default="editais", help="pasta dos editais")
    p.add_argument("-n", "--inventario", default="data/inventario.json")
    p.add_argument("-o", "--output", default="index.html")
    p.add_argument("--pendencias", default="data/pendencias.json",
                   help="notas ainda sem avaliacao de cobertura (gerado por revisar_cobertura.py)")
    p.add_argument("--vault", default="",
                   help="nome do vault para os links obsidian:// (vazio desativa)")
    p.add_argument("--abrir", action="store_true")
    args = p.parse_args(argv)

    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    pasta = Path(args.editais)
    pasta.mkdir(parents=True, exist_ok=True)

    # marcas.json: marcacoes salvas pelo painel, validas para todos os editais
    bruto_marcas = carregar_json_opcional(Path("marcas.json"), {})
    marcas = bruto_marcas.get("marcas", bruto_marcas) if isinstance(
        bruto_marcas, dict) else {}
    marcas = {k: bool(v) for k, v in marcas.items() if not str(k).startswith("_")}
    if marcas:
        print("marcas.json: {0} marcacoes carregadas".format(len(marcas)))

    editais = carregar_editais(pasta, marcas)

    caminho_inv = Path(args.inventario)
    if caminho_inv.is_file():
        acervo = montar_acervo(json.loads(caminho_inv.read_text(encoding="utf-8")))
    else:
        print("aviso: inventario nao encontrado (" + str(caminho_inv)
              + "). A aba Acervo ficara vazia.", file=sys.stderr)
        acervo = {"materias": [], "total_notas": 0, "vault": "", "gerado_em": ""}

    saida = Path(args.output)
    if saida.suffix.lower() == ".md":
        print("ERRO: a saida nao pode ser .md", file=sys.stderr)
        return 1
    saida.parent.mkdir(parents=True, exist_ok=True)
    html = render({"editais": editais, "acervo": acervo, "marcas": marcas},
                  args.vault)
    # aviso de notas ainda nao avaliadas, logo abaixo do cabecalho
    html = html.replace("</header>", "</header>\n" + bloco_pendencias(Path(args.pendencias)), 1)
    saida.write_text(html, encoding="utf-8")

    if not editais:
        print("nenhum edital em " + str(pasta) + "/ - dashboard gerado so com o Acervo")
    for ed in editais:
        print("{0:<34} {1:>5}%  ({2}/{3})".format(
            (ed["concurso"] + " - " + ed["cargo"]).strip(" -")[:34],
            ed["pct"], ed["feitos"], ed["total"]))
        for disc in sorted(ed["disciplinas"], key=lambda d: -d["pct"]):
            cheio = int(round(disc["pct"] / 5))
            print("   {0:<32} {1}{2} {3:>5}%".format(
                disc["nome"][:32], "#" * cheio, "." * (20 - cheio), disc["pct"]))
    print("acervo: {0} notas em {1} materias".format(
        acervo["total_notas"], len(acervo["materias"])))
    print("-> " + str(saida.resolve()))

    if args.abrir:
        webbrowser.open(saida.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
