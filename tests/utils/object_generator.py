from tests.utils.payload_generator import PayloadGenerator
from tests.utils.request_generator import RequestGenerator


class ObjectGenerator:
    @staticmethod
    def analyze_pix(**payload_overrides) -> dict:
        """Manda um Pix e devolve a análise. Falha o teste se a API não criou."""
        payload = PayloadGenerator.create_pix_payload(**payload_overrides)

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

        return response

    @staticmethod
    def build_history(source_account: dict, destination_account: dict, device_id: str, days_ago: list, amount: int) -> None:
        """Pix aprovados no passado, um por dia da lista, todos no mesmo horário comercial."""
        for days in days_ago:
            response = ObjectGenerator.analyze_pix(
                amount=amount,
                transaction_date=PayloadGenerator.daytime(days_ago=days, hour=11),
                source_account=source_account,
                destination_account=destination_account,
                device_id=device_id,
            )
            assert response["status"] == "approved", response["metadata"]["triggered_rules"]
