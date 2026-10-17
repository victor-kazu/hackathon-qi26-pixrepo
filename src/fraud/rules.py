"""As regras antifraude. Cada uma é uma função pura: recebe os sinais e
devolve um RuleHit (disparou) ou None (não disparou).

Há três tipos de regra, e é a combinação deles que dá o "sweet spot"
entre barrar fraude e não travar o cliente bom:

• DURA — impõe uma decisão sozinha (`forced_status`). Fica para o que é
  obrigação (o limite noturno do BCB) ou padrão sem explicação legítima
  (10 Pix em 10 minutos, um aparelho com 3 pagadores).
• DE PONTOS — soma risco. Nenhum sinal fraco reprova sozinho; vários
  juntos, sim.
• DE CONFIANÇA — pontos NEGATIVOS. É o que faz o cliente com histórico
  bom não cair em 2FA por um valor um pouco acima do normal.

Todo `detail` leva o número que a regra viu e o limite contra o qual
comparou: é o que vai no metadata e o que explica a decisão.
"""

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from fraud import parameters as p
from fraud.signals import Signals
from models import Payer, PixAnalysis


@dataclass(frozen=True)
class RuleHit:
    code: str
    points: int
    forced_status: Optional[str] = None
    detail: dict = field(default_factory=dict)


def _is_night(hour: int) -> bool:
    return hour >= p.NIGHT_START_HOUR or hour < p.NIGHT_END_HOUR


def night_limit(s: Signals) -> Optional[RuleHit]:
    """R1 · Pessoa física à noite não passa de R$ 1.000 por Pix (regra do BCB).

    É obrigação regulatória, não calibragem — por isso é dura. Vale só
    para pagador CPF: o limite do BCB é para pessoa física. MEI também
    entra na regra, mas um CNPJ sozinho não diz se é MEI (seria preciso
    consultar a Receita, fora do escopo), e a escolha foi não punir toda
    empresa por isso.
    """
    if s.payer_document_type != Payer.CPF or not _is_night(s.local_hour):
        return None

    if s.amount <= p.NIGHT_LIMIT:
        return None

    return RuleHit(
        "NIGHT_LIMIT",
        0,
        PixAnalysis.REJECTED,
        {"amount": s.amount, "limit": p.NIGHT_LIMIT, "local_hour": s.local_hour},
    )


def high_amount(s: Signals) -> Optional[RuleHit]:
    """R2 · Valor alto que o pagador nunca fez antes.

    Valor alto não é fraude, mas é onde a fraude dói. Se o próprio
    pagador já teve um Pix aprovado desse tamanho, o valor tem precedente
    e a regra não dispara.
    """
    if s.amount <= p.HIGH_AMOUNT or s.amount <= s.payer_approved_max_amount:
        return None

    if s.amount > p.VERY_HIGH_AMOUNT:
        points, limit = p.VERY_HIGH_AMOUNT_POINTS, p.VERY_HIGH_AMOUNT
    else:
        points, limit = p.HIGH_AMOUNT_POINTS, p.HIGH_AMOUNT

    return RuleHit(
        "HIGH_AMOUNT",
        points,
        detail={"amount": s.amount, "limit": limit, "payer_max_approved": s.payer_approved_max_amount},
    )


def velocity(s: Signals) -> Optional[RuleHit]:
    """R3 · Rajada de Pix do mesmo pagador.

    Esvaziar uma conta tomada exige velocidade. Cinco Pix em dez minutos
    já é incomum (pede 2FA); dez não tem explicação de uso normal.
    Conta TODAS as tentativas, inclusive as reprovadas: insistir depois de
    uma recusa é, em si, o sinal.
    """
    count = s.payer_count_window
    detail = {"count": count, "window_minutes": p.VELOCITY_WINDOW_MINUTES}

    if count >= p.VELOCITY_REJECT_COUNT:
        return RuleHit("VELOCITY", 0, PixAnalysis.REJECTED, {**detail, "limit": p.VELOCITY_REJECT_COUNT})

    if count >= p.VELOCITY_TWO_FACTOR_COUNT:
        return RuleHit("VELOCITY", p.VELOCITY_TWO_FACTOR_POINTS, detail={**detail, "limit": p.VELOCITY_TWO_FACTOR_COUNT})

    return None


def split_payment(s: Signals) -> Optional[RuleHit]:
    """R4 · Fragmentação: vários Pix pequenos ao mesmo recebedor que, juntos, furam o teto.

    É o drible das regras de valor: quem sabe que R$ 1.000 à noite é o
    limite manda 3 × R$ 900. Cada Pix fica abaixo do teto; a soma, não.
    O teto comparado é o que valeria para um Pix único: o noturno para
    pessoa física à noite, o de valor alto no resto do tempo.
    """
    if s.payer_document_type == Payer.CPF and _is_night(s.local_hour):
        ceiling = p.NIGHT_LIMIT
    else:
        ceiling = p.HIGH_AMOUNT

    if s.same_destination_count_window < p.SPLIT_MIN_COUNT:
        return None

    if s.amount > ceiling or s.same_destination_sum_window <= ceiling:
        return None

    return RuleHit(
        "SPLIT_PAYMENT",
        p.SPLIT_POINTS,
        detail={
            "count": s.same_destination_count_window,
            "sum": s.same_destination_sum_window,
            "ceiling": ceiling,
            "window_minutes": p.SPLIT_WINDOW_MINUTES,
        },
    )


