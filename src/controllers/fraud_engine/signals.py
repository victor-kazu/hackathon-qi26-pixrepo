"""Os sinais que as regras leem: o Pix atual cruzado com o histórico.

Nada aqui fala com banco. O controller busca o histórico pelo
repository, entrega um `History` pronto, e `build_signals` só faz
contas. É isso que deixa as regras testáveis em unidade, sem API e sem
Postgres.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from statistics import median
from typing import List, Optional

from controllers.fraud_engine import parameters

CPF_LENGTH = 11
EARTH_RADIUS_KM = 6371.0

# Quando o Pix chega em UTC puro ("Z"), não dá pra saber o fuso do
# cliente. Assume-se o de Brasília, que é o da maioria dos clientes.
DEFAULT_LOCAL_TIMEZONE = timezone(timedelta(hours=-3))


@dataclass(frozen=True)
class TransactionFacts:
    """O Pix que chegou, já normalizado (datas com fuso, documento só com dígitos)."""

    pix_id: str
    amount: int
    transaction_date: datetime
    source_branch: str
    source_account_number: str
    source_opening_date: datetime
    source_document_number: str
    destination_participant: str
    destination_branch: str
    destination_account_number: str
    destination_opening_date: datetime
    destination_document_number: str
    device_id: str
    channel: str
    ip: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass(frozen=True)
class PreviousLocation:
    latitude: float
    longitude: float
    transaction_date: datetime
    device_id: str


@dataclass(frozen=True)
class History:
    """O que o banco sabe sobre os envolvidos ANTES deste Pix.

    "Tentativas" contam qualquer status (inclusive rejeitado): tentar de
    novo também é comportamento. Já perfil e exposição só contam o que
    pode ter saído de verdade (aprovado e 2FA), e o perfil só aprovado,
    para que tentativas do fraudador não virem o "normal" do cliente.
    """

    approved_count_total: int = 0
    attempts_short_window: int = 0
    attempts_long_window: int = 0
    rejected_recent: int = 0
    same_amount_recent: int = 0
    destination_parts_recent: int = 0
    destination_amount_recent: int = 0
    approved_amounts_profile: List[int] = field(default_factory=list)
    exposure_24h: int = 0
    counterparty_approved_count: int = 0
    counterparty_first_approved_at: Optional[datetime] = None
    device_trusted: bool = False
    device_exposure_24h: int = 0
    device_distinct_documents: int = 1
    ip_distinct_documents: int = 1
    previous_location: Optional[PreviousLocation] = None
    destination_distinct_payers: int = 1
    destination_flagged_payers: int = 0


@dataclass(frozen=True)
class Signals:
    facts: TransactionFacts
    history: History
    local_hour: int
    is_night: bool
    source_is_pf: bool
    destination_is_pf: bool
    is_self_transfer: bool
    source_account_age_days: float
    destination_account_age_days: float
    has_history: bool
    is_new_device: bool
    profile_median: Optional[int]
    profile_ratio: Optional[float]
    counterparty_relationship_days: Optional[float]
    travel_distance_km: Optional[float]
    travel_speed_kmh: Optional[float]
    travel_device_changed: bool

    def to_dict(self) -> dict:
        """O retrato dos sinais que vai para a metadata da análise."""
        return {
            "amount": self.facts.amount,
            "local_hour": self.local_hour,
            "is_night": self.is_night,
            "source_is_pf": self.source_is_pf,
            "destination_is_pf": self.destination_is_pf,
            "is_self_transfer": self.is_self_transfer,
            "source_account_age_days": round(self.source_account_age_days, 2),
            "destination_account_age_days": round(self.destination_account_age_days, 2),
            "has_history": self.has_history,
            "is_new_device": self.is_new_device,
            "device_trusted": self.history.device_trusted,
            "attempts_last_5min": self.history.attempts_short_window,
            "attempts_last_1h": self.history.attempts_long_window,
            "rejected_last_1h": self.history.rejected_recent,
            "same_amount_last_1h": self.history.same_amount_recent,
            "same_destination_pix_last_1h": self.history.destination_parts_recent,
            "same_destination_amount_last_1h": self.history.destination_amount_recent,
            "profile_transactions": len(self.history.approved_amounts_profile),
            "profile_median": self.profile_median,
            "profile_ratio": _round_or_none(self.profile_ratio),
            "exposure_last_24h": self.history.exposure_24h,
            "counterparty_approved_count": self.history.counterparty_approved_count,
            "counterparty_relationship_days": _round_or_none(self.counterparty_relationship_days),
            "device_distinct_documents_30d": self.history.device_distinct_documents,
            "ip_distinct_documents_24h": self.history.ip_distinct_documents,
            "travel_distance_km": _round_or_none(self.travel_distance_km),
            "travel_speed_kmh": _round_or_none(self.travel_speed_kmh),
            "destination_distinct_payers_24h": self.history.destination_distinct_payers,
            "destination_flagged_payers_30d": self.history.destination_flagged_payers,
        }


def build_signals(facts: TransactionFacts, history: History) -> Signals:
    local_date = local_datetime(facts.transaction_date)
    has_history = history.approved_count_total > 0

    # Sem nenhum Pix aprovado antes, o aparelho não é "novo" nem
    # "conhecido": é desconhecido. Cold start não é suspeita.
    is_new_device = has_history and not history.device_trusted

    profile_median = None
    profile_ratio = None
    if len(history.approved_amounts_profile) >= parameters.PROFILE_MIN_TRANSACTIONS:
        profile_median = int(median(history.approved_amounts_profile))
        profile_ratio = facts.amount / max(profile_median, parameters.PROFILE_MEDIAN_FLOOR)

    counterparty_relationship_days = None
    if history.counterparty_first_approved_at is not None:
        counterparty_relationship_days = _days_between(history.counterparty_first_approved_at, facts.transaction_date)

    travel_distance_km = None
    travel_speed_kmh = None
    travel_device_changed = False
    previous = history.previous_location
    if previous is not None and facts.latitude is not None and facts.longitude is not None:
        travel_distance_km = haversine_km(previous.latitude, previous.longitude, facts.latitude, facts.longitude)
        elapsed_hours = (facts.transaction_date - previous.transaction_date).total_seconds() / 3600
        # Dois Pix no mesmo segundo: trata como 1 minuto, para não dividir por zero.
        elapsed_hours = max(elapsed_hours, 1 / 60)
        travel_speed_kmh = travel_distance_km / elapsed_hours
        travel_device_changed = previous.device_id != facts.device_id

    return Signals(
        facts=facts,
        history=history,
        local_hour=local_date.hour,
        is_night=is_night_hour(local_date.hour),
        source_is_pf=is_pf(facts.source_document_number),
        destination_is_pf=is_pf(facts.destination_document_number),
        is_self_transfer=facts.source_document_number == facts.destination_document_number,
        source_account_age_days=_days_between(facts.source_opening_date, facts.transaction_date),
        destination_account_age_days=_days_between(facts.destination_opening_date, facts.transaction_date),
        has_history=has_history,
        is_new_device=is_new_device,
        profile_median=profile_median,
        profile_ratio=profile_ratio,
        counterparty_relationship_days=counterparty_relationship_days,
        travel_distance_km=travel_distance_km,
        travel_speed_kmh=travel_speed_kmh,
        travel_device_changed=travel_device_changed,
    )


def local_datetime(moment: datetime) -> datetime:
    """A hora de parede do cliente, pelo fuso que veio no próprio Pix."""
    if moment.utcoffset() == timedelta(0):
        return moment.astimezone(DEFAULT_LOCAL_TIMEZONE)

    return moment


def is_night_hour(hour: int) -> bool:
    return hour >= parameters.NIGHT_START_HOUR or hour < parameters.NIGHT_END_HOUR


def is_pf(document_number: str) -> bool:
    return len(document_number) == CPF_LENGTH


def haversine_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Distância em linha reta sobre a superfície da Terra."""
    delta_latitude = radians(latitude_b - latitude_a)
    delta_longitude = radians(longitude_b - longitude_a)

    a = sin(delta_latitude / 2) ** 2 + cos(radians(latitude_a)) * cos(radians(latitude_b)) * sin(delta_longitude / 2) ** 2

    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def _days_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400


def _round_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None

    return round(value, 2)
