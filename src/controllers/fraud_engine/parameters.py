"""Todos os números da política antifraude, num lugar só.

Cada parâmetro tem o porquê do lado. Valor em dinheiro está sempre em
CENTAVOS, como o `amount` do contrato. Mudar qualquer número aqui muda a
política, então a POLICY_VERSION sobe junto: ela vai gravada em cada
análise, e é o que permite explicar uma decisão antiga com a regra que
valia no dia.
"""

POLICY_VERSION = "2026.10.18-v10"

REAIS = 100

# ─── Decisão ────────────────────────────────────────────────────────
# Abaixo de 40 pontos, nenhum conjunto de sinais justifica fricção: são
# precisos pelo menos dois sinais moderados (ex.: destino novo + conta
# destino com menos de 30 dias) ou um forte para chegar aqui.
TWO_FACTOR_SCORE = 40

# Acima de 90 a soma de sinais independentes já é evidência, não dúvida.
# Nenhuma regra sozinha vale 90, então rejeição por score sempre exige
# combinação de sinais.
REJECT_SCORE = 90

# Risco de DESTINO alto rejeita sozinho, mesmo com identidade limpa: no
# golpe de engenharia social a vítima é quem está no app e passa no 2FA.
# Pedir 2FA aí seria fricção que não protege ninguém. 60 é o que soma a
# assinatura do "falso parente": conta destino com menos de 7 dias (30)
# + valor 3x acima do padrão do cliente (20) + primeiro Pix para ela (10).
# Na simulação, com 70 dois terços desses golpes só pediam 2FA.
REJECT_BENEFICIARY_SCORE = 60

# ─── Horário ────────────────────────────────────────────────────────
# Mesma faixa do limite noturno do Pix definido pelo Banco Central.
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6

# Limite noturno padrão para pessoa física (Res. BCB 142/2021). O
# cliente pode ter pedido para aumentar, e o motor não enxerga isso,
# então acima dele à noite o motor pede 2FA em vez de barrar.
NIGHT_PF_LIMIT = 1_000 * REAIS
NIGHT_HIGH_VALUE_POINTS = 40

# Teto noturno duro. De noite o risco maior é coação (sequestro
# relâmpago), e sob coação a vítima também passa no 2FA: acima disso só
# barrar protege.
NIGHT_PF_HARD_LIMIT = 5_000 * REAIS

# Soma da noite: o limite do BCB é por Pix, e quem drena a conta de
# madrugada espalha R$ 950 por várias contas diferentes, cada uma abaixo
# do limite e nenhuma repetida (o SPLIT_PAYMENT, que olha o mesmo
# destino, não vê). A noite inteira, desde as 20h, é tratada como um Pix
# só: passou de 3x o limite noturno, pesa como o próprio limite (40, que
# pede 2FA); passou do teto de coação, barra como o teto barraria.
NIGHT_TOTAL_SOFT_LIMIT = 3_000 * REAIS
NIGHT_TOTAL_POINTS = 40

# ─── Teto diário ────────────────────────────────────────────────────
# Exposição de 24 h (aprovados + 2FA + o Pix atual). Passar do teto pede
# 2FA (+40 de identidade) e não rejeita: o celular roubado e destravado
# que esvazia a conta é tomada de conta, e o 2FA segura; o cliente que
# precisa mesmo pagar um valor alto confirma e segue.
# O teto acompanha o cliente. O teto do cliente é 3x o maior dia de
# saídas aprovadas nos últimos 90 dias (sem contar as últimas 24 h, para
# o próprio ataque não subir o teto), entre um piso e um máximo: quem
# movimenta R$ 2 mil por mês e de repente manda R$ 8 mil precisa provar
# que é ele.
#   - Sem perfil (menos de PROFILE_MIN_TRANSACTIONS aprovados em 90 dias),
#     vale o teto padrão: cold start não é suspeita.
#   - Empresa usa o maior entre o padrão e o do cliente: folha, 13º e
#     fornecedor trimestral são picos que 90 dias não mostram, e a folha
#     já pede 2FA por velocidade (os dois somados rejeitariam).
#   - Transferência entre contas próprias também usa esse maior teto: o
#     dinheiro não sai da pessoa.
DAILY_LIMIT_HISTORY_MULTIPLIER = 3
DAILY_PF_MIN_LIMIT = 5_000 * REAIS
DAILY_PF_MAX_LIMIT = 100_000 * REAIS
DAILY_LIMIT_POINTS = 40
DAILY_PF_DEFAULT_LIMIT = 20_000 * REAIS
# Empresa movimenta mais por natureza: piso, padrão e máximo 10x maiores.
DAILY_PJ_MULTIPLIER = 10
DAILY_PJ_DEFAULT_LIMIT = DAILY_PF_DEFAULT_LIMIT * DAILY_PJ_MULTIPLIER
# Acima da metade do maior teto, o destino já pesa (avisa antes do teto).
DAILY_EXPOSURE_SOFT_RATIO = 0.5
DAILY_EXPOSURE_POINTS = 20

