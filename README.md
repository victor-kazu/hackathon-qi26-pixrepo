# Grupo 3

**Integrantes:** [nomes]

Motor antifraude de Pix: uma API que recebe um Pix, compara com o histórico de quem paga, de quem recebe e do aparelho, e decide em poucos milissegundos se ele sai (`approved`), se o cliente precisa confirmar que é ele (`two_factor_required`) ou se é bloqueado (`rejected`). Toda decisão sai com o motivo, as regras acionadas e os sinais que as acionaram.

## A ideia central: o 2FA só resolve metade das fraudes

O 2FA prova que **quem está no app é o dono da conta**. Ele protege contra **tomada de conta**: celular roubado, golpista logado em outro aparelho, sessão clonada.

Ele **não** protege contra **golpe de engenharia social** (falso parente, falsa central, falso vendedor) nem contra **coação**. Nesses casos quem faz o Pix é a própria vítima, com o próprio celular, e ela passa no 2FA sem pensar duas vezes. Pedir 2FA aí é fricção que não protege ninguém.

Por isso o motor mede o risco em duas dimensões separadas, e cada uma leva a uma decisão diferente:

| Dimensão | Pergunta | Exemplos de sinal | Quando está alta |
|---|---|---|---|
| **Identidade** | É mesmo o cliente quem está pagando? | aparelho novo, viagem impossível, horário noturno, rajada de tentativas | pede **2FA** |
| **Destino** | Para onde vai esse dinheiro? | conta destino recém-aberta, funil de vários pagadores, destino já barrado, valor fora do padrão | **rejeita**, porque o 2FA não ajudaria |

## Como o motor de decisão funciona

```
POST /pix/transaction
   │
   ▼
resource ── valida o formato (JSON Schema)
   │
   ▼
controller ── valida o conteúdo (CPF/CNPJ, datas)  ── mesmo id já analisado? devolve a mesma análise (409 se o conteúdo mudou)
   │          trava a conta pagadora (lock por conta)
   │
   ├──► repository ── 16 consultas de histórico, todas "até a data deste Pix"
   │                  (pagador, destino, par pagador→destino, aparelho, IP)
   ▼
engine/signals.py ── monta os sinais (idade das contas, hora local, mediana, velocidade geográfica...)
engine/rules.py   ── 25 regras: 6 duras, 17 de pontos, 2 bônus de confiança
engine/policy.py  ── soma identidade e destino separadamente e decide
   │
   ▼
grava pix_transaction + pix_analysis + uma linha por regra acionada ── commit ── responde
```

O pacote `engine/` é Python puro: não conhece banco nem HTTP. É isso que permite testar cada regra em unidade, em milissegundos, e reproduzir uma decisão a partir da metadata gravada.

Princípios que guiaram as regras:

- **Ausência de dado é neutra, nunca suspeita.** Sem GPS, sem histórico, primeiro Pix: o motor troca de fonte de evidência, não pune. No cold start o aparelho é "desconhecido", não "novo".
- **Uma anomalia fraca sozinha nunca rejeita.** Nenhuma regra de pontos vale 90; rejeitar por score exige combinação de sinais.
- **O perfil só aprende com Pix aprovados.** Tentativas barradas do fraudador não viram o "normal" do cliente. Já a velocity conta **todas** as tentativas, porque tentar de novo depois de ser barrado também é sinal.
- **Mediana, não média.** Um único Pix grande (a compra do carro) não muda o padrão do cliente.
- **O tempo do Pix, não o do servidor.** Todo histórico é lido "até `transaction_date`", então um Pix nunca é julgado com informação que só existiu depois dele.
- **Parâmetros ancorados em norma quando ela existe.** O limite noturno e o limite de aparelho novo seguem as regras do Banco Central para o Pix.

## Regras

Valores em reais; no código ficam em centavos, em `src/engine/parameters.py`, cada um com o porquê ao lado. Janelas sempre terminam no `transaction_date` do Pix analisado.

