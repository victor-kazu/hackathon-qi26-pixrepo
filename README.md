# [Nome do grupo]

**Integrantes:** [nomes]

Motor antifraude de Pix: uma API que recebe o pedido de Pix e responde `approved`, `two_factor_required` ou `rejected`, sempre com o motivo. Construído a partir do Base Service do bootcamp.

## Como o motor de decisão funciona

O desafio é achar o *sweet spot*: barrar a fraude sem travar quem só quer pagar o aluguel. Três ideias guiam o desenho.

**1. O motor só enxerga o que de fato tem.** O payload traz valor, horário com fuso, as duas contas (com data de abertura e documento) e a origem (aparelho, canal, IP, localização). Sinais clássicos como chamada ativa, acessibilidade ligada ou SIM novo não vêm no contrato, então não fingimos usá-los. A profundidade vem do **histórico que gravamos**: cada Pix que chega fica guardado, e as regras perguntam a esse histórico.

- Quantos Pix este pagador fez nos últimos 10 minutos?
- Qual o valor típico dele?
- Ele já pagou este recebedor antes?
- Quantas pessoas usaram este aparelho?
- Quantos pagadores diferentes esta conta recebeu hoje?

**2. Três tipos de regra, combinados.**

- **Duras:** decidem sozinhas. Ficam para obrigação regulatória (o limite noturno do BCB) ou para padrões sem explicação legítima (10 Pix em 10 minutos, um aparelho com 3 pagadores).
- **De pontos:** somam risco. Nenhum sinal fraco reprova sozinho; vários juntos, sim.
- **De confiança:** pontos **negativos**. É o que faz o cliente com histórico bom não cair em 2FA por um valor um pouco acima do normal.

**3. Toda decisão é explicável e reconstruível.** O `metadata` da resposta traz:

- cada regra que disparou;
- os pontos dela;
- o dado exato que ela viu e o limite contra o qual comparou;
- o score total;
- quem decidiu (`score` ou o código da regra dura);
- a versão da política.

Tudo isso fica gravado (`pix_rule_hit`). Meses depois, uma contestação do MED pode ser respondida com o que o motor sabia naquele instante.

```text
POST /pix/transaction
  │
  ├─ schema JSON (formato) ─────────────── 400
  ├─ CPF/CNPJ e datas que existem ──────── 422
  ├─ mesmo id já analisado? ────────────── devolve a análise gravada (200) · id com outro conteúdo: 409
  ├─ TRAVA O PAGADOR (SELECT … FOR UPDATE)
  ├─ lê o histórico até o instante do Pix (point-in-time) → sinais
  ├─ roda as 15 regras → score + regras duras → decisão
  └─ grava Pix + análise + regras disparadas, commit ─── 201 {key, status, metadata}
```

As regras são funções puras em `src/fraud/`. Elas não falam com banco nem com HTTP: recebem um retrato pronto dos sinais (`Signals`) e devolvem o que viram. Quem monta o retrato é o controller, com as consultas do repository. É isso que deixa cada limiar testável isoladamente (`tests/unit/`).

## Regras

