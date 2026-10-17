"""Todos os números da política antifraude, num lugar só.

Cada número aqui tem uma justificativa na tabela de regras do README.
Mudou um parâmetro, suba a RULES_VERSION: ela vai gravada em cada
análise, e é o que permite dizer, meses depois, com qual régua um Pix
foi julgado.

Valores em CENTAVOS, como o `amount` do contrato.
"""

RULES_VERSION = "2026.10.17-1"

# ── Como os pontos viram decisão ────────────────────────────────────
TWO_FACTOR_SCORE = 50
REJECT_SCORE = 100

# ── R1 · Limite noturno (regra do BCB para pessoa física) ──────────
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6
NIGHT_LIMIT = 100_000  # R$ 1.000,00

# ── R2 · Valor alto sem precedente ──────────────────────────────────
HIGH_AMOUNT = 500_000  # R$ 5.000,00
HIGH_AMOUNT_POINTS = 30
VERY_HIGH_AMOUNT = 2_000_000  # R$ 20.000,00
VERY_HIGH_AMOUNT_POINTS = 50

# ── R3 · Velocity: Pix do mesmo pagador em poucos minutos ───────────
VELOCITY_WINDOW_MINUTES = 10
VELOCITY_TWO_FACTOR_COUNT = 5
VELOCITY_TWO_FACTOR_POINTS = 50
VELOCITY_REJECT_COUNT = 10

# ── R4 · Fragmentação: valor quebrado para passar por baixo do teto ─
SPLIT_WINDOW_MINUTES = 60
SPLIT_MIN_COUNT = 3
SPLIT_POINTS = 50

# ── R5 · Montante do dia contra o histórico do pagador ──────────────
DAILY_SUM_WINDOW_HOURS = 24
DAILY_SUM_HISTORY_DAYS = 30
DAILY_SUM_FACTOR = 3
DAILY_SUM_WITHOUT_HISTORY = 1_000_000  # R$ 10.000,00
DAILY_SUM_POINTS = 30

# ── R6 · Perfil: valor muito acima do habitual ──────────────────────
PROFILE_MIN_APPROVED = 5
PROFILE_FACTOR = 5
PROFILE_POINTS = 30

# ── R7 · Conta recebedora recém-aberta (sinal de conta laranja) ─────
NEW_DESTINATION_DAYS = 7
NEW_DESTINATION_POINTS = 25
BRAND_NEW_DESTINATION_DAYS = 1
BRAND_NEW_DESTINATION_AMOUNT = 100_000  # R$ 1.000,00
BRAND_NEW_DESTINATION_POINTS = 50

# ── R8 · Muitos pagadores diferentes para a mesma conta (CPF) ───────
FAN_IN_WINDOW_HOURS = 24
FAN_IN_PAYERS = 5
FAN_IN_POINTS = 40

# ── R9 · Conta pagadora recém-aberta com valor relevante ────────────
NEW_SOURCE_DAYS = 7
NEW_SOURCE_AMOUNT = 100_000  # R$ 1.000,00
NEW_SOURCE_POINTS = 30

# ── R10 · Aparelho que este pagador nunca usou ──────────────────────
NEW_DEVICE_POINTS = 20
NEW_DEVICE_HIGH_AMOUNT = 100_000  # R$ 1.000,00
NEW_DEVICE_HIGH_AMOUNT_POINTS = 35

# ── R11 · Um aparelho, vários pagadores ─────────────────────────────
SHARED_DEVICE_WINDOW_DAYS = 7
SHARED_DEVICE_TWO_PAYERS_POINTS = 30
SHARED_DEVICE_REJECT_PAYERS = 3

# ── R12 · Viagem impossível entre dois Pix ──────────────────────────
IMPOSSIBLE_TRAVEL_SPEED_KMH = 900
IMPOSSIBLE_TRAVEL_MIN_DISTANCE_KM = 100
IMPOSSIBLE_TRAVEL_POINTS = 50

# ── Sinais de confiança (tiram pontos) ──────────────────────────────
KNOWN_COUNTERPARTY_MIN_APPROVED = 2
KNOWN_COUNTERPARTY_MIN_AGE_HOURS = 24
KNOWN_COUNTERPARTY_POINTS = -30
SAME_OWNER_POINTS = -40
TRUSTED_DEVICE_DAYS = 30
TRUSTED_DEVICE_POINTS = -20
