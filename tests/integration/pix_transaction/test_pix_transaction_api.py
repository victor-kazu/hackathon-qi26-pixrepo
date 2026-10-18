from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text

from tests.utils import DbUtils, ObjectGenerator, PayloadGenerator, RandomGenerator, RequestGenerator
from tests.utils.payload_generator import BRASILIA, iso


class TestContract:
    def test_challenge_example_returns_key_status_and_metadata(self):
        """O payload do enunciado, com as datas trazidas para ontem."""
        transaction_date = PayloadGenerator.daytime(days_ago=1, hour=22, minute=15)
        payload = PayloadGenerator.create_pix_payload(
            amount=150000,
            transaction_date=transaction_date,
            destination_account=PayloadGenerator.create_account(
                participant="60701190", opening_date=transaction_date - timedelta(days=7, hours=13)
            ),
            geolocation={"latitude": -23.56, "longitude": -46.65},
        )

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201
        assert set(response.keys()) == {"key", "status", "metadata"}
        assert response["status"] == "two_factor_required"
        rules = [hit["rule"] for hit in response["metadata"]["triggered_rules"]]
        assert rules == ["NIGHT_HIGH_VALUE", "NEW_DESTINATION_ACCOUNT", "NEW_COUNTERPARTY"]

    def test_get_returns_the_recorded_analysis(self):
        payload = PayloadGenerator.create_pix_payload()
        _, created = RequestGenerator.POST_pix_transaction(payload)

        status, fetched = RequestGenerator.GET_pix_transaction(payload["id"])

        assert status == 200
        assert fetched == created
        assert fetched["metadata"]["pix_id"] == payload["id"]

    def test_get_unknown_pix_is_404(self):
        status, response = RequestGenerator.GET_pix_transaction("pix-que-nao-existe")
        assert status == 404
        assert response["code"] == "QIT002001"

    def test_requires_internal_token(self):
        status, _ = RequestGenerator.POST_pix_transaction(PayloadGenerator.create_pix_payload(), headers={})
        assert status == 403

    def test_each_triggered_rule_is_stored_in_its_own_row(self):
        transaction_date = PayloadGenerator.daytime(days_ago=1, hour=21)
        response = ObjectGenerator.analyze_pix(amount=200_000, transaction_date=transaction_date)

        engine = create_engine(DbUtils.database_url())
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT rule_code, points FROM pix_analysis_rule_hit hit "
                    "JOIN pix_analysis analysis ON analysis.id = hit.pix_analysis_id "
                    "WHERE analysis.analysis_key = :key ORDER BY hit.id"
                ),
                {"key": response["key"]},
            ).all()
        engine.dispose()

        assert [(row.rule_code, row.points) for row in rows] == [("NIGHT_HIGH_VALUE", 40), ("NEW_COUNTERPARTY", 10)]


class TestIdempotency:
    def test_same_id_returns_the_same_analysis(self):
        payload = PayloadGenerator.create_pix_payload()

        first_status, first = RequestGenerator.POST_pix_transaction(payload)
        second_status, second = RequestGenerator.POST_pix_transaction(payload)

        assert first_status == 201
        assert second_status == 200
        assert second == first

    def test_same_id_with_another_amount_is_a_conflict(self):
        payload = PayloadGenerator.create_pix_payload(amount=10_000)
        RequestGenerator.POST_pix_transaction(payload)

        payload["amount"] = 900_000
        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 409
        assert response["code"] == "QIT002006"

        # O conflito não sobrescreve nada: a análise gravada é a do primeiro valor.
        _, fetched = RequestGenerator.GET_pix_transaction(payload["id"])
        assert fetched["metadata"]["signals"]["amount"] == 10_000

    def test_same_id_to_another_destination_is_a_conflict(self):
        payload = PayloadGenerator.create_pix_payload()
        RequestGenerator.POST_pix_transaction(payload)

        payload["destination_account"] = PayloadGenerator.create_account(participant="60701190")
        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 409
        assert response["code"] == "QIT002006"

    def test_same_id_at_the_same_time_from_different_accounts(self):
        """O lock é por conta pagadora: o mesmo id vindo de contas diferentes não se espera.

        Quem barra o segundo é o UNIQUE(pix_id). Antes, o choque virava 500;
        agora o request que perdeu desfaz tudo e responde 409, porque o
        conteúdo (a conta) é outro. Fica exatamente uma análise gravada.
        """
        pix_id = f"pix-{uuid4()}"
        payloads = []
        for _ in range(5):
            payload = PayloadGenerator.create_pix_payload()
            payload["id"] = pix_id
            payloads.append(payload)

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(RequestGenerator.POST_pix_transaction, payloads))

        assert sorted(status for status, _ in results) == [201, 409, 409, 409, 409]

        winner = next(response for status, response in results if status == 201)
        status, fetched = RequestGenerator.GET_pix_transaction(pix_id)
        assert status == 200
        assert fetched["key"] == winner["key"]

    def test_retry_does_not_count_as_a_new_attempt(self):
        source_account = PayloadGenerator.create_account()
        payload = PayloadGenerator.create_pix_payload(source_account=source_account)
        RequestGenerator.POST_pix_transaction(payload)
        RequestGenerator.POST_pix_transaction(payload)
        RequestGenerator.POST_pix_transaction(payload)

        response = ObjectGenerator.analyze_pix(source_account=source_account)

        # Três POSTs do mesmo id contam como UMA tentativa anterior.
        assert response["metadata"]["signals"]["attempts_last_5min"] == 1
        assert response["metadata"]["signals"]["attempts_last_1h"] == 1


