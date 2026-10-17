from concurrent.futures import ThreadPoolExecutor

from tests.utils import Account, RequestGenerator, pix_payload
from tests.utils.requisition import ClientRequisition


class TestResponseContract:
    def test_post_returns_only_the_three_contract_fields(self):
        status, response = RequestGenerator.POST_pix_transaction(pix_payload(Account(), Account()))

        assert status == 201
        assert set(response.keys()) == {"key", "status", "metadata"}
        assert response["status"] in {"approved", "two_factor_required", "rejected"}

    def test_metadata_explains_the_decision(self):
        status, response = RequestGenerator.POST_pix_transaction(pix_payload(Account(), Account()))

        metadata = response["metadata"]
        assert {"transaction_id", "score", "decided_by", "rules_version", "reasons"} <= set(metadata.keys())
        for reason in metadata["reasons"]:
            assert {"rule", "points", "forced_status", "detail"} == set(reason.keys())

    def test_get_returns_the_recorded_analysis(self):
        payload = pix_payload(Account(), Account())
        _, posted = RequestGenerator.POST_pix_transaction(payload)

        status, fetched = RequestGenerator.GET_pix_transaction(payload["id"])

        assert status == 200
        assert fetched == posted

    def test_get_unknown_pix(self):
        status, response = RequestGenerator.GET_pix_transaction("pix-que-nao-existe")

        assert status == 404
        assert response["code"] == "QIT001001"

    def test_the_slide_example_is_accepted(self):
        """O payload exato do slide 19 do desafio, com só o id trocado."""
        payload = {
            "id": "pix-000123-exemplo-do-slide",
            "amount": 150000,
            "transaction_date": "2026-10-17T22:15:00-03:00",
            "source_account": {
                "branch": "0001",
                "account_number": "88213",
                "opening_date": "2025-03-02T10:00:00-03:00",
                "owner": {"document_number": "123.456.789-09"},
            },
            "destination_account": {
                "participant": "60701190",
                "branch": "3675",
                "account_number": "10442",
                "opening_date": "2026-10-10T09:00:00-03:00",
                "owner": {"document_number": "987.654.321-00"},
            },
            "source": {
                "device_id": "dvc-9f2c71a8",
                "channel": "app",
                "ip": "198.51.100.23",
                "geolocation": {"latitude": -23.56, "longitude": -46.65},
            },
        }

        status, response = RequestGenerator.POST_pix_transaction(payload)

        # R$ 1.500 às 22h15 de pessoa física: passa do limite noturno do BCB.
        assert status in (200, 201)
        assert response["status"] == "rejected"
        assert response["metadata"]["decided_by"] == "NIGHT_LIMIT"


class TestIdempotency:
    def test_resending_the_same_pix_returns_the_same_analysis(self):
        payload = pix_payload(Account(), Account())
        _, first = RequestGenerator.POST_pix_transaction(payload)

        status, again = RequestGenerator.POST_pix_transaction(payload)

        assert status == 200
        assert again == first

    def test_same_id_with_another_content_is_conflict(self):
        payload = pix_payload(Account(), Account())
        RequestGenerator.POST_pix_transaction(payload)

        payload["amount"] = payload["amount"] + 1
        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 409
        assert response["code"] == "QIT001002"

    def test_concurrent_resends_create_a_single_analysis(self):
        payload = pix_payload(Account(), Account())

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: RequestGenerator.POST_pix_transaction(payload), range(5)))

        assert sorted(status for status, _ in results) == [200, 200, 200, 200, 201]
        assert len({response["key"] for _, response in results}) == 1


class TestInputValidation:
    def test_missing_field(self):
        payload = pix_payload(Account(), Account())
        del payload["amount"]

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 400
        assert response["code"] == "QIT000001"

    def test_amount_must_be_a_positive_integer_in_cents(self):
        for amount in [0, -100, 150.5, "15000"]:
            payload = pix_payload(Account(), Account())
            payload["amount"] = amount

            status, _ = RequestGenerator.POST_pix_transaction(payload)

            assert status == 400, f"amount={amount!r} deveria ser recusado"

    def test_date_needs_timezone(self):
        """Sem fuso não dá para saber a hora local do pagador — e a regra noturna depende dela."""
        payload = pix_payload(Account(), Account())
        payload["transaction_date"] = "2026-10-17T22:15:00"

        status, _ = RequestGenerator.POST_pix_transaction(payload)

        assert status == 400

    def test_impossible_calendar_date(self):
        payload = pix_payload(Account(), Account())
        payload["transaction_date"] = "2026-02-30T10:00:00-03:00"

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 422
        assert response["code"] == "QIT001004"

    def test_invalid_document_is_refused_and_not_recorded(self):
        payload = pix_payload(Account(document_number="111.222.333-44"), Account())

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 422
        assert response["code"] == "QIT001003"
        assert RequestGenerator.GET_pix_transaction(payload["id"])[0] == 404

    def test_alphanumeric_cnpj_is_accepted(self):
        """O CNPJ alfanumérico (Receita, julho de 2026), com o exemplo oficial."""
        payload = pix_payload(Account(), Account(document_number="12.ABC.345/01DE-35"))

        status, _ = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201

    def test_unknown_field(self):
        payload = pix_payload(Account(), Account())
        payload["descricao"] = "aluguel"

        status, _ = RequestGenerator.POST_pix_transaction(payload)

        assert status == 400

    def test_requires_internal_token(self):
        response = ClientRequisition.send("POST", "/pix/transaction", payload=pix_payload(Account(), Account()))

        assert response.response_status == 403