Todos os números moram em `src/fraud/parameters.py`, com a versão da política (`RULES_VERSION`) gravada em cada análise. Valores em centavos, como o `amount` do contrato.

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| **R1 · Limite noturno** (`NIGHT_LIMIT`) | Pagador **CPF**, das 20h às 6h no horário local do Pix (o fuso vem no `transaction_date`), valor acima do teto | R$ 1.000 · **reprova** (dura) | Regra do BCB para pessoa física. Cumprir regulação não é calibragem, por isso nenhum ponto de confiança a compensa. Não aplicamos a CNPJ: a regra também vale para MEI, mas um CNPJ sozinho não diz se é MEI, e punir toda empresa seria fricção sem base |
| **R2 · Valor alto sem precedente** (`HIGH_AMOUNT`) | Valor acima do teto **e** acima do maior Pix já aprovado deste pagador | > R$ 5.000 → +30 · > R$ 20.000 → +50 | Valor alto não é fraude, mas é onde a fraude dói. Se o próprio pagador já fez um Pix desse tamanho, o valor tem precedente e não pesa |
| **R3 · Velocity** (`VELOCITY`) | Pix do mesmo pagador em 10 minutos, contando este e **todas** as tentativas, inclusive as reprovadas | 5 → +50 (2FA) · 10 → **reprova** (dura) | Esvaziar uma conta tomada exige velocidade. Cinco em dez minutos já é incomum; dez não tem uso normal. Contar as reprovadas é proposital: insistir depois de uma recusa é, em si, o sinal |
| **R4 · Fragmentação** (`SPLIT_PAYMENT`) | 3 ou mais Pix ao **mesmo** recebedor em 60 minutos, cada um abaixo do teto, mas com a soma acima | Teto = R$ 1.000 à noite (CPF) ou R$ 5.000 de dia · +50 | É o drible das regras de valor: quem sabe que o limite noturno é R$ 1.000 manda 3 × R$ 900 |
| **R5 · Montante do dia** (`DAILY_SUM`) | Soma do pagador em 24h contra o maior dia aprovado dele nos últimos 30 dias | 3 × o maior dia; sem histórico, R$ 10.000 · +30 | Pega o esvaziamento em parcelas espaçadas, que o velocity não vê. Sozinho não pede 2FA, de propósito: é sinal de apoio |
| **R6 · Fora do perfil** (`OUT_OF_PROFILE`) | Valor acima de k × a média dos Pix aprovados do pagador | k = 5, com no mínimo 5 aprovados · +30 | R$ 3.000 é normal para um e anômalo para outro. Com menos de 5 Pix, "média" é ruído |
| **R7 · Conta recebedora nova** (`NEW_DESTINATION_ACCOUNT`) | Idade da conta que recebe | < 7 dias → +25 · < 1 dia **e** valor > R$ 1.000 → +50 e **2FA obrigatório** | Conta laranja é aberta para receber e repassar rápido. O caso de menos de 1 dia é 2FA obrigatório porque, no golpe autorizado (falso parente, falso suporte), quem paga é o cliente verdadeiro no aparelho de sempre: a confiança no **pagador** não pode compensar o risco do **recebedor** |
| **R8 · Muitos pagadores na mesma conta** (`DESTINATION_FAN_IN`) | Pagadores distintos para a mesma conta **de pessoa física** em 24h | 5 · +40 | Rastro de laranja e de golpe em massa: várias vítimas pagando a mesma conta. Não vale para CNPJ, porque comércio recebe de muitos pagadores legitimamente |
| **R9 · Conta pagadora nova** (`NEW_SOURCE_ACCOUNT`) | Conta de quem paga com menos de 7 dias, movimentando valor relevante | > R$ 1.000 · +30 | Conta recém-aberta com dados de terceiros, ou aberta só para lavar |
| **R10 · Aparelho novo** (`NEW_DEVICE`) | Primeiro Pix deste pagador neste `device_id` | +20 · com valor > R$ 1.000, +35 | Trocar de aparelho é o primeiro passo do sequestro de conta. Sozinho não pede 2FA, porque todo mundo troca de celular um dia |
| **R11 · Aparelho compartilhado** (`SHARED_DEVICE`) | Pagadores distintos no mesmo aparelho em 7 dias | 2 → +30 · 3 → **reprova** (dura) | Duas pessoas num aparelho pode ser família; três ou mais em uma semana é a "fazenda" de contas do golpe organizado |
| **R12 · Viagem impossível** (`IMPOSSIBLE_TRAVEL`) | Velocidade entre o último Pix com localização e este | > 900 km/h, ignorando distâncias < 100 km · +50 | Duas localizações que ninguém percorre naquele tempo indicam sessão clonada ou acesso remoto. O piso de 100 km existe porque o GPS oscila, e 2 km em 1 segundo dariam uma "velocidade" absurda |
| **C1 · Contraparte conhecida** (`KNOWN_COUNTERPARTY`) | Pix aprovados deste pagador para este recebedor **há mais de 24h** | 2 ou mais · −30 | Histórico bom reduz fricção. O "há mais de 24h" impede o fraudador de fabricar confiança: sem ele, três Pix fragmentados em uma hora tornariam o recebedor "conhecido" e anulariam o R4. Um teste pegou isso |
| **C2 · Mesmo titular** (`SAME_OWNER`) | O mesmo documento nos dois lados | −40 | Mandar para a própria conta em outro banco é o Pix de menor risco que existe |
| **C3 · Aparelho de confiança** (`TRUSTED_DEVICE`) | O pagador usa este aparelho há mais de 30 dias | −20 | É o oposto do sequestro de conta |

## Como as regras viram decisão

1. **Entrada inválida não é decisão antifraude.** Formato errado (`400`), documento com dígito verificador errado ou data inexistente (`422`) saem antes de qualquer análise, e nada é gravado.
2. **Todas as 15 regras rodam, sempre.** Mesmo depois de uma regra dura já ter decidido: o `metadata` registra cada uma que disparou.
3. **Score** = soma dos pontos, com os de confiança subtraindo, nunca abaixo de zero.

   | Score | Decisão |
   |---|---|
   | 0 a 49 | `approved` |
   | 50 a 99 | `two_factor_required` |
   | 100 ou mais | `rejected` |