### Regras duras (rejeitam sozinhas)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `VELOCITY_BOT` | Tentativas da mesma conta pagadora em 5 min, contando a atual e qualquer status | ≥ 5 | Nenhum cliente digita chave, valor e senha cinco vezes em cinco minutos. É script ou alguém raspando o saldo. |
| `DEVICE_FARM` | CPFs/CNPJs pagadores distintos que usaram o mesmo `device_id` em 30 dias | ≥ 3 | Dois pode ser um casal dividindo o celular; três já é central de golpe operando contas compradas. |
| `NIGHT_HARD_LIMIT` | Pix de PF entre 20h e 6h (hora local do cliente) | > R$ 5.000 | De noite o risco maior é coação (sequestro relâmpago), e sob coação a vítima também passa no 2FA. |
| `DAILY_HARD_LIMIT` | Soma de aprovados + 2FA em 24 h, mais o Pix atual | PF > R$ 20.000; PJ > R$ 200.000 | Contenção de dano se tudo o mais falhar: celular destravado esvazia a conta em minutos. |
| `NEW_DEVICE_DAILY_LIMIT` | Soma em 24 h feita de um aparelho novo, mais o Pix atual | > R$ 1.000 | Mesmo número do limite diário para aparelho novo da Res. BCB 402/2024. |
| `IMPOSSIBLE_TRAVEL_OTHER_DEVICE` | Distância Haversine até o último Pix com GPS (até 24 h) ÷ tempo, e o aparelho é outro | > 1.000 km/h e > 100 km | Dois aparelhos em cidades distantes ao mesmo tempo é credencial clonada. |

### Identidade (2FA resolve)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `NIGHT_HIGH_VALUE` | Pix de PF entre 20h e 6h | > R$ 1.000 → +40 | Limite noturno padrão de PF da Res. BCB 142/2021. O cliente pode ter aumentado o limite, o que o motor não enxerga, então pede 2FA em vez de barrar. 40 = pede 2FA sozinho. |
| `NEW_DEVICE` | A conta tem histórico, mas este aparelho não tem Pix aprovado nela há mais de 24 h | +30; +15 se > R$ 200 | Tomada de conta quase sempre começa em outro aparelho. As 24 h de maturação impedem "esquentar" o aparelho com um Pix de R$ 1. R$ 200 é o limite por Pix de aparelho novo do BC. |
| `VELOCITY_BURST` | Tentativas em 5 min (ou em 1 h) | 3 em 5 min → +25; 4 → +40; 10 em 1 h → +20 | Rajada pode ser dividir a conta do restaurante, por isso pesa antes de barrar. Só a janela mais forte conta, para não pontuar duas vezes o mesmo fenômeno. |
| `PROBING_AFTER_REJECTION` | Pix rejeitados desta conta na última hora | +20 por rejeição, máx. +40 | Tentar de novo logo depois de ser barrado é o fraudador procurando o valor que passa. |
| `IMPOSSIBLE_TRAVEL` | Igual à regra dura, mas no mesmo aparelho | +45 | No mesmo aparelho pode ser GPS falsificado ou VPN, então pede confirmação em vez de barrar. |
| `NEW_SOURCE_ACCOUNT` | Conta pagadora recém-aberta movimentando ≥ R$ 1.000 | < 2 dias → +35; < 7 dias → +25 | Identidade sintética ou conta aberta só para girar dinheiro de golpe. |
| `SPLIT_PAYMENT` | Pix desta conta para o **mesmo destino** em 1 h (aprovados + 2FA), contando o atual. Cada pedaço abaixo do limite, a soma acima | ≥ 3 pedaços → +40. Limite: R$ 1.000 à noite (PF); R$ 5.000 de dia (PJ: R$ 50.000) | Partir o valor para passar por baixo do limite noturno é o drible clássico (3 x R$ 990 às 23h). A soma é tratada como o Pix que ela forma, e pesa o mesmo que o limite pesaria: pede 2FA. |
| `SHARED_DEVICE` | 2 pagadores distintos no mesmo aparelho em 30 dias | +15 | Sinal fraco por si só (família), mas soma com outros. |
| `SHARED_IP` | Pagadores distintos no mesmo IP em 24 h | ≥ 5 → +10 | IP é sinal fraco: operadora põe milhares de clientes atrás do mesmo IP (CGNAT). |

