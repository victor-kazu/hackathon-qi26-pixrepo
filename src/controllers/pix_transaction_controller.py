from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Tuple

from controllers.base_controller import BaseController
from dtos import PixAnalysisDTO
from engine import parameters as p
from engine.policy import REJECTED, decide
from engine.signals import History, TransactionFacts, build_signals
from errors import (
    AccountOpenedAfterTransaction,
    FutureTransactionDate,
    InvalidDate,
    InvalidDocumentNumber,
    NotFoundPixTransaction,
)
from repositories import PixTransactionRepository
from utils.document_number import is_valid_document, only_digits

# Relógio de quem chama e o nosso nunca batem no segundo. Dois minutos
# cobrem a diferença sem abrir espaço para plantar histórico no futuro.
FUTURE_TOLERANCE_MINUTES = 2


class PixTransactionController(BaseController):
    """Recebe um Pix, junta o histórico, pede a decisão ao motor e grava tudo."""

    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)

    def analyze(self, payload: dict) -> Tuple[dict, bool]:
        """Devolve a análise e se ela foi criada agora.

        O mesmo `id` chegando de novo é retry, e retry é normal no Pix
        (timeout de rede, cliente apertando duas vezes). A resposta é a
        análise já gravada: analisar de novo daria outra decisão, porque
        o primeiro Pix já entrou no histórico e contaria na velocity.
        """
        started_at = perf_counter()

        existing_analysis = self.pix_transaction_repository.get_analysis_by_pix_id(payload["id"])
        if existing_analysis is not None:
            return PixAnalysisDTO.obj_to_dict(existing_analysis), False

        facts = self._to_facts(payload)

        self.pix_transaction_repository.lock_source_account(facts)

        # Outro request com o mesmo id pode ter gravado enquanto este
        # esperava o lock.
        existing_analysis = self.pix_transaction_repository.get_analysis_by_pix_id(facts.pix_id)
        if existing_analysis is not None:
            return PixAnalysisDTO.obj_to_dict(existing_analysis), False

        signals = build_signals(facts, self._collect_history(facts))
        decision = decide(signals)

        analysis_metadata = decision.to_metadata(signals)
        processing_time_ms = (perf_counter() - started_at) * 1000
        analysis_metadata["processing_time_ms"] = round(processing_time_ms, 3)

        self.logger.info(f"Pix {facts.pix_id} analisado: {decision.status} ({decision.risk_score} pontos)")

        pix_analysis = self.pix_transaction_repository.create(
            facts, payload, decision, analysis_metadata, processing_time_ms
        )
        pix_analysis_dto = PixAnalysisDTO.obj_to_dict(pix_analysis)
        self.session.commit()

        return pix_analysis_dto, True

    def get_by_pix_id(self, pix_id: str) -> dict:
        pix_analysis = self.pix_transaction_repository.get_analysis_by_pix_id(pix_id)

        if pix_analysis is None:
            raise NotFoundPixTransaction(pix_id)

        return PixAnalysisDTO.obj_to_dict(pix_analysis)

    def _collect_history(self, facts: TransactionFacts) -> History:
        repository = self.pix_transaction_repository
        counterparty_count, counterparty_first_date = repository.get_counterparty_stats(facts)

        return History(
            approved_count_total=repository.count_source_approved(facts),
            attempts_short_window=repository.count_source_attempts(
                facts, timedelta(minutes=p.VELOCITY_SHORT_WINDOW_MINUTES)
            ),
            attempts_long_window=repository.count_source_attempts(
                facts, timedelta(minutes=p.VELOCITY_LONG_WINDOW_MINUTES)
            ),
            rejected_recent=repository.count_source_attempts(
                facts, timedelta(minutes=p.PROBING_WINDOW_MINUTES), [REJECTED]
            ),
            same_amount_recent=repository.count_source_same_amount(
                facts, timedelta(minutes=p.STRUCTURING_WINDOW_MINUTES)
            ),
            approved_amounts_profile=repository.list_source_approved_amounts(
                facts, timedelta(days=p.PROFILE_WINDOW_DAYS)
            ),
            exposure_24h=repository.sum_source_amounts(facts, timedelta(hours=24)),
            counterparty_approved_count=counterparty_count,
            counterparty_first_approved_at=counterparty_first_date,
            device_trusted=repository.is_device_trusted(
                facts, timedelta(hours=p.DEVICE_TRUST_MATURATION_HOURS)
            ),
            device_exposure_24h=repository.sum_source_amounts(facts, timedelta(hours=24), facts.device_id),
            device_distinct_documents=repository.count_distinct_documents_by_device(
                facts, timedelta(days=p.DEVICE_WINDOW_DAYS)
            ),
            ip_distinct_documents=repository.count_distinct_documents_by_ip(
                facts, timedelta(hours=p.IP_WINDOW_HOURS)
            ),
            previous_location=repository.get_previous_location(
                facts, timedelta(hours=p.IMPOSSIBLE_TRAVEL_LOOKBACK_HOURS)
            ),
            destination_distinct_payers=repository.count_destination_distinct_payers(
                facts, timedelta(hours=p.FAN_IN_WINDOW_HOURS)
            ),
            destination_rejections_by_others=repository.count_destination_rejections_by_others(
                facts, timedelta(days=p.FLAGGED_DESTINATION_WINDOW_DAYS)
            ),
        )

    def _to_facts(self, payload: dict) -> TransactionFacts:
        """Confere o que o schema não sabe conferir e normaliza o Pix.

        O schema garantiu os formatos. Aqui ficam as perguntas sobre o
        mundo: o CPF existe? A data existe no calendário? A conta foi
        aberta antes do Pix? O Pix não está no futuro?
        """
        source_account = payload["source_account"]
        destination_account = payload["destination_account"]
        source = payload["source"]
        geolocation = source.get("geolocation") or {}

        transaction_date = self._parse_datetime("transaction_date", payload["transaction_date"])
        source_opening_date = self._parse_datetime("source_account.opening_date", source_account["opening_date"])
        destination_opening_date = self._parse_datetime(
            "destination_account.opening_date", destination_account["opening_date"]
        )

        latest_accepted = datetime.now(timezone.utc) + timedelta(minutes=FUTURE_TOLERANCE_MINUTES)
        if transaction_date > latest_accepted:
            raise FutureTransactionDate(payload["transaction_date"], FUTURE_TOLERANCE_MINUTES)

        if source_opening_date > transaction_date:
            raise AccountOpenedAfterTransaction("source_account.opening_date")

        if destination_opening_date > transaction_date:
            raise AccountOpenedAfterTransaction("destination_account.opening_date")

        source_document_number = self._parse_document(
            "source_account.owner.document_number", source_account["owner"]["document_number"]
        )
        destination_document_number = self._parse_document(
            "destination_account.owner.document_number", destination_account["owner"]["document_number"]
        )

        return TransactionFacts(
            pix_id=payload["id"],
            amount=payload["amount"],
            transaction_date=transaction_date,
            source_branch=source_account["branch"],
            source_account_number=source_account["account_number"],
            source_opening_date=source_opening_date,
            source_document_number=source_document_number,
            destination_participant=destination_account["participant"],
            destination_branch=destination_account["branch"],
            destination_account_number=destination_account["account_number"],
            destination_opening_date=destination_opening_date,
            destination_document_number=destination_document_number,
            device_id=source["device_id"],
            channel=source["channel"],
            ip=source["ip"],
            latitude=geolocation.get("latitude"),
            longitude=geolocation.get("longitude"),
        )

    def _parse_datetime(self, field: str, raw_value: str) -> datetime:
        # O fromisoformat do Python 3.11 aceita o "Z"; o replace deixa o
        # código funcionando também em versões anteriores.
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidDate(field, raw_value)

    def _parse_document(self, field: str, raw_value: str) -> str:
        if not is_valid_document(raw_value):
            raise InvalidDocumentNumber(field, raw_value)

        return only_digits(raw_value)
