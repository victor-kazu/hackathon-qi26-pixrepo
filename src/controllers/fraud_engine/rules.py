"""As regras antifraude. Cada uma lê os sinais e diz se disparou.

Toda regra pertence a uma de duas categorias, e a categoria importa
para a decisão (veja fraud_engine/policy.py):

  IDENTITY     "é mesmo o cliente quem está pagando?" O 2FA resolve.
  BENEFICIARY  "para onde vai esse dinheiro?" O 2FA NÃO resolve: no
               golpe de engenharia social quem paga é a própria vítima.

Regra dura (`hard=True`) rejeita sozinha. As outras somam pontos; os
bônus de confiança são pontos negativos.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from controllers.fraud_engine import parameters as p
from controllers.fraud_engine.signals import Signals

IDENTITY = "identity"
BENEFICIARY = "beneficiary"

# O repository procura este código nas regras já gravadas para achar os
# elos de uma cadeia (veja mule_link).
MULE_PASS_THROUGH = "MULE_PASS_THROUGH"


@dataclass(frozen=True)
class RuleHit:
    code: str
    category: str
    points: int
    hard: bool
    detail: str

    def to_dict(self) -> dict:
        return {
            "rule": self.code,
            "category": self.category,
            "points": self.points,
            "hard": self.hard,
            "detail": self.detail,
        }


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _reais(cents: int) -> str:
    value = f"{cents / 100:,.2f}"
    return "R$ " + value.replace(",", "X").replace(".", ",").replace("X", ".")


# ─── Regras duras ───────────────────────────────────────────────────


def velocity_bot(signals: Signals) -> Optional[RuleHit]:
    """Ritmo de robô barra sozinho, mas a régua depende de quem paga.

    Conta nova é o que o robô usa: 5 tentativas em 5 min já barram. Para o
    pagador maduro (conta antiga com histórico aprovado), 5 em 5 min é o
    racha do bar, e quem pede o 2FA é o VELOCITY_BURST; barrar só a partir
    de 10 (pessoa física) ou 30 (empresa pagando folha em lote).
    """
    attempts = signals.history.attempts_short_window + 1
    limit = _velocity_bot_limit(signals)
    if attempts >= limit:
        return RuleHit(
            "VELOCITY_BOT", IDENTITY, 0, True,
            f"{attempts} tentativas em {p.VELOCITY_SHORT_WINDOW_MINUTES} min (limite {limit - 1})",
        )
    return None


def device_farm(signals: Signals) -> Optional[RuleHit]:
    """Três pagadores num aparelho é central de golpe, a não ser que quem paga seja maduro.

    Conta antiga com histórico, num celular com 3 ou 4 pagadores, é o
    perfil da família que divide o aparelho: pesa 40 (pede 2FA) em vez de
    barrar. Com 5 ou mais, ou com pagador sem maturidade, continua barrando.
    """
    documents = signals.history.device_distinct_documents
    if documents < p.DEVICE_FARM_DOCUMENTS:
        return None

    detail = f"aparelho usado por {documents} CPFs/CNPJs pagadores em {p.DEVICE_WINDOW_DAYS} dias"
    if signals.is_mature_payer and documents < p.DEVICE_FARM_HARD_DOCUMENTS:
        return RuleHit(
            "DEVICE_FARM", IDENTITY, p.DEVICE_FARM_MATURE_POINTS, False,
            detail + "; pagador com conta antiga e histórico, então pede 2FA em vez de barrar",
        )

    return RuleHit("DEVICE_FARM", IDENTITY, 0, True, detail)


def night_hard_limit(signals: Signals) -> Optional[RuleHit]:
    amount = signals.facts.amount
    if signals.is_night and signals.source_is_pf and amount > p.NIGHT_PF_HARD_LIMIT:
        return RuleHit(
            "NIGHT_HARD_LIMIT", IDENTITY, 0, True,
            f"{_reais(amount)} às {signals.local_hour}h, acima do teto noturno de {_reais(p.NIGHT_PF_HARD_LIMIT)}",
        )
    return None


def night_total_hard_limit(signals: Signals) -> Optional[RuleHit]:
    """A noite somada passou do teto de coação: barra como um Pix único desse valor barraria."""
    total = _night_total(signals)
    if total is None or total <= p.NIGHT_PF_HARD_LIMIT:
        return None

    # Um Pix sozinho acima do teto já é o NIGHT_HARD_LIMIT.
    if signals.facts.amount > p.NIGHT_PF_HARD_LIMIT:
        return None

    return RuleHit(
        "NIGHT_TOTAL_HARD_LIMIT", IDENTITY, 0, True,
        f"{_reais(total)} saíram desde as {p.NIGHT_START_HOUR}h com este Pix, acima do teto noturno de "
        f"{_reais(p.NIGHT_PF_HARD_LIMIT)}",
    )


def daily_limit(signals: Signals) -> Optional[RuleHit]:
    """Passou do teto diário: pede 2FA, porque quem precisa provar algo é o pagador."""
    exposure = signals.history.exposure_24h + signals.facts.amount
    uses_customer_limit = (
        signals.customer_daily_limit is not None and signals.source_is_pf and not signals.is_self_transfer
    )
    if uses_customer_limit:
        limit = signals.customer_daily_limit
        label = (
            f"teto do cliente de {_reais(limit)} ({p.DAILY_LIMIT_HISTORY_MULTIPLIER}x o maior dia em "
            f"{p.PROFILE_WINDOW_DAYS} dias, {_reais(signals.history.max_daily_outflow)})"
        )
    else:
        limit = signals.daily_limit
        label = f"teto de {_reais(limit)}"

    if exposure > limit:
        return RuleHit(
            "DAILY_LIMIT", IDENTITY, p.DAILY_LIMIT_POINTS, False,
            f"{_reais(exposure)} em 24 h com este Pix, acima do {label}",
        )
    return None


def new_device_daily_limit(signals: Signals) -> Optional[RuleHit]:
    if not signals.is_new_device:
        return None

    exposure = signals.history.device_exposure_24h + signals.facts.amount
    if exposure > p.NEW_DEVICE_DAILY_LIMIT:
        return RuleHit(
            "NEW_DEVICE_DAILY_LIMIT", IDENTITY, 0, True,
            f"aparelho novo somando {_reais(exposure)} em 24 h, acima de {_reais(p.NEW_DEVICE_DAILY_LIMIT)}",
        )
    return None


def impossible_travel_other_device(signals: Signals) -> Optional[RuleHit]:
    if _is_impossible_travel(signals) and signals.travel_device_changed:
        return RuleHit(
            "IMPOSSIBLE_TRAVEL_OTHER_DEVICE", IDENTITY, 0, True,
            f"{signals.travel_distance_km:.0f} km desde o último Pix a {signals.travel_speed_kmh:.0f} km/h, em outro aparelho",
        )
    return None


# ─── Identidade (o 2FA resolve) ─────────────────────────────────────


def velocity_burst(signals: Signals) -> Optional[RuleHit]:
    short_attempts = signals.history.attempts_short_window + 1
    hour_attempts = signals.history.attempts_long_window + 1

    # Rajada curta e volume na hora medem o mesmo fenômeno: vale só a mais forte.
    if short_attempts >= p.VELOCITY_BURST_ATTEMPTS:
        points = p.VELOCITY_BURST_POINTS
        if short_attempts >= p.VELOCITY_STRONG_BURST_ATTEMPTS:
            points = p.VELOCITY_STRONG_BURST_POINTS
        return RuleHit(
            "VELOCITY_BURST", IDENTITY, points, False,
            f"{short_attempts} tentativas em {p.VELOCITY_SHORT_WINDOW_MINUTES} min",
        )
    if hour_attempts >= p.VELOCITY_HOUR_ATTEMPTS:
        return RuleHit(
            "VELOCITY_BURST", IDENTITY, p.VELOCITY_HOUR_POINTS, False,
            f"{hour_attempts} tentativas em {p.VELOCITY_LONG_WINDOW_MINUTES} min",
        )
    return None


def probing_after_rejection(signals: Signals) -> Optional[RuleHit]:
    rejections = signals.history.rejected_recent
    if rejections > 0:
        points = min(rejections * p.PROBING_POINTS_PER_REJECTION, p.PROBING_MAX_POINTS)
        return RuleHit(
            "PROBING_AFTER_REJECTION", IDENTITY, points, False,
            f"{rejections} Pix rejeitado(s) desta conta na última hora",
        )
    return None


def night_high_value(signals: Signals) -> Optional[RuleHit]:
    amount = signals.facts.amount
    if signals.is_night and signals.source_is_pf and amount > p.NIGHT_PF_LIMIT:
        return RuleHit(
            "NIGHT_HIGH_VALUE", IDENTITY, p.NIGHT_HIGH_VALUE_POINTS, False,
            f"{_reais(amount)} às {signals.local_hour}h, acima do limite noturno de {_reais(p.NIGHT_PF_LIMIT)}",
        )
    return None


def night_total(signals: Signals) -> Optional[RuleHit]:
    """Drenagem espalhada de madrugada: vários Pix abaixo do limite, para destinos diferentes."""
    total = _night_total(signals)
    if total is None or total <= p.NIGHT_TOTAL_SOFT_LIMIT or total > p.NIGHT_PF_HARD_LIMIT:
        return None

    # Pix acima do limite noturno já pesa pelo NIGHT_HIGH_VALUE: não conta duas vezes.
    if signals.facts.amount > p.NIGHT_PF_LIMIT:
        return None

    return RuleHit(
        "NIGHT_TOTAL", IDENTITY, p.NIGHT_TOTAL_POINTS, False,
        f"{_reais(total)} saíram desde as {p.NIGHT_START_HOUR}h com este Pix, acima de "
        f"{_reais(p.NIGHT_TOTAL_SOFT_LIMIT)}",
    )


def new_device(signals: Signals) -> Optional[RuleHit]:
    if not signals.is_new_device:
        return None

    points = p.NEW_DEVICE_POINTS
    detail = "aparelho sem Pix aprovado nesta conta há mais de 24 h"
    if signals.facts.amount > p.NEW_DEVICE_TRANSACTION_LIMIT:
        points = points + p.NEW_DEVICE_OVER_LIMIT_POINTS
        detail = detail + f", valor acima de {_reais(p.NEW_DEVICE_TRANSACTION_LIMIT)}"

    return RuleHit("NEW_DEVICE", IDENTITY, points, False, detail)


def impossible_travel(signals: Signals) -> Optional[RuleHit]:
    if _is_impossible_travel(signals) and not signals.travel_device_changed:
        return RuleHit(
            "IMPOSSIBLE_TRAVEL", IDENTITY, p.IMPOSSIBLE_TRAVEL_POINTS, False,
            f"{signals.travel_distance_km:.0f} km desde o último Pix a {signals.travel_speed_kmh:.0f} km/h",
        )
    return None


def new_source_account(signals: Signals) -> Optional[RuleHit]:
    age = signals.source_account_age_days
    if signals.facts.amount < p.SOURCE_NEW_MIN_AMOUNT:
        return None

    if age < p.SOURCE_VERY_NEW_DAYS:
        return RuleHit(
            "NEW_SOURCE_ACCOUNT", IDENTITY, p.SOURCE_VERY_NEW_POINTS, False,
            f"conta pagadora aberta há {_decimal(age)} dias movimentando valor alto",
        )
    if age < p.SOURCE_NEW_DAYS:
        return RuleHit(
            "NEW_SOURCE_ACCOUNT", IDENTITY, p.SOURCE_NEW_POINTS, False,
            f"conta pagadora aberta há {_decimal(age)} dias movimentando valor alto",
        )
    return None


def high_value_without_profile(signals: Signals) -> Optional[RuleHit]:
    # Com perfil, quem cuida do valor é o PROFILE_AMOUNT_SPIKE.
    if signals.profile_ratio is not None or signals.is_self_transfer:
        return None

    multiplier = 1 if signals.source_is_pf else p.HIGH_VALUE_PJ_MULTIPLIER
    amount = signals.facts.amount
    threshold = p.HIGH_VALUE_AMOUNT * multiplier
    if amount < threshold:
        return None

    return RuleHit(
        "HIGH_VALUE_WITHOUT_PROFILE", IDENTITY, p.HIGH_VALUE_POINTS, False,
        f"{_reais(amount)} a partir de {_reais(threshold)}, sem histórico para comparar "
        f"(menos de {p.PROFILE_MIN_TRANSACTIONS} Pix aprovados)",
    )


def split_payment(signals: Signals) -> Optional[RuleHit]:
    if signals.is_self_transfer:
        return None

    parts = signals.history.destination_parts_recent + 1
    total = signals.history.destination_amount_recent + signals.facts.amount
    if parts < p.SPLIT_MIN_PARTS:
        return None

    # Um pedaço acima do limite já é pego pela regra do próprio limite.
    limit, limit_name = _split_limit(signals)
    if signals.facts.amount <= limit < total:
        return RuleHit(
            "SPLIT_PAYMENT", IDENTITY, p.SPLIT_POINTS, False,
            f"{parts} Pix para o mesmo destino em {p.SPLIT_WINDOW_MINUTES} min somando {_reais(total)}, "
            f"acima do {limit_name} de {_reais(limit)}",
        )
    return None


def shared_device(signals: Signals) -> Optional[RuleHit]:
    documents = signals.history.device_distinct_documents
    if p.DEVICE_SHARED_DOCUMENTS <= documents < p.DEVICE_FARM_DOCUMENTS:
        return RuleHit(
            "SHARED_DEVICE", IDENTITY, p.DEVICE_SHARED_POINTS, False,
            f"aparelho usado por {documents} pagadores em {p.DEVICE_WINDOW_DAYS} dias",
        )
    return None


def shared_ip(signals: Signals) -> Optional[RuleHit]:
    documents = signals.history.ip_distinct_documents
    if documents >= p.IP_SHARED_DOCUMENTS:
        return RuleHit(
            "SHARED_IP", IDENTITY, p.IP_SHARED_POINTS, False,
            f"IP usado por {documents} pagadores em {p.IP_WINDOW_HOURS} h",
        )
    return None


# ─── Destino (o 2FA não resolve) ────────────────────────────────────


def new_destination_account(signals: Signals) -> Optional[RuleHit]:
    age = signals.destination_account_age_days
    if signals.is_self_transfer:
        return None

    if age < p.DESTINATION_VERY_NEW_DAYS:
        return RuleHit(
            "NEW_DESTINATION_ACCOUNT", BENEFICIARY, p.DESTINATION_VERY_NEW_POINTS, False,
            f"conta destino aberta há {_decimal(age)} dias",
        )
    if age < p.DESTINATION_NEW_DAYS:
        return RuleHit(
            "NEW_DESTINATION_ACCOUNT", BENEFICIARY, p.DESTINATION_NEW_POINTS, False,
            f"conta destino aberta há {_decimal(age)} dias",
        )
    return None


def destination_fan_in(signals: Signals) -> Optional[RuleHit]:
    if signals.facts.amount < p.FAN_IN_MIN_AMOUNT:
        return None

    payers = signals.history.destination_distinct_payers
    threshold = p.FAN_IN_PF_PAYERS
    high_threshold = p.FAN_IN_PF_HIGH_PAYERS
    if not signals.destination_is_pf:
        threshold = threshold * p.FAN_IN_PJ_MULTIPLIER
        high_threshold = high_threshold * p.FAN_IN_PJ_MULTIPLIER

    if payers >= high_threshold:
        points = p.FAN_IN_HIGH_POINTS
    elif payers >= threshold:
        points = p.FAN_IN_POINTS
    else:
        return None

    return RuleHit(
        "DESTINATION_FAN_IN", BENEFICIARY, points, False,
        f"destino recebeu de {payers} pagadores distintos em {p.FAN_IN_WINDOW_HOURS} h, "
        f"a partir de {_reais(p.FAN_IN_MIN_AMOUNT)} cada",
    )


def mule_pass_through(signals: Signals) -> Optional[RuleHit]:
    """O pagador recebeu dinheiro nas últimas 24 h e está repassando: é um elo da cadeia."""
    history = signals.history
    if signals.is_self_transfer or history.source_inbound_amount < p.MULE_MIN_INBOUND_AMOUNT:
        return None

    forwarded = history.exposure_24h + signals.facts.amount
    if forwarded < history.source_inbound_amount * p.MULE_FORWARD_RATIO:
        return None

    received = (
        f"conta recebeu {_reais(history.source_inbound_amount)} de {history.source_inbound_payers} pagador(es) "
        f"em {p.MULE_WINDOW_HOURS} h e já repassou {_reais(forwarded)} com este Pix"
    )
    if history.source_inbound_suspicious_payers > 0:
        return RuleHit(
            MULE_PASS_THROUGH, BENEFICIARY, p.MULE_DIRTY_MONEY_POINTS, False,
            received + f"; {history.source_inbound_suspicious_payers} desses Pix de entrada já tinham risco de destino alto",
        )
    if signals.source_is_pf and history.source_inbound_payers >= p.MULE_MIN_PAYERS:
        return RuleHit(MULE_PASS_THROUGH, BENEFICIARY, p.MULE_PASS_THROUGH_POINTS, False, received)
    return None


def mule_link(signals: Signals) -> Optional[RuleHit]:
    """O destino já apareceu numa cadeia: recebeu de um laranja ou é um laranja."""
    links = signals.history.destination_mule_links
    if signals.is_self_transfer or links <= 0:
        return None

    return RuleHit(
        "MULE_LINK", BENEFICIARY, p.MULE_LINK_POINTS, False,
        f"destino ligado a {links} conta(s) pega(s) repassando dinheiro de golpe em {p.MULE_LINK_WINDOW_DAYS} dias",
    )


def mule_account_blocked(signals: Signals) -> Optional[RuleHit]:
    """Conta pega repassando dinheiro de golpe fica bloqueada: não envia nem recebe.

    Vale até para conta própria: mandar para outra conta do mesmo CPF é a
    saída mais simples para o laranja barrado.
    """
    history = signals.history
    caught = f"teve um repasse de dinheiro de golpe barrado nos últimos {p.MULE_BLOCK_WINDOW_DAYS} dias"
    if history.source_caught_mule:
        return RuleHit("MULE_ACCOUNT_BLOCKED", BENEFICIARY, 0, True, f"conta pagadora {caught}: bloqueada para enviar")
    if history.destination_caught_mule:
        return RuleHit("MULE_ACCOUNT_BLOCKED", BENEFICIARY, 0, True, f"conta destino {caught}: bloqueada para receber")
    return None


def flagged_destination(signals: Signals) -> Optional[RuleHit]:
    payers = signals.history.destination_flagged_payers
    if payers <= 0:
        return None

    points = p.FLAGGED_DESTINATION_POINTS
    if payers >= p.FLAGGED_DESTINATION_HIGH_PAYERS:
        points = p.FLAGGED_DESTINATION_HIGH_POINTS

    return RuleHit(
        "FLAGGED_DESTINATION", BENEFICIARY, points, False,
        f"{payers} outro(s) pagador(es) tiveram Pix para este destino rejeitado por risco do destino "
        f"em {p.FLAGGED_DESTINATION_WINDOW_DAYS} dias",
    )


def profile_amount_spike(signals: Signals) -> Optional[RuleHit]:
    ratio = signals.profile_ratio
    if ratio is None:
        return None

    # Repassando o que acabou de receber de vários pagadores (a vaquinha), o valor
    # fora do perfil é explicado pela entrada, e o MULE_PASS_THROUGH já pesou isso:
    # não conta duas vezes. O repasse de dinheiro sujo (+60) não entra aqui.
    forwarding = mule_pass_through(signals)
    if forwarding is not None and forwarding.points == p.MULE_PASS_THROUGH_POINTS:
        return None

    if ratio > p.PROFILE_EXTREME_RATIO:
        points = p.PROFILE_EXTREME_POINTS
    elif ratio > p.PROFILE_SPIKE_RATIO:
        points = p.PROFILE_SPIKE_POINTS
    else:
        return None

    return RuleHit(
        "PROFILE_AMOUNT_SPIKE", BENEFICIARY, points, False,
        f"valor {_decimal(ratio)}x a mediana do cliente ({_reais(signals.profile_median)})",
    )


def structuring(signals: Signals) -> Optional[RuleHit]:
    # Valores iguais para o mesmo destino já são SPLIT_PAYMENT: não conta duas vezes.
    if split_payment(signals) is not None:
        return None

    repetitions = signals.history.same_amount_recent + 1
    if repetitions >= p.STRUCTURING_MIN_REPETITIONS:
        return RuleHit(
            "STRUCTURING", BENEFICIARY, p.STRUCTURING_POINTS, False,
            f"{repetitions} Pix de {_reais(signals.facts.amount)} na última hora",
        )
    return None


def daily_exposure(signals: Signals) -> Optional[RuleHit]:
    limit = signals.daily_limit
    exposure = signals.history.exposure_24h + signals.facts.amount
    if limit * p.DAILY_EXPOSURE_SOFT_RATIO < exposure <= limit:
        return RuleHit(
            "DAILY_EXPOSURE", BENEFICIARY, p.DAILY_EXPOSURE_POINTS, False,
            f"{_reais(exposure)} em 24 h com este Pix, mais da metade do teto de {_reais(limit)}",
        )
    return None


def new_counterparty(signals: Signals) -> Optional[RuleHit]:
    if signals.is_self_transfer or signals.history.counterparty_approved_count > 0:
        return None

    if signals.facts.amount > p.NEW_COUNTERPARTY_MIN_AMOUNT:
        return RuleHit(
            "NEW_COUNTERPARTY", BENEFICIARY, p.NEW_COUNTERPARTY_POINTS, False,
            f"primeiro Pix para este destino, acima de {_reais(p.NEW_COUNTERPARTY_MIN_AMOUNT)}",
        )
    return None


# ─── Bônus de confiança ─────────────────────────────────────────────


def trusted_counterparty(signals: Signals) -> Optional[RuleHit]:
    count = signals.history.counterparty_approved_count
    days = signals.counterparty_relationship_days
    if count >= p.TRUSTED_COUNTERPARTY_MIN_TRANSACTIONS and days is not None and days >= p.TRUSTED_COUNTERPARTY_MIN_DAYS:
        return RuleHit(
            "TRUSTED_COUNTERPARTY", BENEFICIARY, p.TRUSTED_COUNTERPARTY_BONUS, False,
            f"{count} Pix aprovados para este destino, o primeiro há {days:.0f} dias",
        )
    return None


def self_transfer(signals: Signals) -> Optional[RuleHit]:
    if signals.is_self_transfer:
        return RuleHit(
            "SELF_TRANSFER", BENEFICIARY, p.SELF_TRANSFER_BONUS, False,
            "pagador e recebedor têm o mesmo CPF/CNPJ, e a conta de destino é antiga ou já conhecida",
        )
    return None


def _velocity_bot_limit(signals: Signals) -> int:
    if not signals.is_mature_payer:
        return p.VELOCITY_BOT_ATTEMPTS

    if signals.source_is_pf:
        return p.VELOCITY_BOT_MATURE_PF_ATTEMPTS

    return p.VELOCITY_BOT_MATURE_PJ_ATTEMPTS


def _night_total(signals: Signals) -> Optional[int]:
    """Quanto saiu na noite, com este Pix. None quando a regra não se aplica (dia, PJ, conta própria)."""
    if not signals.is_night or not signals.source_is_pf or signals.is_self_transfer:
        return None

    return signals.history.night_outflow + signals.facts.amount


def _split_limit(signals: Signals) -> Tuple[int, str]:
    """O limite que os pedaços somados estão tentando driblar."""
    if signals.is_night and signals.source_is_pf:
        return p.NIGHT_PF_LIMIT, "limite noturno"

    multiplier = 1 if signals.source_is_pf else p.HIGH_VALUE_PJ_MULTIPLIER
    return p.HIGH_VALUE_AMOUNT * multiplier, "valor alto"


def _is_impossible_travel(signals: Signals) -> bool:
    if signals.travel_speed_kmh is None:
        return False

    return (
        signals.travel_distance_km > p.IMPOSSIBLE_TRAVEL_MIN_DISTANCE_KM
        and signals.travel_speed_kmh > p.IMPOSSIBLE_TRAVEL_SPEED_KMH
    )


RULES: List[Callable[[Signals], Optional[RuleHit]]] = [
    velocity_bot,
    device_farm,
    night_hard_limit,
    night_total_hard_limit,
    new_device_daily_limit,
    impossible_travel_other_device,
    mule_account_blocked,
    velocity_burst,
    probing_after_rejection,
    night_high_value,
    night_total,
    new_device,
    impossible_travel,
    new_source_account,
    high_value_without_profile,
    daily_limit,
    split_payment,
    shared_device,
    shared_ip,
    new_destination_account,
    destination_fan_in,
    flagged_destination,
    mule_pass_through,
    mule_link,
    profile_amount_spike,
    structuring,
    daily_exposure,
    new_counterparty,
    trusted_counterparty,
    self_transfer,
]


def evaluate_rules(signals: Signals) -> List[RuleHit]:
    hits = []
    for rule in RULES:
        hit = rule(signals)
        if hit is not None:
            hits.append(hit)

    return hits