def daily_sum(s: Signals) -> Optional[RuleHit]:
    """R5 · O pagador movimentou no dia muito mais do que costuma.

    Pega o esvaziamento em parcelas espaçadas, que o velocity não vê. Com
    histórico, o teto é 3 × o maior dia aprovado do último mês; sem
    histórico, um teto fixo de R$ 10.000.
    """
    if s.payer_max_daily_approved > 0:
        limit = s.payer_max_daily_approved * p.DAILY_SUM_FACTOR
    else:
        limit = p.DAILY_SUM_WITHOUT_HISTORY

    if s.payer_sum_24h <= limit:
        return None

    return RuleHit(
        "DAILY_SUM",
        p.DAILY_SUM_POINTS,
        detail={"sum_24h": s.payer_sum_24h, "limit": limit, "max_daily_approved": s.payer_max_daily_approved},
    )


def out_of_profile(s: Signals) -> Optional[RuleHit]:
    """R6 · Valor muito acima do habitual DESTE pagador.

    R$ 3.000 é normal para um e anômalo para outro. Só vale com histórico
    suficiente (5 Pix aprovados) — com menos que isso, "média" é ruído.
    """
    if s.payer_approved_count < p.PROFILE_MIN_APPROVED:
        return None

    limit = s.payer_approved_average * p.PROFILE_FACTOR

    if s.amount <= limit:
        return None

    return RuleHit(
        "OUT_OF_PROFILE",
        p.PROFILE_POINTS,
        detail={"amount": s.amount, "average": s.payer_approved_average, "limit": limit},
    )


def new_destination_account(s: Signals) -> Optional[RuleHit]:
    """R7 · Conta recebedora recém-aberta — o perfil da conta laranja.

    Conta aberta com dados de terceiros só para receber e repassar costuma
    ser usada nos primeiros dias.

    Com menos de 1 dia e valor relevante, a regra EXIGE 2FA, e os pontos de
    confiança não a desfazem. O motivo é o golpe autorizado (falso parente,
    falso suporte): quem paga é o cliente verdadeiro, no aparelho de
    sempre. "Aparelho de confiança" prova quem PAGA, e não diz nada sobre
    quem RECEBE — deixar um compensar o outro aprovaria justamente o golpe.
    """
    age = s.destination_account_age_days

    if age < p.BRAND_NEW_DESTINATION_DAYS and s.amount > p.BRAND_NEW_DESTINATION_AMOUNT:
        return RuleHit(
            "NEW_DESTINATION_ACCOUNT",
            p.BRAND_NEW_DESTINATION_POINTS,
            PixAnalysis.TWO_FACTOR_REQUIRED,
            {"account_age_days": round(age, 2), "limit_days": p.BRAND_NEW_DESTINATION_DAYS},
        )

    if age < p.NEW_DESTINATION_DAYS:
        return RuleHit(
            "NEW_DESTINATION_ACCOUNT",
            p.NEW_DESTINATION_POINTS,
            detail={"account_age_days": round(age, 2), "limit_days": p.NEW_DESTINATION_DAYS},
        )

    return None


def destination_fan_in(s: Signals) -> Optional[RuleHit]:
    """R8 · Muitos pagadores diferentes para a mesma conta de pessoa física.

    É o rastro da conta laranja e do golpe em massa: várias vítimas
    pagando a mesma conta. Só para recebedor CPF — uma empresa recebe de
    muitos pagadores legitimamente, e tratar os dois igual seria travar
    todo comércio.
    """
    if s.destination_document_type != Payer.CPF:
        return None

    if s.destination_distinct_payers_24h < p.FAN_IN_PAYERS:
        return None

    return RuleHit(
        "DESTINATION_FAN_IN",
        p.FAN_IN_POINTS,
        detail={
            "distinct_payers": s.destination_distinct_payers_24h,
            "limit": p.FAN_IN_PAYERS,
            "window_hours": p.FAN_IN_WINDOW_HOURS,
        },
    )


def new_source_account(s: Signals) -> Optional[RuleHit]:
    """R9 · Conta pagadora recém-aberta movimentando valor relevante."""
    if s.source_account_age_days >= p.NEW_SOURCE_DAYS or s.amount <= p.NEW_SOURCE_AMOUNT:
        return None

    return RuleHit(
        "NEW_SOURCE_ACCOUNT",
        p.NEW_SOURCE_POINTS,
        detail={"account_age_days": round(s.source_account_age_days, 2), "limit_days": p.NEW_SOURCE_DAYS},
    )


