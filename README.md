# Grupo 3

**Integrantes:** Victor Kazunori e Matheus Reis

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
   ├──► repository ── 19 consultas de histórico, todas "até a data deste Pix"
   │                  (pagador, destino, par pagador→destino, aparelho, IP, cadeia de laranjas)
   ▼
controllers/fraud_engine/signals.py ── monta os sinais (idade das contas, hora local, mediana, velocidade geográfica...)
controllers/fraud_engine/rules.py   ── 29 regras: 7 duras, 20 de pontos, 2 bônus de confiança
controllers/fraud_engine/policy.py  ── soma identidade e destino separadamente e decide
   │
   ▼
grava pix_transaction + pix_analysis + uma linha por regra acionada ── commit ── responde
```

O motor segue o Base Service: é regra de negócio, então mora na camada de controllers, em `src/controllers/fraud_engine/`. O controller busca o histórico pelo repository, entrega ao motor e grava o que ele decidiu. O motor é Python puro: não conhece banco nem HTTP. É isso que permite testar cada regra em unidade, em milissegundos, e reproduzir uma decisão a partir da metadata gravada.

Princípios que guiaram as regras:

- **Ausência de dado é neutra, nunca suspeita.** Sem GPS, sem histórico, primeiro Pix: o motor troca de fonte de evidência, não pune. No cold start o aparelho é "desconhecido", não "novo".
- **Uma anomalia fraca sozinha nunca rejeita.** Nenhuma regra de pontos vale 90; rejeitar por score exige combinação de sinais.
- **O perfil só aprende com Pix aprovados.** Tentativas barradas do fraudador não viram o "normal" do cliente. Já a velocity conta **todas** as tentativas, porque tentar de novo depois de ser barrado também é sinal.
- **Mediana, não média.** Um único Pix grande (a compra do carro) não muda o padrão do cliente.
- **O tempo do Pix, não o do servidor.** Todo histórico é lido "até `transaction_date`", então um Pix nunca é julgado com informação que só existiu depois dele.
- **Parâmetros ancorados em norma quando ela existe.** O limite noturno e o limite de aparelho novo seguem as regras do Banco Central para o Pix.

## Regras

Valores em reais; no código ficam em centavos, em `src/controllers/fraud_engine/parameters.py`, cada um com o porquê ao lado. Janelas sempre terminam no `transaction_date` do Pix analisado.

### Regras duras (rejeitam sozinhas)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `VELOCITY_BOT` | Tentativas da mesma conta pagadora em 5 min, contando a atual e qualquer status | ≥ 5. Pagador **maduro** (conta com ≥ 180 dias e ≥ 5 Pix aprovados): ≥ 10 (PF) ou ≥ 30 (PJ) | Conta nova digitando cinco Pix em cinco minutos é script ou alguém raspando o saldo. Conta antiga com histórico fazendo o mesmo é o racha do bar (ou a folha de pagamento, na empresa): aí quem pede o 2FA é o `VELOCITY_BURST`, e só barramos num volume que nenhum uso humano explica. |
| `DEVICE_FARM` | CPFs/CNPJs pagadores distintos que usaram o mesmo `device_id` em 30 dias | ≥ 3. Pagador maduro com 3 ou 4 no aparelho → +40 (pede 2FA, não barra); ≥ 5 barra sempre | Três contas novas num aparelho é central de golpe operando contas compradas. Três pessoas com conta antiga e histórico próprio no mesmo celular é uma família, e merece confirmação, não bloqueio. |
| `NIGHT_HARD_LIMIT` | Pix de PF entre 20h e 6h (hora local do cliente) | > R$ 5.000 | De noite o risco maior é coação (sequestro relâmpago), e sob coação a vítima também passa no 2FA. |
| `NIGHT_TOTAL_HARD_LIMIT` | Soma do que saiu da conta de PF desde as 20h (aprovados + 2FA), mais o Pix atual | > R$ 5.000 | O mesmo teto de coação, aplicado à noite inteira: cinco Pix de R$ 990 para cinco contas diferentes tiram o mesmo dinheiro que um de R$ 5.000. |
| `DAILY_HARD_LIMIT` | Soma de aprovados + 2FA em 24 h, mais o Pix atual | PF > R$ 20.000; PJ > R$ 200.000 | Contenção de dano se tudo o mais falhar: celular destravado esvazia a conta em minutos. |
| `NEW_DEVICE_DAILY_LIMIT` | Soma em 24 h feita de um aparelho novo, mais o Pix atual | > R$ 1.000 | Mesmo número do limite diário para aparelho novo da Res. BCB 402/2024. |
| `IMPOSSIBLE_TRAVEL_OTHER_DEVICE` | Distância Haversine até o último Pix com GPS (até 24 h) ÷ tempo, e o aparelho é outro | > 1.000 km/h e > 100 km | Dois aparelhos em cidades distantes ao mesmo tempo é credencial clonada. |

### Identidade (2FA resolve)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `NIGHT_HIGH_VALUE` | Pix de PF entre 20h e 6h | > R$ 1.000 → +40 | Limite noturno padrão de PF da Res. BCB 142/2021. O cliente pode ter aumentado o limite, o que o motor não enxerga, então pede 2FA em vez de barrar. 40 = pede 2FA sozinho. |
| `NIGHT_TOTAL` | Soma do que saiu da conta de PF desde as 20h, mais o Pix atual, com cada Pix abaixo do limite noturno | > R$ 3.000 → +40 | O limite do BCB é por Pix. Quem drena a conta de madrugada espalha Pix abaixo de R$ 1.000 por contas diferentes, sem repetir valor nem destino: o `SPLIT_PAYMENT` (mesmo destino) e o `STRUCTURING` (mesmo valor) não veem. A noite é tratada como um Pix só. |
| `NEW_DEVICE` | A conta tem histórico, mas este aparelho não tem Pix aprovado nela há mais de 24 h | +30; +15 se > R$ 200 | Tomada de conta quase sempre começa em outro aparelho. As 24 h de maturação impedem "esquentar" o aparelho com um Pix de R$ 1. R$ 200 é o limite por Pix de aparelho novo do BC. |
| `VELOCITY_BURST` | Tentativas em 5 min (ou em 1 h) | 3 em 5 min → +25; 4 → +40; 10 em 1 h → +20 | Rajada pode ser dividir a conta do restaurante, por isso pesa antes de barrar. Só a janela mais forte conta, para não pontuar duas vezes o mesmo fenômeno. |
| `PROBING_AFTER_REJECTION` | Pix rejeitados desta conta na última hora | +20 por rejeição, máx. +40 | Tentar de novo logo depois de ser barrado é o fraudador procurando o valor que passa. |
| `IMPOSSIBLE_TRAVEL` | Igual à regra dura, mas no mesmo aparelho | +45 | No mesmo aparelho pode ser GPS falsificado ou VPN, então pede confirmação em vez de barrar. |
| `NEW_SOURCE_ACCOUNT` | Conta pagadora recém-aberta movimentando ≥ R$ 1.000 | < 2 dias → +35; < 7 dias → +25 | Identidade sintética ou conta aberta só para girar dinheiro de golpe. |
| `HIGH_VALUE_WITHOUT_PROFILE` | Valor alto de quem ainda não tem perfil (menos de 5 Pix aprovados em 90 dias); não vale para transferência entre contas próprias (veja `SELF_TRANSFER`) | PF ≥ R$ 5.000 → +30 (PJ ≥ R$ 50.000) | Sem histórico não dá para dizer que o valor é fora do padrão, nem que é mesmo o cliente: conta parada que de repente manda R$ 6 mil é o retrato da conta tomada. Junto do primeiro Pix para o destino (+10) chega aos 40 do 2FA. Primeiro Pix de valor baixo continua sem ponto, e o aluguel recorrente (bônus de −30) passa. |
| `SPLIT_PAYMENT` | Pix desta conta para o **mesmo destino** em 1 h (aprovados + 2FA), contando o atual. Cada pedaço abaixo do limite, a soma acima | ≥ 3 pedaços → +40. Limite: R$ 1.000 à noite (PF); R$ 5.000 de dia (PJ: R$ 50.000) | Partir o valor para passar por baixo do limite noturno é o drible clássico (3 x R$ 990 às 23h). A soma é tratada como o Pix que ela forma, e pesa o mesmo que o limite pesaria: pede 2FA. |
| `SHARED_DEVICE` | 2 pagadores distintos no mesmo aparelho em 30 dias | +15 | Sinal fraco por si só (família), mas soma com outros. |
| `SHARED_IP` | Pagadores distintos no mesmo IP em 24 h | ≥ 5 → +10 | IP é sinal fraco: operadora põe milhares de clientes atrás do mesmo IP (CGNAT). |

### Destino (2FA não resolve)

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `NEW_DESTINATION_ACCOUNT` | Idade da conta destino na data do Pix (não vale para transferência entre contas próprias; veja `SELF_TRANSFER`) | < 7 dias → +30; < 30 dias → +15 | Conta laranja nasce para receber um golpe e é abandonada em dias. |
| `DESTINATION_FAN_IN` | Pagadores distintos para a mesma conta destino em 24 h, contando o atual | PF: ≥ 5 → +35, ≥ 10 → +50. PJ: limiares 10x | Muitos CPFs pagando a mesma pessoa física numa tarde é golpe em massa em andamento. Comércio (CNPJ) recebe de muita gente por natureza. |
| `FLAGGED_DESTINATION` | **Outros** pagadores distintos que tiveram Pix para este destino rejeitado por risco do destino (destino ≥ 60) nos últimos 30 dias | ≥ 1 → +35; ≥ 3 → +50 | Reputação de rede: se alguém já foi barrado pagando essa conta, a próxima vítima está a caminho. Só conta rejeição causada pelo destino (o celular roubado tentando pagar a mãe da vítima não suja a conta da mãe) e conta pessoas, não tentativas, para que ninguém consiga marcar a conta de um inocente insistindo sozinho. É pontuação com janela, e não blacklist permanente, porque uma lista dura se alimenta sozinha: a conta marcada passa a ter tudo rejeitado e nunca sai. Uma lista de fraude confirmada (analista ou marcação de fraude do BC no DICT) seria a evolução, mas exige uma rota de cadastro fora do escopo. |
| `PROFILE_AMOUNT_SPIKE` | Valor ÷ mediana dos aprovados em 90 dias (piso de R$ 100). Só com ≥ 5 Pix no histórico | > 3x → +20; > 10x → +35 | A vítima de golpe manda um valor fora do seu padrão. Com menos de 5 Pix não existe padrão, e a regra fica neutra. |
| `STRUCTURING` | Pix de valor idêntico desta conta na última hora, contando o atual | ≥ 3 → +20 | Fracionar uma quantia em partes iguais para ficar abaixo dos tetos. Se os pedaços vão para o mesmo destino, quem pontua é o `SPLIT_PAYMENT`, para não contar duas vezes. |
| `DAILY_EXPOSURE` | Soma de 24 h acima da metade do teto diário | +20 | Avisa antes do teto duro. |
| `NEW_COUNTERPARTY` | Primeiro Pix desta conta para este destino | > R$ 500 → +10 | Todo mundo paga alguém pela primeira vez, então é fraco e só soma a outros sinais. |

### Cadeia de laranjas (2FA não resolve)

Golpe raramente para na primeira conta: a vítima paga um laranja, o laranja repassa em minutos para outra conta, às vezes uma conta central que junta o dinheiro de vários laranjas, e dali o dinheiro some. O motor só enxerga o trecho da cadeia que passa pelos Pix que ele analisou, e olha um elo para cada lado. As duas regras somam no placar de destino porque quem paga é o próprio dono do laranja, que passa no 2FA.

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `MULE_PASS_THROUGH` | O **pagador** recebeu Pix nas últimas 24 h (aparece como destino no histórico, aprovados + 2FA) e, com este Pix, já repassou pelo menos metade do que recebeu | Recebido ≥ R$ 1.000. De ≥ 3 pagadores distintos (só PF) → +25. Se algum Pix de entrada já tinha risco de destino ≥ 40 → +60 | Receber de vários e repassar é o formato do laranja, mas também o da vaquinha do churrasco, então pesa pouco, e com o +25 o `PROFILE_AMOUNT_SPIKE` não conta (o valor alto é explicado pelo que entrou). Já repassar um dinheiro que o próprio motor achou que estava indo para um laranja é lavar dinheiro de golpe: rejeita sozinho. |
| `MULE_LINK` | Nos últimos 30 dias, algum Pix pego pelo `MULE_PASS_THROUGH` saiu do **destino** ou chegou nele | ≥ 1 conta ligada → +50 | O destino é um laranja ou a conta central da cadeia. Com o primeiro Pix para ele (+10), rejeita. |

O cenário de ponta a ponta `test_mule_chain_is_cut_at_the_second_link` reproduz a cadeia: a vítima paga um laranja aberto há 3 dias e cai no 2FA (risco de destino 40). Quando o laranja tenta repassar para a conta central, o Pix é rejeitado pelo `MULE_PASS_THROUGH`. A partir daí, o próximo cliente que tentar pagar a conta central é rejeitado pelo `MULE_LINK`.

O custo conhecido: uma conta recém-aberta que recebe de alguém pela primeira vez e repassa quase tudo em seguida tem o mesmo formato de um laranja, e é barrada. É o mesmo comportamento que o bloqueio cautelar do Pix existe para pegar.

### Bônus de confiança

| Regra | Lógica | Parâmetros | Por quê |
|-------|--------|------------|---------|
| `TRUSTED_COUNTERPARTY` | Pix aprovados para este destino e há quanto tempo foi o primeiro | ≥ 2 Pix e ≥ 7 dias → −30 | É o aluguel, a diarista, o fornecedor. É o que impede o motor de travar quem só quer pagar as contas. |
| `SELF_TRANSFER` | Mesmo CPF/CNPJ dos dois lados **e** conta de destino com ≥ 30 dias ou já paga com aprovação há ≥ 7 dias | −25; e as regras de destino novo, valor sem perfil e cadeia não se aplicam | O dinheiro não sai da pessoa, desde que a conta seja mesmo dela. No golpe da **conta espelho**, o fraudador abre, com os dados roubados da vítima, uma conta no CPF dela em outro banco e esvazia a conta verdadeira para lá. Só o mesmo CPF não basta: sem a conta antiga ou conhecida, o Pix é tratado como qualquer outro (`same_owner` fica no `metadata`). O custo: o cliente que abriu uma conta nova de verdade e transfere tudo para ela no dia seguinte passa por 2FA ou, num valor alto, é barrado. |

Os bônus só reduzem a própria categoria e nunca a deixam negativa: confiar no destino não "paga" a dúvida sobre a identidade.

## Como as regras viram decisão

É um sistema de pontuação em duas camadas.

1. **Regras duras** não somam pontos. Se uma delas dispara, o Pix é `rejected` na hora, seja qual for o resto (ex.: 5ª tentativa em 5 minutos de uma conta nova, Pix de pessoa física acima de R$ 5.000 à noite).
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
    "policy_version": "2026.10.17-v7",
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
- **Concorrência.** O controller pega um `pg_advisory_xact_lock` pela conta pagadora antes de ler o histórico. Sem isso, cinco Pix simultâneos da mesma conta leriam o mesmo histórico vazio e passariam todos pela velocity. Contas diferentes continuam em paralelo. Como o lock é por conta, o mesmo `id` chegando **ao mesmo tempo** por contas pagadoras diferentes não se espera: quem barra o segundo é o `UNIQUE(pix_id)`, e esse request desfaz tudo o que fez e responde com a análise que ficou (200 se o conteúdo é o mesmo, 409 se é outro), em vez de virar erro 500.

Validações que devolvem 422: CPF/CNPJ com dígito verificador inválido, data que não existe no calendário, conta aberta depois do Pix e Pix com data no futuro (mais de 2 min), que serviria para plantar histórico adiantado. O CNPJ alfanumérico, que a Receita emite desde julho de 2026 (letras nas 12 primeiras posições, dígitos verificadores numéricos), é aceito e validado pela mesma conta, com o valor de cada caractere igual ao código ASCII menos 48.

## Simulação: como calibramos

`scripts/simulate.py` gera 60 dias de histórico de 150 pessoas e 5 empresas (mercado, amigos, aluguel, fornecedores, folha) e, no último dia, mistura o uso normal com os casos legítimos que mais se parecem com fraude e com 14 cenários de ataque. Tudo vai para a API em ordem cronológica, e só o último dia é medido. O repasse do laranja só acontece se o dinheiro das vítimas chegou (Pix aprovado ou com 2FA), como na vida real.

"Contido" depende do golpe. Quando o fraudador usa a conta de outra pessoa, o 2FA segura, porque ele não tem o segundo fator. Quando quem paga é a vítima enganada, ou o fraudador é o dono da conta, o 2FA não segura nada e só a rejeição conta.

Resultado com `--seed 42` (4.390 Pix, 737 medidos):

| Clientes bons | Pix | Aprovado | 2FA | Rejeitado |
|---|---:|---:|---:|---:|
| Dia a dia | 302 | 99,3% | 0,7% | 0,0% |
| Aluguel para o mesmo locador | 42 | 95,2% | 4,8% | 0,0% |
| Entre contas próprias | 13 | 92,3% | 7,7% | 0,0% |
| Cliente novo, Pix comum | 9 | 100,0% | 0,0% | 0,0% |
| Cliente novo, Pix alto (R$ 5 a 12 mil) | 6 | 0,0% | 100,0% | 0,0% |
| Racha do bar (amigos pagando quem pagou a conta) | 23 | 100,0% | 0,0% | 0,0% |
| Vaquinha repassada para a pousada | 6 | 100,0% | 0,0% | 0,0% |
| Fornecedor (empresa) | 7 | 100,0% | 0,0% | 0,0% |
| Folha de pagamento pelo app (empresa) | 50 | 30,0% | 70,0% | 0,0% |

| Fraude | Pix | Aprovado | 2FA | Rejeitado | 2FA segura? | Contido |
|---|---:|---:|---:|---:|:---:|---:|
| Tomada de conta, aparelho novo | 18 | 0,0% | 0,0% | 100,0% | sim | 100,0% |
| Celular roubado à noite | 18 | 5,6% | 16,7% | 77,8% | sim | 94,4% |
| Drenagem noturna espalhada | 36 | 50,0% | 33,3% | 16,7% | sim | 50,0% |
| Viagem impossível | 12 | 0,0% | 16,7% | 83,3% | sim | 100,0% |
| Robô em rajada | 48 | 37,5% | 54,2% | 8,3% | sim | 62,5% |
| Fracionamento, valor repetido | 24 | 50,0% | 25,0% | 25,0% | sim | 50,0% |
| Fracionamento noturno | 18 | 66,7% | 22,2% | 11,1% | sim | 33,3% |
| Golpe para conta laranja | 12 | 0,0% | 0,0% | 100,0% | não | 100,0% |
| Cadeia: vítima paga laranja | 12 | 0,0% | 33,3% | 66,7% | não | 66,7% |
| Cadeia: laranja repassa | 2 | 0,0% | 0,0% | 100,0% | não | 100,0% |
| Cadeia: vítima paga a conta central | 6 | 0,0% | 0,0% | 100,0% | não | 100,0% |
| Conta espelho (mão fantasma) | 6 | 0,0% | 0,0% | 100,0% | não | 100,0% |
| Conta aberta com documento roubado | 12 | 0,0% | 25,0% | 75,0% | não | 75,0% |
| Fazenda de aparelhos | 6 | 33,3% | 0,0% | 66,7% | não | 66,7% |

70% das tentativas de fraude e 82% do valor fraudado foram contidos. Dos Pix de clientes bons, 90,0% passaram direto, 10,0% pediram 2FA e nenhum foi rejeitado; no uso do dia a dia, 99% passam direto. Latência do motor: p50 de 26 ms e p99 de 38 ms. Com outra semente e o dobro de clientes (`--seed 7 --customers 300`), os números se mantêm: 71% dos Pix e 80% do valor contidos, 89,5% dos clientes bons aprovados direto e 1 rejeitado em 902.

Os Pix bons de quem estava sendo atacado no mesmo dia ficam fora dessa conta, numa linha própria da saída: o motor tem motivo para desconfiar deles.

O que sobra aprovado na fraude é o começo de cada padrão. Os primeiros pedaços de um fracionamento ou de uma drenagem noturna, e os três primeiros Pix de um robô, são iguais a uso normal; a regra só enxerga a partir da soma ou da repetição. A drenagem noturna deixa passar por desenho até R$ 3.000 por noite. Dos seis laranjas da cadeia, só dois chegaram a receber dinheiro (as outras vítimas foram barradas no primeiro elo), e os dois foram barrados ao repassar; quem pagou a conta central depois foi barrado pelo `MULE_LINK`.

A fricção dos clientes bons se concentra em dois lugares. O Pix alto de cliente sem histórico pede 2FA por decisão de política. A folha paga Pix a Pix pelo app pede 2FA a partir do 4º funcionário, por velocidade; como o motor não recebe o resultado do 2FA, esses Pix nunca viram "aprovados" e a relação com o funcionário não ganha o bônus de confiança no mês seguinte. A vaquinha era o falso positivo mais caro até a v6: quem recebe dos amigos e repassa tudo para a pousada somava `MULE_PASS_THROUGH` (+25) com `PROFILE_AMOUNT_SPIKE` (até +35), as duas medindo a mesma estranheza, e uma em cada seis era rejeitada. Na v7 o pico de valor não conta quando o repasse fraco dispara. Sobra um caso: com 6 amigos, o 6º Pix de entrada pode somar fan-in (+35) e pico de valor (+20) e chegar a 55, e aí o repasse passa a contar como dinheiro suspeito (+60). Foi o único cliente bom rejeitado na rodada de 300 clientes.

**Limites honestos.** Os rótulos de fraude são do próprio script: isso mede a coerência da política contra ataques conhecidos, não precisão real. Para medir precisão e recall seria preciso fraude confirmada em produção e o resultado dos desafios 2FA, que o enunciado não fornece. Com esses dados, os próximos passos seriam medir a taxa de falso positivo por regra (a tabela `pix_analysis_rule_hit` já permite) e ajustar pesos por segmento (cliente novo vs. recorrente).

## Fora do escopo e próximos passos

O que o motor assume, e o que faria a seguir com mais dados ou mais rotas:

- **O `device_id` é confiável porque vem do app do banco.** Um fraudador que copiasse o identificador do aparelho da vítima herdaria a confiança dele. Em produção, o aparelho é atestado (Play Integrity no Android, App Attest no iOS) e vinculado por uma chave guardada no hardware; isso acontece no app e no cadastro do aparelho, fora deste contrato.
- **A data do Pix vem do sistema do banco, não do cliente.** Quem chama a API é outro sistema interno, autenticado pelo token, então o cliente final não escolhe o `transaction_date`. Por isso as janelas usam essa data; se a origem fosse menos confiável, usariam o horário de chegada, que já fica gravado em `created_at`.
- **Sinais do aparelho que o contrato não traz.** Chamada telefônica ativa, serviço de acessibilidade ligado e tela compartilhada são os sinais clássicos do golpe guiado e do acesso remoto. Entrariam no placar de identidade se o payload os trouxesse.
- **Consulta de CNPJ na Receita (Serpro).** Situação cadastral, data de abertura e porte do recebedor ajudariam a pegar empresa de fachada. Ficou fora porque "outras APIs" estão fora do escopo e porque uma chamada externa no caminho do Pix acrescenta latência e um ponto de falha. O desenho certo é um enriquecimento assíncrono, que marca o recebedor para os próximos Pix.
- **Resultado do 2FA.** O motor nunca sabe se o cliente confirmou. Uma rota de confirmação faria o Pix confirmado contar como aprovado no histórico, e resolveria a fricção que se repete na folha de pagamento pelo app.
- **Rede de contas.** As regras de aparelho compartilhado e de cadeia de laranjas olham um salto de cada vez. Um agrupamento completo (contas ligadas por aparelhos e repasses em cadeia) rodaria fora do caminho do Pix, num processo assíncrono, e o motor só leria a marca do grupo.

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
  controllers/     pix_transaction_controller.py: valida, coleta histórico, decide, grava
    fraud_engine/  o motor: parameters.py (números e porquês), signals.py, rules.py, policy.py
  repositories/    pix_transaction_repository.py: as consultas de janela
  resources/       pix_transaction.py: as duas rotas
  models/ dtos/ schemas/ errors/ middlewares/   camadas do Base Service
database/database.sql
scripts/simulate.py
tests/unit/fraud_engine/           regras e política, sem banco
tests/integration/pix_transaction/ contrato e cenários de fraude pela API
docs/                              guia original do Base Service
```