4. **Regras duras** impõem uma decisão mínima: `rejected` (R1, R3 com 10 ou mais, R11 com 3 ou mais) ou `two_factor_required` (R7 com menos de 1 dia).
5. **A decisão final é a mais severa** entre a do score e a das regras duras. Pontos de confiança não desfazem uma regra dura.

**Quando pedimos 2FA:** quando há dúvida, não certeza. Um sinal forte sozinho (rajada de 5 Pix, fragmentação, conta recebedora de horas, viagem impossível) ou dois sinais médios juntos (valor alto sem precedente com aparelho novo). O 2FA é a fricção barata: o cliente bom confirma em segundos, e o fraudador com a conta tomada não tem o segundo fator.

**Os dois cenários das direcionais**, provados em `tests/integration/pix/test_pix_scenarios.py`:

| Cenário | Regras | Decisão |
|---|---|---|
| Primeiro Pix, R$ 150 | `NEW_DEVICE` (+20) | `approved`: não punimos todo cliente novo |
| Primeiro Pix, R$ 6.000 | `HIGH_AMOUNT` (+30), `NEW_DEVICE` (+35) | `two_factor_required` |
| **Os mesmos R$ 6.000**, de quem pagou esse recebedor 5 vezes no último mês, do mesmo aparelho | `HIGH_AMOUNT` (+30), `OUT_OF_PROFILE` (+30), `DAILY_SUM` (+30), `KNOWN_COUNTERPARTY` (−30), `TRUSTED_DEVICE` (−20) | `approved` (40) |

### Decisões de desenho

- **Idempotência pelo `id` do Pix.** O mesmo `id` com o mesmo conteúdo devolve a análise gravada, com `200` e corpo idêntico, sem reavaliar. O mesmo `id` com outro conteúdo dá `409`. A comparação usa o hash do payload. A garantia final é o `UNIQUE(transaction_id)`; o teste dispara 5 reenvios simultâneos e confere que nasce uma análise só.
- **Principal desafio: a corrida do histórico.** Dois Pix do mesmo pagador chegam juntos, os dois leem "4 Pix nos últimos 10 minutos" e os dois passam no velocity. A solução é serializar as análises **por pagador**: a linha `payer` é travada com `SELECT … FOR UPDATE` antes de ler o histórico, e só é solta no commit, depois que este Pix já está gravado. Medimos: com a trava desligada, 12 Pix simultâneos geraram **5** aprovações em vez das **3** corretas.
- **Point-in-time.** O histórico de cada Pix só enxerga Pix com `transaction_date` até ele. Um Pix posterior que por acaso chegou antes não entra na conta. Isso torna cada decisão reprodutível.
- **O que conta como histórico.**
  - Velocity, montante, fragmentação e rede contam **todas** as tentativas.
  - Perfil, contraparte conhecida e aparelho de confiança só usam Pix **aprovados**. Um `two_factor_required` pode não ter sido concluído, e não existe rota de confirmação de 2FA no escopo. Usar só aprovados também impede o fraudador de "treinar" o perfil da vítima com tentativas.
- **Latência.** Nenhuma chamada externa no caminho crítico. Cada pergunta de histórico é uma consulta indexada: (pagador, data), (recebedor, data) e (aparelho, data).
- **CNPJ alfanumérico.** Desde julho de 2026 a Receita emite CNPJ com letras. A validação já aceita o formato novo (o exemplo oficial `12.ABC.345/01DE-35` está nos testes).

### O que consideramos e deixamos de fora

| Ideia | Por que ficou de fora |
|---|---|
| Rejeitar `transaction_date` distante do relógio do servidor (anti-replay) | O avaliador e os testes mandam Pix com datas fixas. Em produção, a data seria carimbada pelo próprio PSP, e a regra faria sentido |
| Consultar o CNPJ recebedor no Serpro (situação cadastral, data de abertura, porte) | "Outras APIs" estão fora do escopo, e a consulta põe latência e um ponto de falha em cada Pix. Em produção: enriquecimento **assíncrono**, fora do caminho crítico, que marca o recebedor para os próximos Pix |
| Modelo de machine learning | Sem dados rotulados, um modelo seria pesos inventados com aparência de rigor. As regras são explicáveis, que é o que o Pix exige ("toda decisão sai com um motivo"). O `pix_rule_hit` já guarda o histórico de disparos que um modelo precisaria para ser treinado |
| Responder com 2FA quando o banco cai (fail-closed) | Erro interno responde `500`: a API não finge uma análise que não fez. Em produção, a política típica é aprovar valor baixo e pedir 2FA para valor alto quando o motor cai |
| Trava também por aparelho e por recebedor | A trava é por pagador. Dois pagadores **diferentes** no mesmo aparelho, no mesmo milissegundo, ainda podem ler a contagem antiga. Aceitamos: o caso é raríssimo e, no máximo, atrasa a reprovação para o Pix seguinte |

