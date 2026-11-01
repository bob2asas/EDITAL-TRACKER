# Edital Tracker

Cruza o conteúdo programático de um edital com as suas notas do Obsidian e gera
um dashboard de progresso.

> **Esta pasta é isolada.** Nenhum script altera, move ou apaga arquivos do vault.
> As notas `.md` são abertas apenas em modo leitura.

---

## Primeiros passos (depois de clonar)

Requisitos: **Python 3.10+**. A ferramenta usa só a biblioteca padrão; o `Pillow`
é opcional (reduz os logos embutidos no painel) e o `anthropic` só é preciso se
você quiser a avaliação automática de cobertura por IA (`--api`).

```bash
# 1. gere o edital de exemplo e o painel
python edital_parser.py --id exemplo-sefaz-al -i editais/exemplo-sefaz-al/edital.txt \
    --concurso "SEFAZ AL" --cargo "Auditor Fiscal" --banca "Cebraspe" --max-len 400 --reset
python dashboard_generator.py --vault ""

# 2. abra index.html no navegador
```

Para ligar ao **seu** vault do Obsidian, a pasta do projeto precisa ficar
**dentro do vault**, um nível abaixo de uma pasta de estudos:

```
<seu-vault>/
└── <pasta de estudos>/        # ex.: "Concursos"
    └── _edital-tracker/       # este repositório
```

O script se localiza sozinho a partir da própria pasta:

```bash
python atualizar.py
```

Ele só **lê** as notas `.md`; nada no vault é alterado.

Para cadastrar outro edital: coloque o texto do conteúdo programático em
`editais/<id>/edital.txt`, crie `editais/<id>/info.json` (veja o do exemplo) e
rode o `edital_parser.py` como acima.

> O que o `.gitignore` deixa de fora é, de propósito, tudo o que é pessoal:
> o `index.html` gerado, suas marcações, a cobertura (`decisoes.json`), o
> inventário das suas notas e o `publicar.json`. Nada disso deve ir para o
> repositório.

---


# COMO USAR NO DIA A DIA

## 1. Abrir o painel

Dê duplo clique em **`index.html`**. Abre no navegador, funciona offline.

Para ter sempre à mão: abra uma vez, e com a página aberta arraste o ícone da
barra de endereço para a barra de favoritos.

## 2. Marcar o que já estudou

**Pelo próprio painel, sem rodar nada:** clique no círculo ao lado do subtópico.
Os números recalculam na hora. É o jeito mais rápido, e a sua marcação sempre
vence o resultado automático.

Para a marcação sobreviver a uma troca de computador: clique em **Salvar
marcações**, e mova o `manual.json` baixado para `editais/<id>/manual.json`.

## 3. Reler as notas do Obsidian

Depois de escrever notas novas no vault, dê duplo clique em:

**`ATUALIZAR PAINEL.bat`**

Ele relê o vault inteiro, reaplica a cobertura de todos os editais, regera o
painel e abre no navegador. Equivale a:

```bash
python atualizar.py
```

**Importante:** reler o vault atualiza o *acervo* (quantas notas você tem), mas
**não transforma sozinho nota nova em cobertura**. Quem decide se uma nota cobre
um subtópico é o `decisoes.json` de cada edital. Para atualizar essa avaliação,
há dois caminhos:

| Caminho | Como |
|---|---|
| **Manual** (grátis, imediato) | marque no painel o que estudou |
| **Automático** (precisa de API key) | `pip install anthropic`, defina `ANTHROPIC_API_KEY` e rode `python atualizar.py --api` |

## 4. Adicionar outro edital

Um concurso novo = uma pasta nova. Os existentes não são tocados.

```bash
python edital_parser.py --id sefaz-pe -i editais/sefaz-pe/edital.txt --concurso "SEFAZ PE" --cargo "Auditor" --banca "FGV" --preview
```

Passo a passo:

1. Crie a pasta `editais/sefaz-pe/`
2. Salve o conteúdo programático em `editais/sefaz-pe/edital.txt`
3. Rode o comando acima e **confira o `--preview`** (edital mal lido = percentual mentiroso)
4. Opcional: `info.json` (data da prova, discursiva, pontuação) e os logos
5. Duplo clique em `ATUALIZAR PAINEL.bat`

