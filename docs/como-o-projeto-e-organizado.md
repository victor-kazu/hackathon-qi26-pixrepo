# Como o projeto é organizado

Dentro de `src/` tem dez pastas. Para um programa que responde quatro
endereços, parece muita pasta — e no começo assusta mesmo.

Este texto explica por que elas existem, o que cada uma pode e não pode
fazer, e principalmente: **onde você mexe quando quer fazer alguma
coisa**.

Não precisa decorar. Leia uma vez para pegar o mapa e volte quando
estiver perdido.

---

## 1. O caminho de uma requisição

Toda requisição atravessa as mesmas camadas, sempre na mesma ordem.
Este é o `POST /pix/transaction`, que analisa um Pix:

```
  requisição chega
        ↓
  src/middlewares/     quatro camadas, de fora para dentro:
        ↓              identificador único da requisição → log de
        ↓              entrada → INTERNAL-TOKEN (sem ele, para aqui:
        ↓              403) → sessão de banco. A ordem está comentada
        ↓              em src/app.py, onde ela aparece de trás pra
        ↓              frente — o comentário de lá explica por quê
        ↓
  src/resources/       que endereço é esse? quem cuida dele?
        ↓              (o endereço → resource está escrito em src/app.py)
        ↓              (antes da 1ª linha da rota rodar, src/schemas/
        ↓               confere o JSON. Torto, para aqui: 400)
        ↓
  src/controllers/     pode fazer isso? é aqui que mora a regra
        ↓              (o motor antifraude, em controllers/fraud_engine/,
        ↓               é Python puro: recebe os sinais e devolve a decisão)
        ↓
  src/repositories/    a conversa com o banco — só aqui existe consulta
        ↓
  src/models/          a tabela, descrita em Python
        ↓
      banco
        ↓
  src/dtos/            traduz o objeto do banco no JSON da resposta
        ↓
  resposta sai
```

Repare no que isso compra: quando o código chega no **controller**, o
JSON já foi conferido. Quando chega no **repository**, a decisão já foi
tomada. Cada camada confia no trabalho da anterior e só cuida do seu
pedaço.

Abra os arquivos e siga o caminho uma vez. Vale mais que ler três vezes
este texto.

---

## 2. O que cada pasta pode e não pode

| Pasta | Pode | Não pode |
|---|---|---|
| `resources/` | receber a requisição, conferir o schema, chamar **um** controller, devolver a resposta | saber SQL, decidir regra de negócio |
| `schemas/` | dizer qual JSON é aceito na entrada, e recusar o que não é | falar com banco, decidir regra |
| `controllers/` | decidir o que pode e o que não pode, chamar repositories, salvar (`commit`) | escrever consulta, saber que existe HTTP |
| `repositories/` | buscar, criar e atualizar no banco | decidir se aquilo era permitido |
| `models/` | descrever as tabelas em Python | ter regra dentro |
| `dtos/` | transformar o objeto do banco no dicionário que vira a resposta | buscar coisa no banco, decidir regra |
| `errors/` | definir cada erro: código, mensagem e status HTTP | ter regra de negócio dentro |
| `middlewares/` | fazer algo em **toda** requisição (token, log, cabeçalho, identificador, sessão de banco) | conhecer uma rota específica |
| `connectors/` | chamar um serviço de fora: endereço, timeout e o desembrulho da resposta | decidir regra de negócio, falar com o nosso banco |
| `utils/` | ferramenta de uso geral — aqui, o logger e o identificador da requisição | virar o depósito do que não se sabe onde pôr |

Um exemplo do que isso significa na prática: em
`src/controllers/pix_transaction_controller.py` você lê
`if existing_analysis.pix_transaction.raw_payload != payload: raise PixIdConflict(...)`.
Essa frase é a regra do negócio (o mesmo `id` com outro conteúdo não é
retry), e ela mora no controller. Em
`src/repositories/pix_transaction_repository.py` não existe nenhuma frase
dessas: o repository conta e soma o histórico, e até os limites de cada
janela (24 h, 30 dias, R$ 200) chegam do controller como parâmetro. Quem
decide o que esses números significam é o motor.

### "E a conexão com o banco, em qual pasta ela mora?"

Em duas, e a divisão é o assunto mais interessante deste texto.