### Destino (2FA não resolve)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `NEW_DESTINATION_ACCOUNT` | Idade da conta destino na data do Pix (não vale para mesma titularidade) | < 7 dias → +30; < 30 dias → +15 | Conta laranja nasce para receber um golpe e é abandonada em dias. |
| `DESTINATION_FAN_IN` | Pagadores distintos para a mesma conta destino em 24 h, contando o atual | PF: ≥ 5 → +35, ≥ 10 → +50. PJ: limiares 10x | Muitos CPFs pagando a mesma pessoa física numa tarde é golpe em massa em andamento. Comércio (CNPJ) recebe de muita gente por natureza. |
| `FLAGGED_DESTINATION` | **Outros** pagadores distintos que tiveram Pix para este destino rejeitado por risco do destino (destino ≥ 60) nos últimos 30 dias | ≥ 1 → +35; ≥ 3 → +50 | Reputação de rede: se alguém já foi barrado pagando essa conta, a próxima vítima está a caminho. Só conta rejeição causada pelo destino (o celular roubado tentando pagar a mãe da vítima não suja a conta da mãe) e conta pessoas, não tentativas, para que ninguém consiga marcar a conta de um inocente insistindo sozinho. É pontuação com janela, e não blacklist permanente, porque uma lista dura se alimenta sozinha: a conta marcada passa a ter tudo rejeitado e nunca sai. Uma lista de fraude confirmada (analista ou marcação de fraude do BC no DICT) seria a evolução, mas exige uma rota de cadastro fora do escopo. |
| `PROFILE_AMOUNT_SPIKE` | Valor ÷ mediana dos aprovados em 90 dias (piso de R$ 100). Só com ≥ 5 Pix no histórico | > 3x → +20; > 10x → +35 | A vítima de golpe manda um valor fora do seu padrão. Com menos de 5 Pix não existe padrão, e a regra fica neutra. |
| `HIGH_VALUE_WITHOUT_PROFILE` | Valor alto de quem ainda não tem perfil (menos de 5 Pix aprovados em 90 dias); não vale para mesma titularidade | PF ≥ R$ 5.000 → +20 (PJ ≥ R$ 50.000) | Sem histórico não dá para dizer que o valor é "fora do padrão", mas é onde a fraude dói. Pesa moderado: primeiro Pix de valor baixo continua sem ponto, e o valor alto só chega ao 2FA junto de outro sinal (acima de R$ 10 mil, o `DAILY_EXPOSURE` já soma). |
| `STRUCTURING` | Pix de valor idêntico desta conta na última hora, contando o atual | ≥ 3 → +20 | Fracionar uma quantia em partes iguais para ficar abaixo dos tetos. Se os pedaços vão para o mesmo destino, quem pontua é o `SPLIT_PAYMENT`, para não contar duas vezes. |
| `DAILY_EXPOSURE` | Soma de 24 h acima da metade do teto diário | +20 | Avisa antes do teto duro. |
| `NEW_COUNTERPARTY` | Primeiro Pix desta conta para este destino | > R$ 500 → +10 | Todo mundo paga alguém pela primeira vez, então é fraco e só soma a outros sinais. |

### Bônus de confiança

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `TRUSTED_COUNTERPARTY` | Pix aprovados para este destino e há quanto tempo foi o primeiro | ≥ 2 Pix e ≥ 7 dias → −30 | É o aluguel, a diarista, o fornecedor. É o que impede o motor de travar quem só quer pagar as contas. |
| `SELF_TRANSFER` | Mesmo CPF/CNPJ dos dois lados | −25 | O dinheiro não sai da pessoa. |

Os bônus só reduzem a própria categoria e nunca a deixam negativa: confiar no destino não "paga" a dúvida sobre a identidade.

## Como as regras viram decisão

É um sistema de pontuação em duas camadas.