## Modelagem de dados

| Tabela | Papel |
|---|---|
| `payer` | Um por documento. Existe para ser **travada**: a serialização por pagador |
| `pix_transaction` | O Pix como chegou, em colunas planas (não JSON), porque é sobre elas que o histórico é consultado. `UNIQUE(transaction_id)`, `amount BIGINT` em centavos, datas `TIMESTAMPTZ` mais o fuso original do pagador |
| `pix_analysis` | A decisão: `analysis_key` (o `key` da resposta), `status`, `score`, `decided_by` e `rules_version`. `UNIQUE(pix_transaction_id)`: uma análise por Pix |
| `pix_rule_hit` | Cada regra que disparou: código, pontos, decisão imposta e `detail` (JSONB com o número que a regra viu). É o `metadata` e a trilha de auditoria |

O esquema está em [`database/database.sql`](database/database.sql).

## Como rodar e testar

Pré-requisito: Docker (e Python 3.11+ para rodar os testes).

1. Suba a API e o banco:

   ```bash
   docker compose up --build
   ```

   A API atende em `http://localhost:3000`. Espere o container `api` ficar `(healthy)`.

2. Rode os testes em outro terminal:

   ```bash
   pip install -r requirements-dev.txt
   ```

   ```bash
   pytest -v
   ```

   São 72 testes:
   - **34 unitários** (`tests/unit/`), um ou mais por regra, cada limiar testado no ponto em que muda de lado;
   - **38 black box** (`tests/integration/`): contrato, idempotência, validação, health check e um cenário de fraude por regra, com o histórico montado pela própria API.

3. Analise um Pix. Todas as rotas, menos `/` e `/health_check`, exigem o header `INTERNAL-TOKEN`; sem ele, a resposta é `403`:

   ```bash
   curl -s -X POST http://localhost:3000/pix/transaction -H "Content-Type: application/json" -H "INTERNAL-TOKEN: default_token" -d '{"id":"pix-000123","amount":150000,"transaction_date":"2026-10-17T22:15:00-03:00","source_account":{"branch":"0001","account_number":"88213","opening_date":"2025-03-02T10:00:00-03:00","owner":{"document_number":"123.456.789-09"}},"destination_account":{"participant":"60701190","branch":"3675","account_number":"10442","opening_date":"2026-10-10T09:00:00-03:00","owner":{"document_number":"987.654.321-00"}},"source":{"device_id":"dvc-9f2c71a8","channel":"app","ip":"198.51.100.23","geolocation":{"latitude":-23.56,"longitude":-46.65}}}'
   ```

   O Pix do exemplo do desafio é R$ 1.500 de pessoa física às 22h15. Num banco vazio, a resposta é esta (saída real; só o `key` muda):

   ```json
   {
     "key": "bf3cdb4c-a390-48ee-864f-52ae14aedd08",
     "status": "rejected",
     "metadata": {
       "transaction_id": "pix-000123",
       "score": 35,
       "decided_by": "NIGHT_LIMIT",
       "rules_version": "2026.10.17-1",
       "reasons": [
         {"rule": "NIGHT_LIMIT", "points": 0, "forced_status": "rejected", "detail": {"amount": 150000, "limit": 100000, "local_hour": 22}},
         {"rule": "NEW_DEVICE", "points": 35, "forced_status": null, "detail": {"amount": 150000}}
       ]
     }
   }
   ```

   O score (35) sozinho aprovaria. Quem reprova é a regra dura do limite noturno, e o `decided_by` diz isso. A conta recebedora do exemplo tem 7,55 dias, um pouco acima do limite de 7 da R7, por isso ela não aparece.

4. Consulte a análise gravada:

   ```bash
   curl -s http://localhost:3000/pix/transaction/pix-000123 -H "INTERNAL-TOKEN: default_token"
   ```

| Rota | Respostas |
|---|---|
| `POST /pix/transaction` | `201` análise nova · `200` reenvio do mesmo Pix (mesmo corpo) · `400 QIT000001` fora do formato · `403 QIT000002` sem token · `409 QIT001002` mesmo `id` com outro conteúdo · `422 QIT001003` CPF/CNPJ inválido · `422 QIT001004` data inexistente |
| `GET /pix/transaction/{id}` | `200` a análise gravada · `404 QIT001001` Pix nunca analisado |
