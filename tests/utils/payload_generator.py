import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from tests.utils.random_generator import RandomGenerator

BRASILIA = timezone(timedelta(hours=-3))


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


class PayloadGenerator:
    @staticmethod
    def create_pix_payload(
        amount: int = 15_000,
        transaction_date: datetime = None,
        source_account: dict = None,
        destination_account: dict = None,
        device_id: str = None,
        ip: str = None,
        geolocation: dict = None,
        channel: str = "app",
    ) -> dict:
        """Um Pix válido e sem nenhum sinal de risco, com qualquer parte trocada a pedido.

        Contas, aparelho e IP saem aleatórios: os testes dividem o mesmo
        banco, e um teste não pode herdar o histórico de outro. Quem
        quer montar histórico passa as mesmas contas em vários Pix.

        A data padrão é ontem às 14h no horário de Brasília: no passado
        (o motor recusa Pix no futuro) e fora da faixa noturna.
        """
        if transaction_date is None:
            transaction_date = PayloadGenerator.daytime(days_ago=1)

        if source_account is None:
            source_account = PayloadGenerator.create_account()

        if destination_account is None:
            destination_account = PayloadGenerator.create_account(participant="60701190")

        if device_id is None:
            device_id = f"dvc-{uuid4().hex[:12]}"

        if ip is None:
            ip = f"10.{random.randrange(256)}.{random.randrange(256)}.{random.randrange(1, 255)}"

        source = {"device_id": device_id, "channel": channel, "ip": ip}
        if geolocation is not None:
            source["geolocation"] = geolocation

        return {
            "id": f"pix-{uuid4()}",
            "amount": amount,
            "transaction_date": iso(transaction_date),
            "source_account": source_account,
            "destination_account": destination_account,
            "source": source,
        }

    @staticmethod
    def create_account(
        participant: str = None,
        opening_date: datetime = None,
        document_number: str = None,
    ) -> dict:
        """Uma conta com dois anos de vida, para não acionar regra de conta nova sem querer."""
        if opening_date is None:
            opening_date = datetime.now(BRASILIA) - timedelta(days=730)

        if document_number is None:
            document_number = RandomGenerator.generate_cpf()

        account = {
            "branch": "0001",
            "account_number": str(random.randrange(10_000_000, 99_999_999)),
            "opening_date": iso(opening_date),
            "owner": {"document_number": document_number},
        }
        if participant is not None:
            account["participant"] = participant

        return account

    @staticmethod
    def daytime(days_ago: int = 1, hour: int = 14, minute: int = 0) -> datetime:
        moment = datetime.now(BRASILIA) - timedelta(days=days_ago)
        return moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