# ─── Velocity ───────────────────────────────────────────────────────
VELOCITY_SHORT_WINDOW_MINUTES = 5
# 5 tentativas em 5 min (contando a atual) é ritmo de script ou de quem
# está raspando o saldo; nenhum cliente digita chave, valor e senha
# cinco vezes em cinco minutos.
VELOCITY_BOT_ATTEMPTS = 5
# A 3ª em 5 min já é incomum, mas pode ser alguém dividindo a conta do
# restaurante: pesa, não barra. A 4ª sozinha já pede 2FA.
VELOCITY_BURST_ATTEMPTS = 3
VELOCITY_BURST_POINTS = 25
VELOCITY_STRONG_BURST_ATTEMPTS = 4
VELOCITY_STRONG_BURST_POINTS = 40
VELOCITY_LONG_WINDOW_MINUTES = 60
VELOCITY_HOUR_ATTEMPTS = 10
VELOCITY_HOUR_POINTS = 20

# Tentar de novo logo depois de ser barrado é sondagem: o fraudador
# procura o valor que passa.
PROBING_WINDOW_MINUTES = 60
PROBING_POINTS_PER_REJECTION = 20
PROBING_MAX_POINTS = 40

# Fracionamento: o mesmo valor repetido na mesma hora, típico de quem
# parte uma quantia para ficar abaixo de um teto.
STRUCTURING_WINDOW_MINUTES = 60
STRUCTURING_MIN_REPETITIONS = 3
STRUCTURING_POINTS = 20

# Fracionamento para o mesmo destino: partir um valor em pedaços abaixo
# do limite e mandar todos para a mesma conta. É o drible clássico do
# limite noturno (3 x R$ 990 às 23h). Os pedaços somados são tratados
# como o Pix que eles formam: se a soma passa do limite que valeria para
# um Pix só, pesa como o próprio limite pesaria (40 = pede 2FA).
SPLIT_WINDOW_MINUTES = 60
SPLIT_MIN_PARTS = 3
SPLIT_POINTS = 40

# ─── Perfil do pagador ──────────────────────────────────────────────
PROFILE_WINDOW_DAYS = 90
# Com menos de 5 Pix aprovados não existe "padrão" a comparar: uma
# mediana de duas operações não diz nada. Abaixo disso a regra fica
# neutra (cold start não é suspeita).
PROFILE_MIN_TRANSACTIONS = 5
# Piso da mediana: quem só fez Pix de R$ 5 não deve tomar 2FA num de R$ 60.
PROFILE_MEDIAN_FLOOR = 100 * REAIS
PROFILE_SPIKE_RATIO = 3
PROFILE_SPIKE_POINTS = 20
PROFILE_EXTREME_RATIO = 10
PROFILE_EXTREME_POINTS = 35

# Valor alto de quem ainda não tem perfil (menos de 5 Pix aprovados).
# Sem histórico não dá para dizer que o valor é "fora do padrão", e
# também não dá para dizer que é o cliente: conta antiga e parada que de
# repente manda R$ 6 mil é o retrato da conta tomada. É dúvida de
# identidade, que o 2FA resolve, então pesa 30: junto do primeiro Pix
# para o destino (+10) chega aos 40 do 2FA. Primeiro Pix de valor baixo
# continua sem ponto nenhum, e o aluguel recorrente (bônus de -30) passa.
# PJ movimenta mais por natureza, então o valor é 10x maior.
HIGH_VALUE_AMOUNT = 5_000 * REAIS
HIGH_VALUE_POINTS = 30
HIGH_VALUE_PJ_MULTIPLIER = 10

# ─── Aparelho ───────────────────────────────────────────────────────
# Um aparelho só vira "conhecido" depois de ter um Pix aprovado nesta
# conta há pelo menos 24 h. Sem essa maturação, o fraudador "esquentaria"
# o aparelho com um Pix de R$ 1 e no minuto seguinte faria o grande.
DEVICE_TRUST_MATURATION_HOURS = 24
NEW_DEVICE_POINTS = 30
# Res. BCB 402/2024: aparelho nunca usado pelo cliente tem limite de
# R$ 200 por Pix e R$ 1.000 por dia.
NEW_DEVICE_TRANSACTION_LIMIT = 200 * REAIS
NEW_DEVICE_OVER_LIMIT_POINTS = 15
NEW_DEVICE_DAILY_LIMIT = 1_000 * REAIS