Quem fala com o banco fala por uma **sessão**: um rascunho onde as
mudanças ficam guardadas até alguém mandar salvar. Ela precisa nascer no
início da requisição e morrer no fim, sempre — inclusive quando a rota
explode no meio. Duas perguntas diferentes, então:

- **Quem cuida do ciclo de vida?** `src/middlewares/session_manager.py`.
  Abre, desfaz se deu errado, fecha sempre — e **nunca salva**.
- **Quem pede a sessão?** O controller, ao ser construído. O
  `BaseController` chama o `get_context()` de `src/database.py`, e é só
  isso: a rota não escreve nada sobre banco.

**O resource não fala de banco, de propósito.** Abra `src/resources/` e repare
no que não está lá: nenhuma menção a sessão, a `Session`, a
`Depends`. A rota recebe a requisição, chama o controller e devolve a
resposta. Quem precisa de banco é a regra de negócio, então é ela que
pede.

**Como a sessão chega lá, se ninguém a passa?** Pelo **contexto** — o
mesmo caminho por onde o `request_id` viaja (está explicado na seção do
log). O middleware deixa a sessão num lugar combinado no começo da
requisição; o controller pega de lá quando nasce. Middleware não
consegue *entregar* um objeto para quem vem depois, mas consegue
*deixar num lugar onde ele sabe procurar*.

**O combinado que isso exige.** Sessão no contexto é sessão no ar:
qualquer código, em qualquer camada, alcança o banco chamando
`get_context()`. Nada impede um DTO de fazer isso — nada além do
combinado, que é curto justamente pra caber na cabeça:

> **Quem chama `get_context()` é o controller, e mais ninguém.**

**Por que o ciclo virou middleware.** Dava para deixar tudo numa
*dependency* do FastAPI — e por um tempo foi assim. O motivo da troca
não é técnico: "onde a sessão de banco nasce e morre?" é uma pergunta
que se responde olhando a lista de middlewares, e é lá que as pessoas
procuram. Um projeto de estudo que ensina um caminho diferente do que se
encontra no trabalho ensina uma coisa a mais para desaprender depois.

**O que isso custou, e como o custo foi pago.** Middleware atende TODA
requisição, inclusive o `/health_check` que o Docker consulta a cada três
segundos — e seria ruim que o health check passasse a depender do banco
estar de pé. Por isso o middleware não abre nada: ele só deixa o lugar
preparado, e a sessão só nasce quando alguém pede. O `/` e o
`/health_check` não constroem controller nenhum, então continuam sem
tocar no banco.

O preço dessa preguiça é que a sessão passou a ser **opcional** — pode
não existir —, e todo `close` e todo `rollback` precisa perguntar antes
de agir. Esquecer essa pergunta derruba justamente as rotas que não usam
banco, com as rotas de banco seguindo verdes. Aconteceu enquanto este
middleware era escrito; a história está na docstring dele.

**E o `commit`?** Nunca é do middleware. Quem sabe se o trabalho terminou
é o controller, e é por isso que `self.session.commit()` é a última linha
antes do `return` do `analyze`, em `src/controllers/pix_transaction_controller.py`. O
middleware cuida do ciclo de vida; o controller decide o conteúdo.

Abra os dois arquivos na ordem — o middleware primeiro, o `database.py`
depois. São poucas linhas de código cada um, e juntas elas dizem o
combinado inteiro.

---

## 3. Onde eu mexo quando quero...