1. **Regras duras** não somam pontos. Se uma delas dispara, o Pix é `rejected` na hora, seja qual for o resto (ex.: 5ª tentativa em 5 minutos, Pix de pessoa física acima de R$ 5.000 à noite).
2. **Regras de pontuação** somam pontos em uma de duas contas separadas:
   - **identidade**: é mesmo o cliente quem está pagando? O 2FA resolve essa dúvida.
   - **destino**: para onde vai o dinheiro? O 2FA não resolve, porque no golpe de engenharia social quem paga é o próprio cliente, e ele passa no 2FA.

   Os bônus de confiança (destino que o cliente já paga há tempo, Pix para conta do mesmo titular) são pontos negativos dentro da sua conta, que nunca fica abaixo de zero.

Com as duas contas fechadas, a tabela é lida de cima para baixo e a primeira linha que bate decide:

| Ordem | Condição | Status | Racional |
|---|---|---|---|
| 1 | Alguma regra dura disparou | `rejected` | Risco claro, definido por política explícita. |
| 2 | Risco de **destino** ≥ 60 | `rejected` | O 2FA não protege vítima de golpe. 60 é a assinatura do "falso parente": conta destino com menos de 7 dias (30) + valor 3x acima do padrão (20) + primeiro Pix para ela (10). |
| 3 | Risco total (identidade + destino) ≥ 90 | `rejected` | A soma de sinais independentes já é evidência, não dúvida. Nenhuma regra sozinha chega a 90. |
| 4 | Risco total ≥ 40 | `two_factor_required` | Dúvida moderada: o cliente confirma que é ele. Exige pelo menos um sinal forte ou dois moderados. |
| 5 | O resto | `approved` | Sem fricção. |

O Pix de exemplo do enunciado (R$ 1.500 às 22h15 para uma conta aberta há 7 dias) dá `two_factor_required`: limite noturno (+40 identidade), conta destino com menos de 30 dias (+15) e primeiro Pix para ela (+10), total 65.

Um exemplo para cada desfecho:

| Pix | Pontos | Status |
|---|---|---|
| R$ 300 às 14h para a diarista, que recebe dele há meses | nenhuma regra de risco; bônus de −30 no destino, que fica em 0 | `approved` |
| R$ 1.500 às 22h15 para conta aberta há 7 dias (o exemplo do enunciado) | identidade 40 + destino 25 = 65 | `two_factor_required` |
| R$ 3.000 às 15h, no aparelho de sempre, de um cliente que costuma fazer Pix de R$ 500, para conta aberta há 3 dias | identidade 0; destino 30 (conta nova) + 20 (6x o padrão) + 10 (primeiro Pix) = 60 | `rejected` pela linha 2, mesmo com identidade limpa |
| R$ 800 de um aparelho novo, 3ª tentativa em 5 minutos, para conta aberta há 20 dias | identidade 45 (aparelho novo acima de R$ 200) + 25 (rajada); destino 15 + 10 = 95 | `rejected` pela linha 3 |
| R$ 6.000 às 23h | regra dura do teto noturno | `rejected` pela linha 1 |

A resposta segue o contrato e explica a decisão na `metadata`:

```json
{
  "key": "5170cbfc-f08a-43c5-bca6-87956cce16db",
  "status": "two_factor_required",
  "metadata": {
    "risk_score": 65,
    "identity_score": 40,
    "beneficiary_score": 25,
    "decision_reason": "Dúvida moderada (65 pontos, 2FA a partir de 40): o cliente confirma que é ele antes do Pix sair.",
    "triggered_rules": [
      {"rule": "NIGHT_HIGH_VALUE", "category": "identity", "points": 40, "hard": false,
       "detail": "R$ 1.500,00 às 22h, acima do limite noturno de R$ 1.000,00"},
      {"rule": "NEW_DESTINATION_ACCOUNT", "category": "beneficiary", "points": 15, "hard": false,
       "detail": "conta destino aberta há 7,6 dias"},
      {"rule": "NEW_COUNTERPARTY", "category": "beneficiary", "points": 10, "hard": false,
       "detail": "primeiro Pix para este destino, acima de R$ 500,00"}
    ],
    "signals": {"local_hour": 22, "is_night": true, "destination_account_age_days": 7.55, "...": "..."},
    "thresholds": {"two_factor_required": 40, "rejected": 90, "rejected_by_beneficiary": 60},
    "policy_version": "2026.10.17-v1",
    "processing_time_ms": 21.4,
    "pix_id": "pix-000123"
  }
}
```