def new_device(s: Signals) -> Optional[RuleHit]:
    """R10 · Primeiro Pix deste pagador neste aparelho.

    Trocar de aparelho é o primeiro passo do sequestro de conta. Sozinho
    não basta para 2FA (todo mundo troca de celular um dia); com valor
    alto, pesa mais.
    """
    if s.device_used_before_by_payer:
        return None

    if s.amount > p.NEW_DEVICE_HIGH_AMOUNT:
        points = p.NEW_DEVICE_HIGH_AMOUNT_POINTS
    else:
        points = p.NEW_DEVICE_POINTS

    return RuleHit("NEW_DEVICE", points, detail={"amount": s.amount})


def shared_device(s: Signals) -> Optional[RuleHit]:
    """R11 · O mesmo aparelho pagando por várias pessoas.

    Duas pessoas num aparelho pode ser família (pede 2FA); três ou mais em
    uma semana é a "fazenda" de contas do golpe organizado (reprova).
    """
    payers = s.device_distinct_payers_7d
    detail = {"distinct_payers": payers, "window_days": p.SHARED_DEVICE_WINDOW_DAYS}

    if payers >= p.SHARED_DEVICE_REJECT_PAYERS:
        return RuleHit("SHARED_DEVICE", 0, PixAnalysis.REJECTED, {**detail, "limit": p.SHARED_DEVICE_REJECT_PAYERS})

    if payers == 2:
        return RuleHit("SHARED_DEVICE", p.SHARED_DEVICE_TWO_PAYERS_POINTS, detail=detail)

    return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância na superfície da Terra (fórmula de haversine)."""
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def impossible_travel(s: Signals) -> Optional[RuleHit]:
    """R12 · Dois Pix em lugares que ninguém percorre naquele tempo.

    Acima de 900 km/h (velocidade de avião) entre o último Pix com
    localização e este, a sessão provavelmente não é a do dono. Distâncias
    abaixo de 100 km são ignoradas: o GPS do celular oscila, e uma
    oscilação de 2 km em 1 segundo daria uma "velocidade" absurda.
    """
    if s.latitude is None or s.previous_latitude is None:
        return None

    distance = distance_km(s.previous_latitude, s.previous_longitude, s.latitude, s.longitude)

    if distance < p.IMPOSSIBLE_TRAVEL_MIN_DISTANCE_KM:
        return None

    # Mesmo instante conta como um minuto, para não dividir por zero.
    hours = max(s.minutes_since_previous_location, 1) / 60
    speed = distance / hours

    if speed <= p.IMPOSSIBLE_TRAVEL_SPEED_KMH:
        return None

    return RuleHit(
        "IMPOSSIBLE_TRAVEL",
        p.IMPOSSIBLE_TRAVEL_POINTS,
        detail={
            "distance_km": round(distance, 1),
            "minutes": round(s.minutes_since_previous_location, 1),
            "speed_kmh": round(speed),
            "limit_kmh": p.IMPOSSIBLE_TRAVEL_SPEED_KMH,
        },
    )


def known_counterparty(s: Signals) -> Optional[RuleHit]:
    """C1 · O pagador já pagou este recebedor, com aprovação, mais de uma vez — há mais de 24 horas.

    O "há mais de 24 horas" é o que impede o fraudador de fabricar a
    própria confiança: sem ele, três Pix fragmentados para a mesma conta
    em uma hora transformariam o recebedor em "conhecido" a partir do
    terceiro, e o desconto anularia a regra de fragmentação. Relação de
    confiança é construída com tempo.
    """
    if s.counterparty_approved_count < p.KNOWN_COUNTERPARTY_MIN_APPROVED:
        return None

    return RuleHit("KNOWN_COUNTERPARTY", p.KNOWN_COUNTERPARTY_POINTS, detail={"approved_before": s.counterparty_approved_count})


def same_owner(s: Signals) -> Optional[RuleHit]:
    """C2 · O mesmo documento nos dois lados: dinheiro indo para a própria conta em outro lugar."""
    if not s.same_owner:
        return None

    return RuleHit("SAME_OWNER", p.SAME_OWNER_POINTS)


def trusted_device(s: Signals) -> Optional[RuleHit]:
    """C3 · Aparelho que o pagador usa há mais de 30 dias — o oposto do sequestro de conta."""
    if s.device_days_with_payer is None or s.device_days_with_payer < p.TRUSTED_DEVICE_DAYS:
        return None

    return RuleHit("TRUSTED_DEVICE", p.TRUSTED_DEVICE_POINTS, detail={"days_with_payer": round(s.device_days_with_payer)})


# A ordem é a da tabela do README. Todas rodam sempre: a análise grava
# TODAS as que dispararam, e não só a primeira.
RULES: List[Callable[[Signals], Optional[RuleHit]]] = [
    night_limit,
    high_amount,
    velocity,
    split_payment,
    daily_sum,
    out_of_profile,
    new_destination_account,
    destination_fan_in,
    new_source_account,
    new_device,
    shared_device,
    impossible_travel,
    known_counterparty,
    same_owner,
    trusted_device,
]
