from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import and_, func, text

from database import Context
from models import PixAnalysis, PixAnalysisRuleHit, PixAnalysisStatus, PixTransaction

# Só para as anotações de tipo: em tempo de execução o repository não
# depende do controller (que é quem depende dele).
if TYPE_CHECKING:
    from controllers.fraud_engine.policy import Decision
    from controllers.fraud_engine.signals import TransactionFacts

# Status que podem ter tirado dinheiro da conta. O 2FA entra porque o
# motor não fica sabendo se o cliente confirmou: para teto e exposição,
# o conservador é contar.
MONEY_OUT_STATUSES = [PixAnalysisStatus.APPROVED, PixAnalysisStatus.TWO_FACTOR_REQUIRED]

PROFILE_MAX_TRANSACTIONS = 500


class PixTransactionRepository:
    """Grava os Pix e responde as perguntas de histórico das regras.

    Toda consulta de histórico olha só para o que aconteceu ATÉ a data
    do Pix analisado (`transaction_date <= facts.transaction_date`), e
    nunca para o relógio do servidor. Assim um Pix sempre é julgado com
    o que se sabia no instante dele, e os testes podem montar histórico
    com datas no passado.
    """

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    # ─── Escrita ────────────────────────────────────────────────────

    def lock_source_account(self, facts: "TransactionFacts") -> None:
        """Serializa os Pix da mesma conta pagadora até o commit.

        Sem isso, cinco Pix simultâneos da mesma conta leriam o mesmo
        histórico vazio e passariam todos pela regra de velocity. O lock
        é por conta, então contas diferentes continuam em paralelo.
        """
        account_key = f"{facts.source_branch}:{facts.source_account_number}"
        self.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:account_key))"), {"account_key": account_key})

    def create(
        self,
        facts: "TransactionFacts",
        raw_payload: dict,
        decision: "Decision",
        analysis_metadata: dict,
        processing_time_ms: float,
    ) -> PixAnalysis:
        pix_transaction = PixTransaction()
        pix_transaction.pix_id = facts.pix_id
        pix_transaction.amount = facts.amount
        pix_transaction.transaction_date = facts.transaction_date
        pix_transaction.utc_offset_minutes = int(facts.transaction_date.utcoffset().total_seconds() // 60)
        pix_transaction.source_branch = facts.source_branch
        pix_transaction.source_account_number = facts.source_account_number
        pix_transaction.source_opening_date = facts.source_opening_date
        pix_transaction.source_document_number = facts.source_document_number
        pix_transaction.destination_participant = facts.destination_participant
        pix_transaction.destination_branch = facts.destination_branch
        pix_transaction.destination_account_number = facts.destination_account_number
        pix_transaction.destination_opening_date = facts.destination_opening_date
        pix_transaction.destination_document_number = facts.destination_document_number
        pix_transaction.device_id = facts.device_id
        pix_transaction.channel = facts.channel
        pix_transaction.ip = facts.ip
        pix_transaction.latitude = facts.latitude
        pix_transaction.longitude = facts.longitude
        pix_transaction.raw_payload = raw_payload

        pix_analysis = PixAnalysis()
        pix_analysis.analysis_key = str(uuid4())
        pix_analysis.pix_transaction = pix_transaction
        pix_analysis.status = self.get_status(decision.status)
        pix_analysis.risk_score = decision.risk_score
        pix_analysis.identity_score = decision.identity_score
        pix_analysis.beneficiary_score = decision.beneficiary_score
        pix_analysis.decision_reason = decision.reason
        pix_analysis.policy_version = analysis_metadata["policy_version"]
        pix_analysis.processing_time_ms = round(processing_time_ms, 3)
        pix_analysis.analysis_metadata = analysis_metadata

        for hit in decision.hits:
            rule_hit = PixAnalysisRuleHit()
            rule_hit.rule_code = hit.code
            rule_hit.category = hit.category
            rule_hit.points = hit.points
            rule_hit.is_hard_rule = hit.hard
            rule_hit.detail = hit.detail
            pix_analysis.rule_hits.append(rule_hit)

        self.session.add(pix_analysis)
        return pix_analysis

    # ─── Leitura ────────────────────────────────────────────────────

    def get_analysis_by_pix_id(self, pix_id: str) -> Optional[PixAnalysis]:
        return (
            self.session.query(PixAnalysis)
            .join(PixAnalysis.pix_transaction)
            .filter(PixTransaction.pix_id == pix_id)
            .first()
        )

    def get_status(self, enumerator: str) -> PixAnalysisStatus:
        return self.session.query(PixAnalysisStatus).filter(PixAnalysisStatus.enumerator == enumerator).one()

    # ─── Histórico do pagador ───────────────────────────────────────

    def count_source_approved(self, facts: "TransactionFacts") -> int:
        query = self._source_query(facts, None, [PixAnalysisStatus.APPROVED])
        return query.with_entities(func.count(PixTransaction.id)).scalar()

    def count_source_attempts(self, facts: "TransactionFacts", window: timedelta, statuses: List[str] = None) -> int:
        query = self._source_query(facts, window, statuses)
        return query.with_entities(func.count(PixTransaction.id)).scalar()

    def count_source_same_amount(self, facts: "TransactionFacts", window: timedelta) -> int:
        query = self._source_query(facts, window).filter(PixTransaction.amount == facts.amount)
        return query.with_entities(func.count(PixTransaction.id)).scalar()

    def list_source_approved_amounts(self, facts: "TransactionFacts", window: timedelta) -> List[int]:
        query = (
            self._source_query(facts, window, [PixAnalysisStatus.APPROVED])
            .with_entities(PixTransaction.amount)
            .order_by(PixTransaction.transaction_date.desc())
            .limit(PROFILE_MAX_TRANSACTIONS)
        )
        return [row.amount for row in query.all()]

    def sum_source_amounts(self, facts: "TransactionFacts", window: timedelta, device_id: str = None) -> int:
        query = self._source_query(facts, window, MONEY_OUT_STATUSES)
        if device_id is not None:
            query = query.filter(PixTransaction.device_id == device_id)

        # SUM de BIGINT volta NUMERIC (Decimal) do Postgres; o motor trabalha em centavos inteiros.
        return int(query.with_entities(func.coalesce(func.sum(PixTransaction.amount), 0)).scalar())

    def get_destination_recent_stats(self, facts: "TransactionFacts", window: timedelta) -> Tuple[int, int]:
        """Quantos Pix e quanto dinheiro saíram desta conta para este destino na janela."""
        query = self._source_query(facts, window, MONEY_OUT_STATUSES).filter(self._destination_filter(facts))
        count, total = query.with_entities(
            func.count(PixTransaction.id), func.coalesce(func.sum(PixTransaction.amount), 0)
        ).one()
        return count, int(total)

    def get_counterparty_stats(self, facts: "TransactionFacts") -> Tuple[int, Optional[datetime]]:
        query = self._source_query(facts, None, [PixAnalysisStatus.APPROVED]).filter(self._destination_filter(facts))
        count, first_date = query.with_entities(
            func.count(PixTransaction.id), func.min(PixTransaction.transaction_date)
        ).one()
        return count, first_date

    def is_device_trusted(self, facts: "TransactionFacts", maturation: timedelta) -> bool:
        query = (
            self._source_query(facts, None, [PixAnalysisStatus.APPROVED])
            .filter(PixTransaction.device_id == facts.device_id)
            .filter(PixTransaction.transaction_date <= facts.transaction_date - maturation)
        )
        return query.with_entities(PixTransaction.id).first() is not None

    def get_previous_location(self, facts: "TransactionFacts", window: timedelta):
        """O último Pix com GPS desta conta na janela (latitude, longitude, data, aparelho), ou None."""
        return (
            self._source_query(facts, window)
            .filter(PixTransaction.latitude.isnot(None), PixTransaction.longitude.isnot(None))
            .with_entities(
                PixTransaction.latitude,
                PixTransaction.longitude,
                PixTransaction.transaction_date,
                PixTransaction.device_id,
            )
            .order_by(PixTransaction.transaction_date.desc())
            .first()
        )

    # ─── Aparelho e rede ────────────────────────────────────────────

    def count_distinct_documents_by_device(self, facts: "TransactionFacts", window: timedelta) -> int:
        """Quantos pagadores distintos usaram este aparelho, contando o atual."""
        query = self.session.query(func.count(func.distinct(PixTransaction.source_document_number))).filter(
            PixTransaction.device_id == facts.device_id,
            PixTransaction.source_document_number != facts.source_document_number,
            *self._window_filter(facts, window),
        )
        return query.scalar() + 1

    def count_distinct_documents_by_ip(self, facts: "TransactionFacts", window: timedelta) -> int:
        query = self.session.query(func.count(func.distinct(PixTransaction.source_document_number))).filter(
            PixTransaction.ip == facts.ip,
            PixTransaction.source_document_number != facts.source_document_number,
            *self._window_filter(facts, window),
        )
        return query.scalar() + 1

    # ─── Histórico do destino ───────────────────────────────────────

    def count_destination_distinct_payers(self, facts: "TransactionFacts", window: timedelta) -> int:
        """Quantos pagadores distintos mandaram Pix para este destino, contando o atual."""
        query = self.session.query(func.count(func.distinct(PixTransaction.source_document_number))).filter(
            self._destination_filter(facts),
            PixTransaction.source_document_number != facts.source_document_number,
            *self._window_filter(facts, window),
        )
        return query.scalar() + 1

    def count_destination_flagged_payers(self, facts: "TransactionFacts", window: timedelta, min_beneficiary_score: int) -> int:
        """Outros pagadores que tiveram Pix para este destino rejeitado por causa do destino."""
        query = (
            self.session.query(func.count(func.distinct(PixTransaction.source_document_number)))
            .join(PixTransaction.analysis)
            .join(PixAnalysis.status)
            .filter(
                self._destination_filter(facts),
                PixTransaction.source_document_number != facts.source_document_number,
                PixAnalysisStatus.enumerator == PixAnalysisStatus.REJECTED,
                PixAnalysis.beneficiary_score >= min_beneficiary_score,
                *self._window_filter(facts, window),
            )
        )
        return query.scalar()

    # ─── Filtros ────────────────────────────────────────────────────

    def _source_query(self, facts: "TransactionFacts", window: Optional[timedelta], statuses: List[str] = None):
        query = self.session.query(PixTransaction).filter(
            PixTransaction.source_branch == facts.source_branch,
            PixTransaction.source_account_number == facts.source_account_number,
            *self._window_filter(facts, window),
        )
        if statuses is not None:
            query = query.join(PixTransaction.analysis).join(PixAnalysis.status).filter(
                PixAnalysisStatus.enumerator.in_(statuses)
            )

        return query

    def _destination_filter(self, facts: "TransactionFacts"):
        return and_(
            PixTransaction.destination_participant == facts.destination_participant,
            PixTransaction.destination_branch == facts.destination_branch,
            PixTransaction.destination_account_number == facts.destination_account_number,
        )

    def _window_filter(self, facts: "TransactionFacts", window: Optional[timedelta]) -> list:
        filters = [PixTransaction.transaction_date <= facts.transaction_date]
        if window is not None:
            filters.append(PixTransaction.transaction_date > facts.transaction_date - window)

        return filters
