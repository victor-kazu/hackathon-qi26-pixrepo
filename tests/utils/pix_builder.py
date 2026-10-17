from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from tests.utils.random_generator import RandomGenerator

SAO_PAULO = timezone(timedelta(hours=-3))

# Uma quarta-feira às 14h: longe do horário noturno, para que nenhum
# teste esbarre na regra noturna sem querer.
BASE_DATE = datetime(2026, 10, 14, 14, 0, tzinfo=SAO_PAULO)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def new_device() -> str:
    return f"dvc-{uuid4().hex[:12]}"


@dataclass
class Account:
    """Uma conta de teste. Cada teste cria as suas, com documento e número aleatórios.

    É isso que isola um teste do outro sem precisar limpar o banco: o
    histórico que um teste cria é de pagadores, aparelhos e recebedores
    que nenhum outro teste usa.
    """

    document_number: str = field(default_factory=RandomGenerator.generate_cpf)
    participant: str = "60701190"
    branch: str = "0001"
    account_number: str = field(default_factory=lambda: str(uuid4().int)[:10])
    opening_date: datetime = BASE_DATE - timedelta(days=400)

    @staticmethod
    def company(**kwargs) -> "Account":
        return Account(document_number=RandomGenerator.generate_cnpj(), **kwargs)


def pix_payload(
    payer: Account,
    receiver: Account,
    amount: int = 15_000,
    when: datetime = BASE_DATE,
    device_id: Optional[str] = None,
    geolocation: Optional[tuple] = None,
    transaction_id: Optional[str] = None,
) -> dict:
    """Um Pix no formato exato do contrato (slide 19), com cada campo trocável."""
    if device_id is None:
        device_id = new_device()

    if transaction_id is None:
        transaction_id = f"pix-{uuid4()}"

    source = {"device_id": device_id, "channel": "app", "ip": "198.51.100.23"}
    if geolocation is not None:
        source["geolocation"] = {"latitude": geolocation[0], "longitude": geolocation[1]}

    return {
        "id": transaction_id,
        "amount": amount,
        "transaction_date": iso(when),
        "source_account": {
            "branch": payer.branch,
            "account_number": payer.account_number,
            "opening_date": iso(payer.opening_date),
            "owner": {"document_number": payer.document_number},
        },
        "destination_account": {
            "participant": receiver.participant,
            "branch": receiver.branch,
            "account_number": receiver.account_number,
            "opening_date": iso(receiver.opening_date),
            "owner": {"document_number": receiver.document_number},
        },
        "source": source,
    }