## Modelagem de dados

`database/database.sql` cria quatro tabelas:

| Tabela | O que guarda | Por quê |
|---|---|---|
| `pix_transaction` | Cada Pix recebido, com os campos do contrato em colunas tipadas e o corpo original em `JSONB` | As regras consultam por coluna (conta, aparelho, data); o JSON fica para auditoria. Valor em `BIGINT` de centavos, datas em `TIMESTAMPTZ` e o fuso do cliente em `utc_offset_minutes`, porque o `TIMESTAMPTZ` guarda o instante e perde o fuso. |
| `pix_analysis` | A decisão (1:1 com o Pix): `analysis_key`, status, os três scores, motivo, versão da política, tempo de processamento e a metadata completa | O fato que chegou e o que o motor concluiu são coisas diferentes. A versão da política gravada permite explicar uma decisão antiga com a regra que valia no dia. |
| `pix_analysis_status` | `approved`, `two_factor_required`, `rejected` | Tabela de domínio, no padrão do Base Service. |
| `pix_analysis_rule_hit` | Uma linha por regra acionada, com pontos e detalhe | Permite perguntar ao banco "qual regra mais gera 2FA?" sem abrir JSON. |

Índices compostos seguem exatamente as perguntas das regras, com a data por último para a janela virar *range scan*: `(source_branch, source_account_number, transaction_date)`, `(destination_participant, destination_branch, destination_account_number, transaction_date)`, `(device_id, transaction_date)` e `(ip, transaction_date)`.

Duas garantias de consistência:

- **Idempotência.** `pix_id` é único. O mesmo `id` chegando de novo (retry de rede, clique duplo) devolve a análise já gravada com HTTP 200, em vez de analisar de novo, o que daria outra decisão porque o primeiro Pix já estaria contando na velocity. Retry é o mesmo Pix: o mesmo `id` com outro conteúdo devolve 409, comparando com o payload original gravado em `raw_payload`. Sem isso, bastaria reaproveitar o `id` de um Pix aprovado para "aprovar" outro valor ou outro destino.
- **Concorrência.** O controller pega um `pg_advisory_xact_lock` pela conta pagadora antes de ler o histórico. Sem isso, cinco Pix simultâneos da mesma conta leriam o mesmo histórico vazio e passariam todos pela velocity. Contas diferentes continuam em paralelo.

Validações que devolvem 422: CPF/CNPJ com dígito verificador inválido, data que não existe no calendário, conta aberta depois do Pix e Pix com data no futuro (mais de 2 min), que serviria para plantar histórico adiantado.

## Simulação: como calibramos

`scripts/simulate.py` gera 60 dias de histórico de 150 clientes comuns (mercado, amigos, aluguel) e, no último dia, mistura o uso normal com sete tipos de ataque. Tudo é enviado para a API em ordem cronológica. Resultado com `--seed 42` (3.968 Pix, medindo só o último dia):

| Cenário | Pix | Aprovado | 2FA | Rejeitado |
|---|---:|---:|---:|---:|
| Clientes legítimos | 328 | 98,2% | 1,8% | 0,0% |
| Tomada de conta (aparelho novo) | 24 | 0,0% | 12,5% | 87,5% |
| Celular roubado à noite | 24 | 0,0% | 37,5% | 62,5% |
| Golpe para conta laranja | 16 | 0,0% | 0,0% | 100,0% |
| Robô em rajada | 64 | 37,5% | 12,5% | 50,0% |
| Fracionamento | 32 | 28,1% | 40,6% | 31,2% |
| Fazenda de aparelhos | 6 | 33,3% | 0,0% | 66,7% |
| Fracionamento noturno (3 x ~R$ 980 para a mesma conta) | 24 | 66,7% | 33,3% | 0,0% |

73% das tentativas de fraude foram paradas (2FA ou rejeição) e 85% do valor fraudado não passou direto, com 1,8% de clientes bons desafiados e nenhum rejeitado. Latência do motor: p50 de 23 ms e p99 de 34 ms.