| Quero... | Mexo em | Na ordem |
|---|---|---|
| **aceitar um campo novo** no JSON de entrada | `src/schemas/post_pix_transaction.json` (e `pix_definitions.json`, para as partes repetidas) | se o campo vai para o banco, também `database/database.sql` e `src/models/` |
| **mudar o que a resposta devolve** | `src/dtos/pix_analysis_dto.py` | é o único lugar; se o campo ainda não existe no banco, antes disso `database/database.sql` e `src/models/` |
| **criar uma rota nova** numa entidade que já existe | `src/resources/pix_transaction.py` | registrar o endereço em `src/app.py`, e o método no controller se a regra for nova |
| **mudar uma regra** ("não pode X") | `src/controllers/pix_transaction_controller.py` | e um erro novo em `src/errors/custom_errors.py`, se precisar |
| **mudar uma regra antifraude** ou um limite | `src/controllers/fraud_engine/rules.py` e `parameters.py` | se a regra precisa de um dado novo, antes `signals.py` e o repository; depois o teste em `tests/unit/fraud_engine/` |
| **consultar o banco de outro jeito** (filtrar, ordenar, contar) | `src/repositories/pix_transaction_repository.py` | o controller chama o método novo |
| **criar uma tabela** | `database/database.sql` | depois `src/models/` e o `__init__.py` da pasta |
| **criar uma entidade inteira** (rota + regra + tabela) | um arquivo em cada pasta | `database.sql` → `models/` → `repositories/` → `controllers/` → `schemas/` → `resources/` → registrar em `src/app.py` |
| **fazer algo em toda requisição** | `src/middlewares/` | registrar em `src/app.py` |
| **chamar outro serviço** | um connector novo em `src/connectors/`, herdando o `rest_connector.py` | quem chama o connector é o controller, nunca o resource |

Quatro armadilhas que pegam quase todo mundo. A segunda e a terceira
custam caro pelo mesmo motivo: a mensagem de erro aponta para o
sintoma, não para a causa. A quarta é pior ainda — nela não vem
mensagem nenhuma.

**Criou um arquivo e o Python diz que não existe?** Cada pasta tem um
`__init__.py` que lista o que ela oferece. Abra o da pasta (por exemplo
`src/repositories/__init__.py`) e acrescente a sua linha.

**E a ORDEM das linhas do `src/models/__init__.py` importa.** Ele lista
o `PixAnalysisStatus` e o `PixTransaction` **antes** do `PixAnalysis`, e o
`PixAnalysisRuleHit` por último: de quem não depende de ninguém para quem
depende. Aqui os models importam direto do arquivo
(`from models.pix_transaction import PixTransaction`), o que já evita o
problema. Mas se um model novo importar do pacote (`from models import
PixTransaction`) e o `__init__.py` listar ele antes do que ele usa, o
Python responde:

```
ImportError: cannot import name 'PixTransaction' from partially
initialized module 'models' (most likely due to a circular import)
```

A mensagem culpa uma "importação circular", que não é bem o que
aconteceu — e você vai procurar um ciclo que não existe. A regra é
simples: **entidade nova que se relaciona com outra entra no
`__init__.py` depois daquela de que ela depende.**

**Mexeu no `database/database.sql` e nada mudou?** Aquele arquivo roda
uma vez só: **quando o banco nasce**. Depois disso o Postgres nunca mais
olha para ele — nem no `docker compose up` seguinte, nem num
`docker compose restart db`. O sintoma aparece longe daí, na resposta da
API ou no log:

```
psycopg2.errors.UndefinedTable: relation "minha_tabela" does not exist
```

Ela não diz "seu SQL não rodou": diz que a tabela não existe. Dá vontade
de reler o SQL procurando erro de digitação, e não tem nenhum.

Para o banco nascer de novo, com o schema novo:

```bash
docker compose down -v
docker compose up
```

O `-v` é o que apaga o volume, o disco do banco — e **ele leva junto
tudo que você criou na mão** até aqui. Não tem meio-termo: ou o banco
nasce de novo com o schema novo, ou continua com o antigo. Em sistema de
verdade ninguém apaga o banco, claro: lá a mudança de schema entra por
um comando aplicado no deploy, e é por isso que este projeto guarda o
schema num arquivo versionado em vez de deixá-lo só dentro do banco.

**Mexeu numa dependência?** Então preste atenção, porque agora ela mora
em dois lugares — e eles não se falam.

As dependências da aplicação são instaladas **dentro da imagem**, quando
o Docker constrói a API. As de teste ficam no **seu `.venv`**, na sua
máquina. Acrescentar uma linha no `requirements.txt` não avisa nenhum dos
dois: o container continua com a imagem que ele já tinha, e o seu `.venv`
continua com o que você instalou da última vez.

O sintoma é enganoso. Se a biblioteca nova é usada pelo **código da API**,
os testes quebram com um erro que parece de teste, mas vem do container.
Se é usada só pelos **testes**, tudo passa na sua máquina e quebra na de
quem clonar o projeto.