# Mesmo aparelho usado por vários CPFs pagadores. Dois pode ser casal
# dividindo o celular; três já é central de golpe.
DEVICE_WINDOW_DAYS = 30
DEVICE_SHARED_DOCUMENTS = 2
DEVICE_SHARED_POINTS = 15
DEVICE_FARM_DOCUMENTS = 3

# IP é sinal fraco: operadora de celular põe milhares de clientes atrás
# do mesmo IP (CGNAT). Só pesa com muitos CPFs, e pouco.
IP_WINDOW_HOURS = 24
IP_SHARED_DOCUMENTS = 5
IP_SHARED_POINTS = 10

# ─── Geolocalização ─────────────────────────────────────────────────
# Avião comercial voa a ~900 km/h e o tempo entre dois Pix inclui
# aeroporto: acima de 1.000 km/h não existe deslocamento físico.
IMPOSSIBLE_TRAVEL_SPEED_KMH = 1_000
# Abaixo de 100 km a diferença cabe no erro do GPS / IP de operadora.
IMPOSSIBLE_TRAVEL_MIN_DISTANCE_KM = 100
IMPOSSIBLE_TRAVEL_LOOKBACK_HOURS = 24
IMPOSSIBLE_TRAVEL_POINTS = 45

# ─── Contas ─────────────────────────────────────────────────────────
# Conta laranja nasce para receber um golpe e some em dias.
DESTINATION_VERY_NEW_DAYS = 7
DESTINATION_VERY_NEW_POINTS = 30
DESTINATION_NEW_DAYS = 30
DESTINATION_NEW_POINTS = 15

# Conta pagadora recém-aberta movimentando valor alto: identidade
# sintética ou conta aberta só para fazer giro.
SOURCE_VERY_NEW_DAYS = 2
SOURCE_VERY_NEW_POINTS = 35
SOURCE_NEW_DAYS = 7
SOURCE_NEW_POINTS = 25
SOURCE_NEW_MIN_AMOUNT = 1_000 * REAIS

# ─── Destino ────────────────────────────────────────────────────────
# Funil: muitos CPFs diferentes pagando a mesma conta de pessoa física
# em 24 h. É a assinatura do golpe em massa (falso parente, falsa venda).
# PJ recebe de muita gente por natureza (é um comércio), então o limiar
# é 10x maior.
# Só contam Pix a partir de FAN_IN_MIN_AMOUNT: o golpe em massa tira
# centenas ou milhares de cada vítima, e o vendedor ambulante (pipoca,
# feira) recebe de dezenas de clientes por dia em ticket baixo.
FAN_IN_WINDOW_HOURS = 24
FAN_IN_MIN_AMOUNT = 200 * REAIS
FAN_IN_PF_PAYERS = 5
FAN_IN_PF_HIGH_PAYERS = 10
FAN_IN_PJ_MULTIPLIER = 10
FAN_IN_POINTS = 35
FAN_IN_HIGH_POINTS = 50

# Reputação de rede: o destino já teve Pix barrado vindo de OUTROS
# pagadores. Se alguém foi barrado tentando pagar essa conta, a próxima
# vítima provavelmente está a caminho. Duas travas contra marcar inocente:
# - só conta rejeição causada pelo destino (risco de destino >= corte de
#   rejeição). Celular roubado tentando pagar a mãe da vítima é rejeitado
#   por aparelho novo, e isso não diz nada sobre a conta da mãe;
# - conta pagadores distintos, não rejeições: um só pagador insistindo
#   não leva o destino ao nível máximo, e "sujar" a conta de alguém de
#   propósito exige vários CPFs.
# É pontuação com janela, não blacklist permanente: lista dura se
# alimenta sozinha (marcada, tudo é rejeitado, nunca sai da lista).
FLAGGED_DESTINATION_WINDOW_DAYS = 30
FLAGGED_DESTINATION_POINTS = 35
FLAGGED_DESTINATION_HIGH_PAYERS = 3
FLAGGED_DESTINATION_HIGH_POINTS = 50

