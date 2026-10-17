from datetime import datetime
from typing import Optional, Set, Tuple

from sqlalchemy import Date, cast, distinct, func

from constants import BANK_TIMEZONE
from database import Context
from models import PixAnalysis, PixTransaction


class PixTransactionRepository:
    """Grava o Pix e responde as perguntas de histórico do motor.

    Toda consulta de histórico recebe um `until` — o instante do Pix em
    análise — e só enxerga o que aconteceu ATÉ ele (point-in-time). Um Pix
    com data posterior que por acaso chegou antes não entra na conta: o
    motor julga cada Pix com o que se sabia naquele instante.
    """

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def get_by_transaction_id(self, transaction_id: str) -> Optional[PixTransaction]:
        return self.session.query(PixTransaction).filter(PixTransaction.transaction_id == transaction_id).first()

    def create(self, fields: dict) -> PixTransaction:
        pix = PixTransaction(**fields)
        self.session.add(pix)
        self.session.flush()
        return pix

    # ── Sinais do próprio pagador ───────────────────────────────────

    def count_by_payer(self, payer_id: int, since: datetime, until: datetime) -> int:
        """Todas as tentativas, qualquer que tenha sido a decisão."""
        return (
            self.session.query(func.count(PixTransaction.id))
            .filter(
                PixTransaction.payer_id == payer_id,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .scalar()
        )

    def sum_by_payer(self, payer_id: int, since: datetime, until: datetime) -> int:
        # SUM de BIGINT volta NUMERIC no PostgreSQL (Decimal no Python): int() traz de volta para centavos.
        return int(
            self.session.query(func.coalesce(func.sum(PixTransaction.amount), 0))
            .filter(
                PixTransaction.payer_id == payer_id,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .scalar()
        )

    def count_and_sum_to_destination(
        self, payer_id: int, destination: Tuple[str, str, str], since: datetime, until: datetime
    ) -> Tuple[int, int]:
        participant, branch, account_number = destination
        count, total = (
            self.session.query(func.count(PixTransaction.id), func.coalesce(func.sum(PixTransaction.amount), 0))
            .filter(
                PixTransaction.payer_id == payer_id,
                PixTransaction.destination_participant == participant,
                PixTransaction.destination_branch == branch,
                PixTransaction.destination_account_number == account_number,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .one()
        )
        return count, int(total)

    def approved_stats(self, payer_id: int, until: datetime) -> Tuple[int, int, int]:
        """Quantidade, média e maior valor dos Pix APROVADOS do pagador.

        Só os aprovados formam o perfil: um Pix que pediu 2FA pode não ter
        sido concluído, e um reprovado não aconteceu. Contar os dois daria
        ao fraudador um jeito de "treinar" o perfil da vítima.
        """
        count, average, maximum = (
            self.session.query(
                func.count(PixTransaction.id),
                func.coalesce(func.avg(PixTransaction.amount), 0),
                func.coalesce(func.max(PixTransaction.amount), 0),
            )
            .join(PixAnalysis, PixAnalysis.pix_transaction_id == PixTransaction.id)
            .filter(
                PixTransaction.payer_id == payer_id,
                PixAnalysis.status == PixAnalysis.APPROVED,
                PixTransaction.transaction_date <= until,
            )
            .one()
        )
        return count, int(average), int(maximum)

    def max_daily_approved(self, payer_id: int, since: datetime, until: datetime) -> int:
        """O maior total aprovado num único dia (dia de São Paulo) entre `since` e `until`."""
        day = cast(func.timezone(BANK_TIMEZONE, PixTransaction.transaction_date), Date)

        daily_totals = (
            self.session.query(func.sum(PixTransaction.amount).label("total"))
            .join(PixAnalysis, PixAnalysis.pix_transaction_id == PixTransaction.id)
            .filter(
                PixTransaction.payer_id == payer_id,
                PixAnalysis.status == PixAnalysis.APPROVED,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .group_by(day)
            .subquery()
        )

        return int(self.session.query(func.coalesce(func.max(daily_totals.c.total), 0)).scalar())

    def counterparty_approved_count(self, payer_id: int, destination: Tuple[str, str, str], until: datetime) -> int:
        participant, branch, account_number = destination
        return (
            self.session.query(func.count(PixTransaction.id))
            .join(PixAnalysis, PixAnalysis.pix_transaction_id == PixTransaction.id)
            .filter(
                PixTransaction.payer_id == payer_id,
                PixAnalysis.status == PixAnalysis.APPROVED,
                PixTransaction.destination_participant == participant,
                PixTransaction.destination_branch == branch,
                PixTransaction.destination_account_number == account_number,
                PixTransaction.transaction_date <= until,
            )
            .scalar()
        )

    def previous_location(self, payer_id: int, until: datetime) -> Optional[Tuple[float, float, datetime]]:
        row = (
            self.session.query(PixTransaction.latitude, PixTransaction.longitude, PixTransaction.transaction_date)
            .filter(
                PixTransaction.payer_id == payer_id,
                PixTransaction.latitude.isnot(None),
                PixTransaction.transaction_date <= until,
            )
            .order_by(PixTransaction.transaction_date.desc(), PixTransaction.id.desc())
            .first()
        )

        if row is None:
            return None

        return float(row.latitude), float(row.longitude), row.transaction_date

    # ── Sinais de rede: o recebedor e o aparelho ────────────────────

    def destination_payers(self, destination: Tuple[str, str, str], since: datetime, until: datetime) -> Set[int]:
        participant, branch, account_number = destination
        rows = (
            self.session.query(distinct(PixTransaction.payer_id))
            .filter(
                PixTransaction.destination_participant == participant,
                PixTransaction.destination_branch == branch,
                PixTransaction.destination_account_number == account_number,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .all()
        )
        return {row[0] for row in rows}

    def device_payers(self, device_id: str, since: datetime, until: datetime) -> Set[int]:
        rows = (
            self.session.query(distinct(PixTransaction.payer_id))
            .filter(
                PixTransaction.device_id == device_id,
                PixTransaction.transaction_date > since,
                PixTransaction.transaction_date <= until,
            )
            .all()
        )
        return {row[0] for row in rows}

    def device_first_use_by_payer(self, payer_id: int, device_id: str, until: datetime) -> Optional[datetime]:
        return (
            self.session.query(func.min(PixTransaction.transaction_date))
            .filter(
                PixTransaction.payer_id == payer_id,
                PixTransaction.device_id == device_id,
                PixTransaction.transaction_date <= until,
            )
            .scalar()
        )