Mexeu no `requirements.txt`, reconstrua a imagem:

```bash
docker compose build --no-cache api
docker compose up -d
```

Aqui o `--no-cache` é cinto e suspensório: o `docker compose build api`
sozinho já bastaria. O `Dockerfile` copia o `requirements.txt` ANTES de
rodar o `pip install` (linhas 11-14) justamente para isso — mudou o
arquivo, a camada do `pip` deixa de valer e ele reinstala. O
`--no-cache` refaz a imagem INTEIRA, inclusive o que não mudou; use
quando desconfiar da imagem, não toda vez.

Mexeu no `requirements-dev.txt`, reinstale no seu ambiente:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Esse é o preço de rodar os testes fora do Docker: você ganha um ciclo
mais rápido e perde a garantia de que a sua máquina e a imagem estão
sincronizadas. Quem clona o projeto do zero faz os dois passos; quem já
tem tudo de pé precisa lembrar de qual dos dois arquivos mudou.

---

## 4. A regra que explica todas as outras

A seta anda num sentido só:

```
resources → controllers → repositories → models
```

Um resource pode chamar um controller. Um controller pode chamar um
repository. **O contrário nunca acontece** — repository não chama
controller, model não sabe que existe rota.

Por que isso importa, em três respostas concretas:

- **Você acha o problema mais rápido.** Erro no formato do JSON? É
  `schemas/`. Salvou o que não devia? É `controllers/`. Você já começa
  a procurar no lugar certo.
- **Trocar uma peça não derruba as outras.** Este projeto já foi
  reescrito de um framework para outro, e os testes não mudaram — porque
  quem conhecia o framework era só uma camada.
- **Três pessoas trabalham juntas sem colidir.** Uma mexe na regra,
  outra na consulta, outra na rota. Arquivos diferentes, sem pisar no pé
  um do outro.

Quando você estiver com pressa, vai dar vontade de escrever a consulta
direto no resource. Funciona. E é exatamente assim que um projeto vira
aquele em que ninguém mais encontra nada.

---

## 5. Schemas e DTOs: o que entra e o que sai

Duas pastas falam de formato, e é fácil confundi-las. A divisão é a
direção: **o `schemas/` cuida do que ENTRA, o `dtos/` cuida do que
SAI.**

- `src/schemas/post_pix_transaction.json` descreve o JSON que o cliente
  manda. Quem lê isso é o `jsonschema`, antes da primeira linha da rota
  rodar: campo faltando, tipo errado ou campo a mais viram 400 ali
  mesmo. Quem aciona a conferência é o decorator
  `@SchemaHandler.validate("post_pix_transaction.json")` na rota, e o
  código dele está em `src/utils/schema_handler.py`.

  **Por que um `.json` e não uma classe Python?** O FastAPI validaria
  sozinho, com uma classe herdando de `BaseModel` — e por um tempo foi
  assim aqui. A troca não é técnica: nos serviços da QI Tech o contrato
  de entrada é um arquivo escrito em **JSON Schema**, um padrão que
  existe fora do Python e que quem integra com a API consegue ler sem
  abrir o repositório. O preço é que o corpo chega como dicionário:
  `payload["amount"]` em vez de `payload.amount`.
- `src/dtos/pix_analysis_dto.py` faz o caminho de volta. O repository
  entrega o objeto do banco; o DTO devolve um dicionário simples, e é
  esse dicionário que vira o JSON da resposta.

Abra os dois ao lado de `src/models/pix_analysis.py` e a diferença
fica óbvia. Na **tabela**, o status é um número apontando para outra
tabela e o `pix_id` mora em outra tabela, a `pix_transaction`. Na
**resposta**, o status é texto (`approved`, `two_factor_required`,
`rejected`) e o `pix_id` aparece dentro do `metadata`. Quem faz
essa travessia é o DTO, e é por isso que ele existe.

Campo novo na resposta? Acrescente no `dtos/`. Nenhum outro arquivo
precisa saber.

Uma consequência que vale conhecer: **o que o DTO monta é exatamente o
que sai.** Não existe ninguém depois dele conferindo a forma — se um
campo interno entrar naquele dicionário, ele vai para o cliente do
mesmo jeito. Quando aparecer na resposta um dado que você não queria
mostrar, comece procurando aqui.