# ─── Cadeia de laranjas ─────────────────────────────────────────────
# Golpe raramente para na primeira conta: a vítima paga o laranja, o
# laranja repassa em minutos para outra conta (às vezes uma "conta
# central" que junta o dinheiro de vários laranjas), e dali o dinheiro
# some. O motor só enxerga o trecho da cadeia que passa pelos Pix que
# ele analisou, e olha um elo para cada lado:
#
# 1. O PAGADOR é um elo? Recebeu dinheiro nas últimas 24 h (aparece como
#    destino no nosso histórico) e agora está repassando boa parte dele.
#    - De vários pagadores distintos: é o formato do laranja, mas também
#      o da vaquinha do churrasco, então pesa pouco (25). Não vale para
#      PJ, que recebe de clientes e paga fornecedor por natureza.
#    - Se algum desses Pix de entrada já tinha sido considerado arriscado
#      pelo motor (risco de destino >= 40, ou seja, o próprio motor achou
#      que ESTA conta parecia laranja), repassar é lavar dinheiro de
#      golpe: 60, que rejeita sozinho. O dono do laranja passa no 2FA.
MULE_WINDOW_HOURS = 24
MULE_MIN_INBOUND_AMOUNT = 1_000 * REAIS
MULE_FORWARD_RATIO = 0.5
MULE_MIN_PAYERS = 3
MULE_PASS_THROUGH_POINTS = 25
MULE_SUSPICIOUS_INBOUND_SCORE = 40
MULE_DIRTY_MONEY_POINTS = 60
#
# 2. O DESTINO é um elo? Nos últimos 30 dias, algum Pix já pego como
#    repasse de laranja (MULE_PASS_THROUGH) saiu desta conta ou chegou
#    nela. Pagar a conta central de uma cadeia é o fim do golpe: 50,
#    que com o primeiro Pix para ela (+10) já rejeita.
MULE_LINK_WINDOW_DAYS = 30
MULE_LINK_POINTS = 50

# Primeiro Pix para este destino. Sozinho é fraco (todo mundo paga
# alguém pela primeira vez), por isso só acima de R$ 500 e com peso baixo.
NEW_COUNTERPARTY_MIN_AMOUNT = 500 * REAIS
NEW_COUNTERPARTY_POINTS = 10

# ─── Bônus de confiança ─────────────────────────────────────────────
# Já pagou esse destino pelo menos duas vezes, e a primeira faz mais de
# 7 dias: é o aluguel, a diarista, o fornecedor. É o que impede o motor
# de travar quem só quer pagar as contas.
TRUSTED_COUNTERPARTY_MIN_TRANSACTIONS = 2
TRUSTED_COUNTERPARTY_MIN_DAYS = 7
TRUSTED_COUNTERPARTY_BONUS = -30

# Pix para conta do mesmo titular: o dinheiro não sai da pessoa.
SELF_TRANSFER_BONUS = -25

# ...desde que a conta de destino seja mesmo da pessoa. O golpe da conta
# espelho abre, com os dados roubados da vítima, uma conta NO CPF DELA em
# outro banco, e depois esvazia a conta verdadeira para lá: para o motor,
# "mesmo titular". Por isso o mesmo CPF só vale como transferência entre
# contas próprias se a conta de destino tem 30 dias ou já recebeu deste
# cliente um Pix aprovado há pelo menos TRUSTED_COUNTERPARTY_MIN_DAYS.
# Fora isso é um Pix comum: conta nova, cadeia de laranjas, tudo vale.
SELF_TRANSFER_MIN_DESTINATION_DAYS = 30

# ─── Maturidade do pagador ──────────────────────────────────────────
# Robô de rajada e fazenda de aparelhos usam conta nova: conta aberta
# com dados de terceiros, ou comprada, e descartada depois do golpe. Uma
# conta com 6 meses e histórico aprovado pagando 5 amigos em 4 minutos é
# o racha do bar; três pessoas antigas no mesmo celular é uma família.
# Para o pagador maduro, essas duas regras pedem 2FA em vez de barrar,
# e só barram num volume que nenhum uso humano explica.
MATURE_ACCOUNT_DAYS = 180
MATURE_MIN_APPROVED = 5
VELOCITY_BOT_MATURE_PF_ATTEMPTS = 10
# Empresa paga folha e fornecedores em lote: dezenas em poucos minutos.
VELOCITY_BOT_MATURE_PJ_ATTEMPTS = 30
DEVICE_FARM_MATURE_POINTS = 40
# Cinco ou mais pagadores no mesmo aparelho em 30 dias não é família.
DEVICE_FARM_HARD_DOCUMENTS = 5
