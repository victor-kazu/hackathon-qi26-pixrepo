from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Signals:
    """Tudo o que o motor sabe sobre um Pix, já calculado.

    O motor não fala com o banco: recebe este retrato pronto e decide.
    Quem monta o retrato é o controller, com as consultas do repository.
    Separar assim é o que deixa cada regra testável com um objeto feito à
    mão, sem banco nem HTTP.

    Toda contagem de histórico olha só para Pix com `transaction_date` até
    o instante deste (point-in-time) e já INCLUI o próprio Pix: "5 Pix em
    10 minutos" quer dizer este e mais quatro.
    """

    amount: int
    local_hour: int
    payer_document_type: str
    destination_document_type: str
    same_owner: bool
    source_account_age_days: float
    destination_account_age_days: float

    payer_count_window: int
    same_destination_count_window: int
    same_destination_sum_window: int
    payer_sum_24h: int
    payer_max_daily_approved: int

    payer_approved_count: int
    payer_approved_average: int
    payer_approved_max_amount: int

    destination_distinct_payers_24h: int
    counterparty_approved_count: int

    device_used_before_by_payer: bool
    device_days_with_payer: Optional[float]
    device_distinct_payers_7d: int

    latitude: Optional[float]
    longitude: Optional[float]
    previous_latitude: Optional[float]
    previous_longitude: Optional[float]
    minutes_since_previous_location: Optional[float]