No fracionamento noturno, todo terceiro pedaço caiu no 2FA pelo `SPLIT_PAYMENT`; os dois primeiros são Pix normais e passam, e é por isso que a taxa geral cai em relação à versão anterior da simulação, sem nenhum cenário antigo ter piorado.

A simulação mudou dois parâmetros: com o corte de destino em 70, dois terços dos golpes para conta laranja só pediam 2FA (que a vítima passaria), e ele caiu para 60; a 4ª tentativa em 5 min passou a pedir 2FA sozinha. Nenhuma das duas mudanças aumentou a fricção dos clientes legítimos.

O que sobra aprovado é estrutural: os primeiros Pix de um robô ou de um fracionamento são indistinguíveis de uso normal, e o padrão só aparece a partir do terceiro.

**Limites honestos.** Os rótulos de fraude são do próprio script: isso mede a coerência da política contra ataques conhecidos, não precisão real. Para medir precisão e recall seria preciso fraude confirmada em produção e o resultado dos desafios 2FA, que o enunciado não fornece. Com esses dados, os próximos passos seriam medir a taxa de falso positivo por regra (a tabela `pix_analysis_rule_hit` já permite) e ajustar pesos por segmento (cliente novo vs. recorrente).

## Como rodar e testar

Requisitos: Docker e Python 3.11 ou 3.12 para os testes. No Python 3.13 o SQLAlchemy 2.0.23 fixado no requirements não importa.

```bash
# 1. Sobe a API (porta 3000) e o PostgreSQL (porta 5432)
docker compose up --build

# 2. Em outro terminal, instala as dependências de teste
python -m venv .venv
source .venv/bin/activate        # no Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. Roda os testes (unidade + integração contra a API de pé)
pytest

# 4. Opcional: simulação com clientes bons e ataques
python scripts/simulate.py
```

Os testes de unidade (`tests/unit/`) testam as regras e a política sem banco nem API. Os de integração (`tests/integration/`) mandam Pix de verdade para a API: contrato, validações, idempotência e um cenário de ponta a ponta para cada tipo de fraude e de cliente bom. Cada teste usa contas e aparelhos aleatórios, então nenhum herda o histórico de outro.

Exemplo de chamada (a rota exige o header `INTERNAL-TOKEN`; o valor padrão é `default_token`):

```bash
curl -X POST http://localhost:3000/pix/transaction \
  -H "INTERNAL-TOKEN: default_token" -H "Content-Type: application/json" \
  -d '{
    "id": "pix-000123",
    "amount": 150000,
    "transaction_date": "2026-10-17T22:15:00-03:00",
    "source_account": {"branch": "0001", "account_number": "88213",
      "opening_date": "2025-03-02T10:00:00-03:00", "owner": {"document_number": "123.456.789-09"}},
    "destination_account": {"participant": "60701190", "branch": "3675", "account_number": "10442",
      "opening_date": "2026-10-10T09:00:00-03:00", "owner": {"document_number": "987.654.321-00"}},
    "source": {"device_id": "dvc-9f2c71a8", "channel": "app", "ip": "198.51.100.23",
      "geolocation": {"latitude": -23.56, "longitude": -46.65}}
  }'

curl http://localhost:3000/pix/transaction/pix-000123 -H "INTERNAL-TOKEN: default_token"
```

| Rota | Resposta |
|---|---|
| `POST /pix/transaction` | 201 com a análise; 200 se o `id` já foi analisado com o mesmo conteúdo; 409 se foi analisado com outro conteúdo; 400 para payload fora do schema; 422 para documento ou data inválidos |
| `GET /pix/transaction/{id}` | 200 com a análise gravada; 404 se o Pix não existe |

## Estrutura

```
src/
  engine/          o motor: parameters.py (números e porquês), signals.py, rules.py, policy.py
  controllers/     pix_transaction_controller.py: valida, coleta histórico, decide, grava
  repositories/    pix_transaction_repository.py: as consultas de janela
  resources/       pix_transaction.py: as duas rotas
  models/ dtos/ schemas/ errors/ middlewares/   camadas do Base Service
database/database.sql
scripts/simulate.py
tests/unit/engine/                 regras e política, sem banco
tests/integration/pix_transaction/ contrato e cenários de fraude pela API
docs/                              guia original do Base Service
```