O novo concurso aparece como mais um cartão no topo do painel, com percentual
próprio. Clicar alterna entre eles.

> Para a avaliação de cobertura do edital novo, vale o mesmo da seção 3: ou você
> marca no painel, ou roda com `--api`, ou pede uma revisão nota a nota.

## O que fica em cada lugar

```
_edital-tracker/
├── index.html                  <- ABRA ESTE
├── ATUALIZAR PAINEL.bat        <- CLIQUE AQUI depois de estudar
├── data/inventario.json        o retrato do seu vault
└── editais/
    └── iss-caruaru/
        ├── edital.txt          conteúdo programático bruto
        ├── edital.json         já estruturado e marcado
        ├── decisoes.json       por que cada item está (ou não) coberto
        ├── info.json           data da prova, discursiva, pontuação
        ├── logo-municipio.png
        └── logo-banca.png
```

---

## Instalação (uma vez)

```bash
pip install anthropic
```

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

## Multi-edital

Cada concurso tem sua própria pasta. Adicionar um edital novo não mexe nos outros:

```
editais/
  iss-gru/edital.json
  sefaz-sc/edital.json
  camara-deputados/edital.json
data/inventario.json      <- o vault, compartilhado por todos
index.html                <- um dashboard, seletor no topo
```

O inventário do vault é varrido **uma vez** e serve a todos os editais. Quando
sair um edital novo, você roda só os passos 1, 3 e 4 — o passo 2 só precisa
rodar de novo quando você criar notas novas.

## Pipeline

```
edital_raw.txt ──[1]──> editais/<id>/edital.json ──┐
                                                   ├──[3]──> marcado ──[4]──> index.html
vault Obsidian ──[2]──> data/inventario.json ──────┘
```

### 1. Parser do edital

```bash
python edital_parser.py --id iss-gru -i data/iss_gru.txt --concurso "ISS Guarulhos" --cargo "Auditor Fiscal" --banca "IBAM" --preview
```

Rodar de novo com o mesmo `--id` **preserva o que já estava concluído** (os IDs
dos subtópicos são hashes do texto). Use `--reset` para zerar o progresso —
necessário quando o match anterior tinha erros, senão eles são preservados.

Flags úteis quando o layout do edital é difícil: `--disciplinas "Nome"` força
cabeçalhos, `--max-len` controla a quebra de enumerações longas por vírgula.

Confira o `--preview`: edital mal parseado gera percentual mentiroso.
Ajuste com `--min-len` se os subtópicos saírem quebrados demais.

### 2. Inventário do Obsidian

```bash
python obsidian_scraper.py --vault "C:\caminho\do\vault" --somente "02 - Concursos" -o data/inventario.json --preview
```

### 3. Match semântico

```bash
python semantic_matcher.py --id iss-gru --dry-run --preview
```

```bash
python semantic_matcher.py --id iss-gru --limite-lotes 2
```

```bash
python semantic_matcher.py --todos
```

Sempre nessa ordem: o `--dry-run` é grátis e mostra se o pré-filtro está
achando as notas certas; `--limite-lotes 2` custa centavos e mostra se o
julgamento do modelo está razoável.

**Sem API key?** O match pode vir de um arquivo de decisões pronto:

```bash
python semantic_matcher.py --id iss-gru --importar data/decisoes.json
```

Formato aceito (a chave pode ser o id, o texto do subtópico, ou
`"Disciplina :: texto"` quando o mesmo texto aparece em duas disciplinas):

```json
{ "Acentuação gráfica": { "coberto": true, "confianca": 0.97, "notas": ["Ortografia e Acentuação"], "justificativa": "Seções 2, 3 e 4 da nota." } }
```

A nota pode ser citada só pelo título — o script resolve o caminho pelo
inventário. Confiança entre **0,50 e 0,69 com `coberto: false`** significa
**cobertura parcial**: existe base no vault, mas falta o recorte que o edital
pede. O dashboard marca esses itens com ◐.

### 4. Dashboard

```bash
python dashboard_generator.py --abrir
```

Gera um `index.html` com **todos** os editais e duas abas:

- **Edital** — cartões de navegação entre concursos (cada um com seu
  percentual), donut global, barras por disciplina e o detalhamento
  subtópico a subtópico