class TestConcurrency:
    def test_parallel_pix_from_one_account_get_the_same_decisions_as_in_a_queue(self):
        """Conta nova dispara 5 Pix ao mesmo tempo, como um robô faria.

        Sem o lock por conta, os 5 leriam o histórico vazio e seriam todos
        aprovados. Com ele, cada um enxerga os anteriores: o histórico visto
        vai de 0 a 4 tentativas, e as decisões são as mesmas da fila.
        """
        source_account, device_id = PayloadGenerator.create_account(), f"dvc-{uuid4().hex[:12]}"
        moment = PayloadGenerator.daytime(days_ago=1, hour=14)
        payloads = [
            PayloadGenerator.create_pix_payload(
                amount=10_000 + index * 100, transaction_date=moment, source_account=source_account, device_id=device_id
            )
            for index in range(5)
        ]

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(RequestGenerator.POST_pix_transaction, payloads))

        assert [status for status, _ in results] == [201] * 5
        by_attempt = sorted(
            (response["metadata"]["signals"]["attempts_last_5min"], response["status"]) for _, response in results
        )
        assert by_attempt == [
            (0, "approved"),
            (1, "approved"),
            (2, "two_factor_required"),
            (3, "two_factor_required"),
            (4, "rejected"),
        ]


class TestValidation:
    def test_refuses_empty_body(self):
        status, response = RequestGenerator.POST_pix_transaction({})
        assert status == 400
        assert response["code"] == "QIT000001"

    def test_refuses_unknown_field(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["campo_que_nao_existe"] = 1

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 400
        assert response["code"] == "QIT000001"

    def test_refuses_malformed_fields(self):
        malformed = [
            ("amount", 0),
            ("amount", 10.5),
            ("transaction_date", "2026-10-17 22:15:00"),
            ("transaction_date", "2026-10-17T22:15:00"),
        ]
        for field, value in malformed:
            payload = PayloadGenerator.create_pix_payload()
            payload[field] = value

            status, response = RequestGenerator.POST_pix_transaction(payload)
            assert status == 400, f"{field}={value!r} foi aceito"
            assert response["code"] == "QIT000001"

    def test_refuses_document_with_wrong_check_digit(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["destination_account"]["owner"]["document_number"] = "111.222.333-44"

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 422
        assert response["code"] == "QIT002002"

    def test_refuses_date_that_does_not_exist(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["source_account"]["opening_date"] = "2025-02-30T10:00:00-03:00"

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 422
        assert response["code"] == "QIT002003"

    def test_refuses_account_opened_after_the_pix(self):
        transaction_date = PayloadGenerator.daytime(days_ago=2)
        payload = PayloadGenerator.create_pix_payload(
            transaction_date=transaction_date,
            destination_account=PayloadGenerator.create_account(
                participant="60701190", opening_date=transaction_date + timedelta(days=1)
            ),
        )

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 422
        assert response["code"] == "QIT002004"

    def test_refuses_pix_in_the_future(self):
        payload = PayloadGenerator.create_pix_payload(transaction_date=datetime.now(BRASILIA) + timedelta(hours=1))

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 422
        assert response["code"] == "QIT002005"

    def test_accepts_cnpj_unformatted_cpf_and_utc_date(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["transaction_date"] = iso(PayloadGenerator.daytime(days_ago=1).astimezone(timezone.utc)).replace("+00:00", "Z")
        payload["source_account"]["owner"]["document_number"] = RandomGenerator.generate_cnpj()
        payload["destination_account"]["owner"]["document_number"] = RandomGenerator.generate_cpf().replace(".", "").replace("-", "")

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response
        assert response["metadata"]["signals"]["source_is_pf"] is False

    def test_accepts_alphanumeric_cnpj(self):
        """CNPJ com letras, emitido pela Receita desde julho de 2026 (exemplo oficial)."""
        payload = PayloadGenerator.create_pix_payload()
        payload["destination_account"]["owner"]["document_number"] = "12.ABC.345/01DE-35"

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response
        assert response["metadata"]["signals"]["destination_is_pf"] is False

    def test_refuses_alphanumeric_cnpj_with_wrong_check_digit(self):
        payload = PayloadGenerator.create_pix_payload()
        payload["destination_account"]["owner"]["document_number"] = "12.ABC.345/01DE-36"

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 422
        assert response["code"] == "QIT002002"

    def test_geolocation_is_optional_and_may_be_null(self):
        without = PayloadGenerator.create_pix_payload()
        with_null = PayloadGenerator.create_pix_payload()
        with_null["source"]["geolocation"] = None

        for payload in (without, with_null):
            status, response = RequestGenerator.POST_pix_transaction(payload)
            assert status == 201, response
            assert response["status"] == "approved"
