"""Todos os números da política antifraude, num lugar só.

Cada parâmetro tem o porquê do lado. Valor em dinheiro está sempre em
CENTAVOS, como o `amount` do contrato. Mudar qualquer número aqui muda a
política, então a POLICY_VERSION sobe junto: ela vai gravada em cada
análise, e é o que permite explicar uma decisão antiga com a regra que
valia no dia.
"""

POLICY_VERSION = "2026.10.17-v4"

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

# ─── Teto diário ────────────────────────────────────────────────────
# Exposição de 24 h (aprovados + 2FA + o Pix atual). Contenção de dano
# se tudo o mais falhar: celular roubado e destravado esvazia conta em
# minutos.
DAILY_PF_HARD_LIMIT = 20_000 * REAIS
DAILY_PJ_HARD_LIMIT = 200_000 * REAIS
# Antes do teto duro, metade dele já pesa no score.
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
FAN_IN_WINDOW_HOURS = 24
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