- **Acervo** — tudo o que existe no vault por matéria, com busca; funciona
  mesmo sem nenhum edital cadastrado

## Identidade visual do concurso

Coloque na pasta do edital — vale para qualquer concurso futuro:

```
editais/<id>/logo-municipio.png     brasão da prefeitura / órgão
editais/<id>/logo-banca.png         logo da banca
```

Aceita `.png`, `.jpg`, `.webp`, `.gif` e `.svg` (até 400 KB cada). As imagens
são embutidas no HTML em base64, então o `index.html` continua sendo um arquivo
único que abre offline. O brasão também vira miniatura no cartão de navegação
entre concursos. Sem os arquivos, o painel simplesmente não mostra logo.

Para recortar do PDF do edital, a dica é cortar pelo que **não é papel branco**
em vez de chutar coordenadas — foi assim que os logos do ISS Caruaru saíram
limpos.

## Cores por faixa

As barras, o donut e os selos seguem um semáforo pelo percentual:

| Faixa | Cor | Selo |
|---|---|---|
| 85% ou mais | verde | Dominado |
| 60% a 84% | amarelo | Avançado |
| 35% a 59% | laranja | Em curso |
| abaixo de 35% | vermelho | Inicial |

A cor nunca anda sozinha: o selo com texto e o número em % vão sempre junto,
para quem não distingue as cores. O amarelo e o laranja usam um tom mais escuro
no texto do selo, porque no fundo claro eles não teriam contraste suficiente.

## Informações do concurso

Cada edital pode ter um `editais/<id>/info.json` com data da prova, discursiva,
inscrições, vagas e salário. O que estiver preenchido vira uma faixa no topo do
painel; campo vazio simplesmente não aparece.

```json
{
  "data_prova": "2026-12-14",
  "vagas": "10",
  "salario": "R$ 14.500,00",
  "inscricoes": { "inicio": "2026-10-20", "fim": "2026-11-18" },
  "discursiva": {
    "tem": true,
    "tipo": "Estudo de caso + 5 questões dissertativas",
    "materias": ["Direito Tributário", "Legislação Municipal"],
    "peso": "peso 2, eliminatória"
  },
  "observacoes": ["80 questões objetivas", "Caruaru/PE"]
}
```

Com `data_prova` preenchida o painel calcula sozinho **quantos dias faltam** (em
vermelho nos últimos 30) e **quantos subtópicos por dia** você precisa fechar
para zerar o edital a tempo — esse número respeita as disciplinas ocultas.

## Percentual por pontos da prova

Contar subtópicos trata todas as disciplinas como iguais — e elas não são. No
ISS Caruaru, Informática tem 48 subtópicos e vale **5 dos 130 pontos**;
Conhecimentos Específicos vale 60, mais os 40 da discursiva.

Preencha `pontuacao` e `mapa_pontuacao` no `info.json` e o painel passa a
mostrar **% da nota** em vez de % do edital. O botão no topo alterna entre as
duas leituras a qualquer momento.

```json
"mapa_pontuacao": {
  "Sistema Tributário Brasileiro": "Conhecimentos Especificos",
  "Português para todos os cargos": "Portugues"
},
"discursiva_sobre": "Conhecimentos Especificos",
"pontuacao": {
  "Conhecimentos Especificos": { "questoes": 20, "valor": 3, "maximo": 60 },
  "Portugues": { "questoes": 10, "valor": 2, "maximo": 20 }
}
```

Quando várias disciplinas caem no mesmo componente (quatro delas formam
"Conhecimentos Específicos"), os pontos são repartidos **na proporção do número
de subtópicos** — o edital não diz quantas questões cada uma recebe, e essa é a
melhor aproximação disponível. `discursiva_sobre` joga os pontos da dissertativa
no componente em que ela é cobrada.

A ordenação **"Maior impacto na nota"** lista as disciplinas por quantos pontos
ainda estão em aberto — é a fila de estudo com melhor retorno.

## Ocultar disciplina

O `⊘` ao lado de cada disciplina tira ela da conta. O percentual recalcula na
hora e o painel passa a dizer, em letras, sobre quantos subtópicos ele vale e
qual seria o número com tudo incluído — um percentual sem esse aviso seria
enganoso.

