import hashlib
import json
from datetime import datetime, timedelta
from typing import Tuple

from sqlalchemy.exc import IntegrityError

from controllers.base_controller import BaseController
from fraud import parameters as p
from fraud.engine import decide
from fraud.signals import Signals
from dtos import PixAnalysisDTO
from errors import InvalidDate, InvalidDocumentNumber, NotFoundPixTransaction, PixTransactionIdReused
from repositories import PayerRepository, PixAnalysisRepository, PixTransactionRepository
from utils.document_number import document_type, is_valid_document, normalize_document


class PixTransactionController(BaseController):
    """Analisa um Pix: valida, trava o pagador, monta os sinais, decide e grava.

    ────────────────────────────────────────────────────────────────
    A ORDEM DO `analyze` É A GARANTIA
    ────────────────────────────────────────────────────────────────
    1. Entrada inválida (documento ou data impossível) para antes de
       qualquer trava, e nada é gravado: não é decisão antifraude.
    2. O mesmo `id` já analisado devolve a análise gravada — idempotência.
    3. TRAVA O PAGADOR (`SELECT … FOR UPDATE`). Duas análises do mesmo
       pagador passam a acontecer uma depois da outra.
    4. Confere o `id` de novo, já com a trava: um reenvio simultâneo que
       esperou a trava acha a análise do primeiro aqui.
    5. Lê o histórico, monta os sinais e decide.
    6. Grava o Pix, a análise e as regras que dispararam, e só então dá
       commit — que é o que solta a trava.

    Sem o passo 3, dois Pix do mesmo pagador chegando juntos contariam o
    mesmo histórico, e os dois passariam no velocity. É o "principal
    desafio" deste desenho, explicado no README.
    """

    def __init__(self) -> None:
        super().__init__(__name__)
        self.payer_repository = PayerRepository(self.context)
        self.pix_repository = PixTransactionRepository(self.context)
        self.analysis_repository = PixAnalysisRepository(self.context)

    def analyze(self, payload: dict) -> Tuple[dict, bool]:
        """Devolve a análise e se ela é NOVA (201) ou um reenvio do mesmo Pix (200)."""
        fields = self._parse(payload)
        payload_hash = self._hash(payload)

        existing = self._existing_analysis(fields["transaction_id"], payload_hash)
        if existing is not None:
            return existing, False

        payer = self.payer_repository.lock(fields["source_document_number"], document_type(fields["source_document_number"]))

        existing = self._existing_analysis(fields["transaction_id"], payload_hash)
        if existing is not None:
            return existing, False

        signals = self._build_signals(payer.id, fields)
        decision = decide(signals)

        try:
            pix = self.pix_repository.create({**fields, "payer_id": payer.id, "payload_hash": payload_hash})
            analysis = self.analysis_repository.create(
                pix,
                status=decision.status,
                score=decision.score,
                decided_by=decision.decided_by,
                rules_version=p.RULES_VERSION,
                hits=decision.hits,
            )
        except IntegrityError:
            # O mesmo id chegou por outro pagador ao mesmo tempo (a trava é
            # por pagador, então não pegou esse caso). O UNIQUE(transaction_id)
            # barrou a segunda gravação; desfaz tudo e responde com a que ficou.
            self.session.rollback()
            existing = self._existing_analysis(fields["transaction_id"], payload_hash)
            if existing is None:
                raise
            return existing, False

        self.logger.info(
            f"Pix {pix.transaction_id}: {decision.status} (score {decision.score}, decidido por {decision.decided_by})"
        )

        analysis_dto = PixAnalysisDTO.analysis_to_dict(analysis, pix)
        self.session.commit()

        return analysis_dto, True

    def get_by_id(self, transaction_id: str) -> dict:
        pix = self.pix_repository.get_by_transaction_id(transaction_id)

        if pix is None:
            raise NotFoundPixTransaction(transaction_id)

        analysis = self.analysis_repository.get_by_transaction(pix)
        return PixAnalysisDTO.analysis_to_dict(analysis, pix)

    # ────────────────────────────────────────────────────────────────
    # Os sinais
    # ────────────────────────────────────────────────────────────────

    def _build_signals(self, payer_id: int, fields: dict) -> Signals:
        """Responde, com o histórico gravado, cada pergunta que as regras fazem.

        O próprio Pix ainda não está gravado neste ponto, então toda
        contagem soma 1 (ou o valor dele) no fim: "5 Pix em 10 minutos"
        quer dizer este e mais quatro anteriores.
        """
        now = fields["transaction_date"]
        amount = fields["amount"]
        destination = (
            fields["destination_participant"],
            fields["destination_branch"],
            fields["destination_account_number"],
        )
        repo = self.pix_repository

        count_window = repo.count_by_payer(payer_id, now - timedelta(minutes=p.VELOCITY_WINDOW_MINUTES), now)
        same_destination_count, same_destination_sum = repo.count_and_sum_to_destination(
            payer_id, destination, now - timedelta(minutes=p.SPLIT_WINDOW_MINUTES), now
        )
        sum_24h = repo.sum_by_payer(payer_id, now - timedelta(hours=p.DAILY_SUM_WINDOW_HOURS), now)
        max_daily = repo.max_daily_approved(
            payer_id,
            now - timedelta(days=p.DAILY_SUM_HISTORY_DAYS),
            now - timedelta(hours=p.DAILY_SUM_WINDOW_HOURS),
        )
        approved_count, approved_average, approved_max = repo.approved_stats(payer_id, now)

        destination_payers = repo.destination_payers(destination, now - timedelta(hours=p.FAN_IN_WINDOW_HOURS), now)
        device_payers = repo.device_payers(fields["device_id"], now - timedelta(days=p.SHARED_DEVICE_WINDOW_DAYS), now)
        device_first_use = repo.device_first_use_by_payer(payer_id, fields["device_id"], now)

        if device_first_use is not None:
            device_days = (now - device_first_use).total_seconds() / 86400
        else:
            device_days = None

        previous_location = repo.previous_location(payer_id, now)
        if previous_location is not None and fields["latitude"] is not None:
            previous_latitude, previous_longitude, previous_date = previous_location
            minutes_since_previous = (now - previous_date).total_seconds() / 60
        else:
            previous_latitude = previous_longitude = minutes_since_previous = None

        latitude = fields["latitude"]
        longitude = fields["longitude"]

        return Signals(
            amount=amount,
            local_hour=now.hour,
            payer_document_type=document_type(fields["source_document_number"]),
            destination_document_type=fields["destination_document_type"],
            same_owner=fields["source_document_number"] == fields["destination_document_number"],
            source_account_age_days=self._age_in_days(fields["source_opening_date"], now),
            destination_account_age_days=self._age_in_days(fields["destination_opening_date"], now),
            payer_count_window=count_window + 1,
            same_destination_count_window=same_destination_count + 1,
            same_destination_sum_window=same_destination_sum + amount,
            payer_sum_24h=sum_24h + amount,
            payer_max_daily_approved=max_daily,
            payer_approved_count=approved_count,
            payer_approved_average=approved_average,
            payer_approved_max_amount=approved_max,
            destination_distinct_payers_24h=len(destination_payers | {payer_id}),
            counterparty_approved_count=repo.counterparty_approved_count(
                payer_id, destination, now - timedelta(hours=p.KNOWN_COUNTERPARTY_MIN_AGE_HOURS)
            ),
            device_used_before_by_payer=device_first_use is not None,
            device_days_with_payer=device_days,
            device_distinct_payers_7d=len(device_payers | {payer_id}),
            latitude=float(latitude) if latitude is not None else None,
            longitude=float(longitude) if longitude is not None else None,
            previous_latitude=previous_latitude,
            previous_longitude=previous_longitude,
            minutes_since_previous_location=minutes_since_previous,
        )

    # ────────────────────────────────────────────────────────────────
    # Entrada e idempotência
    # ────────────────────────────────────────────────────────────────

    def _existing_analysis(self, transaction_id: str, payload_hash: str):
        pix = self.pix_repository.get_by_transaction_id(transaction_id)

        if pix is None:
            return None

        if pix.payload_hash != payload_hash:
            raise PixTransactionIdReused(transaction_id)

        analysis = self.analysis_repository.get_by_transaction(pix)
        return PixAnalysisDTO.analysis_to_dict(analysis, pix)

    def _parse(self, payload: dict) -> dict:
        """O payload, já conferido pelo schema, virado nas colunas do Pix.

        O schema garantiu o FORMATO. Aqui fica o que só se sabe fazendo a
        conta: se o documento existe (dígitos verificadores) e se a data
        existe no calendário.
        """
        source_account = payload["source_account"]
        destination_account = payload["destination_account"]
        source = payload["source"]
        geolocation = source.get("geolocation") or {}

        source_document = self._document("source_account.owner.document_number", source_account["owner"]["document_number"])
        destination_document = self._document(
            "destination_account.owner.document_number", destination_account["owner"]["document_number"]
        )

        transaction_date = self._datetime("transaction_date", payload["transaction_date"])

        return {
            "transaction_id": payload["id"],
            "amount": payload["amount"],
            "transaction_date": transaction_date,
            "utc_offset_minutes": int(transaction_date.utcoffset().total_seconds() // 60),
            "source_branch": source_account["branch"],
            "source_account_number": source_account["account_number"],
            "source_opening_date": self._datetime("source_account.opening_date", source_account["opening_date"]),
            "source_document_number": source_document,
            "destination_participant": destination_account["participant"],
            "destination_branch": destination_account["branch"],
            "destination_account_number": destination_account["account_number"],
            "destination_opening_date": self._datetime(
                "destination_account.opening_date", destination_account["opening_date"]
            ),
            "destination_document_number": destination_document,
            "destination_document_type": document_type(destination_document),
            "device_id": source["device_id"],
            "channel": source["channel"],
            "ip": source["ip"],
            "latitude": geolocation.get("latitude"),
            "longitude": geolocation.get("longitude"),
        }

    def _document(self, field_name: str, raw_document: str) -> str:
        if not is_valid_document(raw_document):
            raise InvalidDocumentNumber(field_name, raw_document)

        return normalize_document(raw_document)

    def _datetime(self, field_name: str, raw_datetime: str) -> datetime:
        try:
            return datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidDate(field_name, raw_datetime)

    def _age_in_days(self, opening_date: datetime, now: datetime) -> float:
        # Conta aberta "depois" do Pix é dado incoerente; conta como recém-aberta.
        return max((now - opening_date).total_seconds() / 86400, 0)

    def _hash(self, payload: dict) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