As disciplinas fora da conta ficam numa faixa logo abaixo dos filtros; clicar
nelas (ou em "Mostrar todas") traz de volta. Vale para qualquer disciplina.

Para que a escolha valha sempre, crie `editais/<id>/ocultas.json`:

```json
["Informática para todos os cargos"]
```

## Check manual

Clique no círculo ao lado de qualquer subtópico para marcar o que já estudou.
Cada clique percorre três estados:

| Estado | Marca | Significado |
|---|---|---|
| automático | ○ ◐ ✓ | o que o match decidiu |
| marcado | ✓ com anel | você disse que estudou |
| desmarcado | ○ com anel | você disse que **não** estudou |

A marcação manual **sempre vence** o match automático, e todos os números
(donut, barras, contadores) recalculam na hora.

**A marcação vale para todos os editais.** Os quatro concursos repetem assunto:
marcar "Fato gerador, sujeito ativo e passivo" em Caruaru marca o mesmo item em
Queimados. A marca é guardada pelo **texto** do subtópico, não pelo edital.

A exceção são textos curtos ou genéricos — "Características", "Normas gerais" —
que aparecem em disciplinas diferentes querendo dizer coisas diferentes. Esses
ficam presos ao subtópico onde você marcou. O critério é mecânico: menos de 20
caracteres ou menos de 3 palavras não propaga.

Passe o mouse sobre um subtópico para ver se ele vale em outros editais.

**As marcações sobrevivem a fechar o navegador** e a regerar o `index.html` —
ficam no armazenamento do navegador, que não é apagado quando o arquivo é
reescrito. Se o navegador estiver bloqueando o armazenamento (janela anônima,
por exemplo), o painel avisa em vermelho em vez de perder o seu trabalho em
silêncio.

Para torná-las permanentes de verdade — e sobreviverem a trocar de máquina ou
a limpar o navegador:

1. Clique em **Salvar marcações** (baixa um `marcas.json`)
2. Mova o arquivo para a pasta `_edital-tracker/`, **ao lado do `index.html`**
3. Duplo clique em `ATUALIZAR PAINEL.bat`

É **um arquivo só, para todos os editais** — não vai dentro da pasta de nenhum
concurso. A partir daí as marcações valem sempre, inclusive se você reprocessar
os editais, e aparecem também no resumo do terminal.

> Também continua funcionando um `manual.json` por edital, em
> `editais/<id>/manual.json`, para marcações que valham só naquele concurso.

## Reprocessar sem perder progresso

Os IDs dos subtópicos são hashes do texto. Rodar o Passo 1 de novo com o mesmo
edital preserva os IDs; o Passo 3 só avalia o que ainda está pendente.
Use `--refazer` para reavaliar tudo e `--sem-cache` para ignorar o cache local.

## Parâmetros que mais importam

| Flag | Onde | Efeito |
|---|---|---|
| `--min-confianca` | matcher | padrão 0.70. Subir = mais rigor, percentual menor |
| `--topk` | matcher | notas candidatas por subtópico (custo x recall) |
| `--lote` | matcher | subtópicos por chamada. Reduza se der `max_tokens` |
| `--modelo` | matcher | `claude-opus-5-5` (padrão) ou `claude-sonnet-5-5` (~metade do custo) |
| `--incluir-mocs` | scraper | inclui MOCs; por padrão ficam de fora (casariam com tudo) |
| `--vault` | dashboard | nome do vault para os links `obsidian://` funcionarem |

## Arquivos

| Arquivo | Papel |
|---|---|
| `edital_parser.py` | texto do edital → `edital.json` |
| `obsidian_scraper.py` | vault → `inventario.json` (somente leitura) |
| `semantic_matcher.py` | match via LLM → marca `concluido` |
| `dashboard_generator.py` | `edital.json` → `index.html` |
| `editais/<id>/` | um edital por pasta |
| `editais/<id>/info.json` | data da prova, discursiva, vagas (opcional) |
| `editais/<id>/manual.json` | seus checks manuais (opcional) |
| `editais/<id>/ocultas.json` | disciplinas fora da conta (opcional) |
| `data/` | inventário do vault e arquivos auxiliares |
| `.cache_matcher/` | cache de respostas do LLM (pode apagar) |
