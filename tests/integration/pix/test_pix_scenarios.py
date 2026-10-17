"""Os cenários de fraude de ponta a ponta: o histórico é montado pela
própria API, Pix a Pix, e a decisão é conferida pela resposta HTTP.

Cada teste cria os seus pagadores, aparelhos e recebedores (aleatórios),
então nenhum teste enxerga o histórico de outro.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from tests.utils import BASE_DATE, Account, RequestGenerator, new_device, pix_payload


def send(payload: dict) -> dict:
    status, response = RequestGenerator.POST_pix_transaction(payload)
    assert status == 201, response
    return response


def rules_of(response: dict) -> set:
    return {reason["rule"] for reason in response["metadata"]["reasons"]}


class TestFirstPixVersusHistory:
    """Os dois cenários pedidos nas direcionais."""

    def test_first_pix_with_low_amount_is_approved(self):
        response = send(pix_payload(Account(), Account(), amount=15_000))

        assert response["status"] == "approved"
        assert rules_of(response) == {"NEW_DEVICE"}

    def test_first_pix_with_high_amount_asks_two_factor(self):
        response = send(pix_payload(Account(), Account(), amount=600_000))

        assert response["status"] == "two_factor_required"
        assert response["metadata"]["score"] == 65
        assert rules_of(response) == {"HIGH_AMOUNT", "NEW_DEVICE"}

    def test_client_with_history_is_not_bothered_by_the_same_amount(self):
        """Os mesmos R$ 6.000 do teste acima, agora de quem tem histórico com esse recebedor."""
        payer, receiver, device = Account(), Account(), new_device()

        for days_ago in [40, 35, 20, 10, 5]:
            history = send(pix_payload(payer, receiver, 20_000, BASE_DATE - timedelta(days=days_ago), device))
            assert history["status"] == "approved"

        response = send(pix_payload(payer, receiver, 600_000, BASE_DATE, device))

        # +30 +30 +30 -30 -20 = 40: os sinais de risco aparecem, a confiança compensa.
        assert response["status"] == "approved"
        assert response["metadata"]["score"] == 40
        assert rules_of(response) == {
            "HIGH_AMOUNT",
            "OUT_OF_PROFILE",
            "DAILY_SUM",
            "KNOWN_COUNTERPARTY",
            "TRUSTED_DEVICE",
        }


class TestLimitAndAmount:
    def test_night_limit_for_individuals(self):
        night = BASE_DATE.replace(hour=23)

        response = send(pix_payload(Account(), Account(), amount=150_000, when=night))

        assert response["status"] == "rejected"
        assert response["metadata"]["decided_by"] == "NIGHT_LIMIT"

    def test_night_limit_does_not_apply_to_companies(self):
        night = BASE_DATE.replace(hour=23)

        response = send(pix_payload(Account.company(), Account(), amount=150_000, when=night))

        assert response["status"] != "rejected"
        assert "NIGHT_LIMIT" not in rules_of(response)

    def test_split_payment_to_dodge_the_night_limit(self):
        """3 × R$ 900 para o mesmo recebedor em meia hora: cada um abaixo do teto, a soma acima."""
        payer, receiver, device = Account(), Account(), new_device()
        night = BASE_DATE.replace(hour=21)

        statuses = []
        for minutes in [0, 10, 20]:
            response = send(pix_payload(payer, receiver, 90_000, night + timedelta(minutes=minutes), device))
            statuses.append(response["status"])

        assert statuses == ["approved", "approved", "two_factor_required"]
        assert "SPLIT_PAYMENT" in rules_of(response)


class TestVelocity:
    def test_burst_asks_two_factor_then_rejects(self):
        payer, device = Account(), new_device()

        statuses = []
        for second in range(10):
            response = send(pix_payload(payer, Account(), 1_000, BASE_DATE + timedelta(seconds=30 * second), device))
            statuses.append(response["status"])

        assert statuses == ["approved"] * 4 + ["two_factor_required"] * 5 + ["rejected"]
        assert response["metadata"]["decided_by"] == "VELOCITY"

    def test_simultaneous_burst_is_counted_correctly(self):
        """12 Pix do mesmo pagador ao mesmo tempo. Sem a trava por pagador, vários
        leriam o mesmo histórico e escapariam do velocity; com ela, a contagem é exata."""
        payer, device = Account(), new_device()
        send(pix_payload(payer, Account(), 1_000, BASE_DATE, device))

        payloads = [pix_payload(payer, Account(), 1_000, BASE_DATE, device) for _ in range(11)]

        with ThreadPoolExecutor(max_workers=11) as pool:
            results = list(pool.map(RequestGenerator.POST_pix_transaction, payloads))

        statuses = sorted(response["status"] for _, response in results)
        # 12 no total: do 1º ao 4º aprovado, do 5º ao 9º pede 2FA, do 10º ao 12º reprovado.
        assert statuses.count("approved") == 3
        assert statuses.count("two_factor_required") == 5
        assert statuses.count("rejected") == 3


class TestCounterparty:
    def test_brand_new_receiving_account(self):
        receiver = Account(opening_date=BASE_DATE - timedelta(hours=5))
        payer, device = Account(), new_device()
        send(pix_payload(payer, Account(), 5_000, BASE_DATE - timedelta(days=60), device))

        response = send(pix_payload(payer, receiver, 200_000, BASE_DATE, device))

        assert response["status"] == "two_factor_required"
        assert "NEW_DESTINATION_ACCOUNT" in rules_of(response)

    def test_mule_account_receiving_from_many_people(self):
        """Conta de pessoa física aberta há 3 dias recebendo de 5 pagadores diferentes."""
        mule = Account(opening_date=BASE_DATE - timedelta(days=3))

        statuses = []
        for minute in range(5):
            response = send(pix_payload(Account(), mule, 30_000, BASE_DATE + timedelta(minutes=minute)))
            statuses.append(response["status"])

        assert statuses[:4] == ["approved"] * 4
        assert statuses[4] == "two_factor_required"
        assert "DESTINATION_FAN_IN" in rules_of(response)

    def test_company_receiving_from_many_people_is_normal(self):
        shop = Account.company(opening_date=BASE_DATE - timedelta(days=3))

        for minute in range(6):
            response = send(pix_payload(Account(), shop, 30_000, BASE_DATE + timedelta(minutes=minute)))

        assert "DESTINATION_FAN_IN" not in rules_of(response)
        assert response["status"] == "approved"

    def test_transfer_to_own_account_elsewhere(self):
        payer = Account()
        same_person_other_bank = Account(document_number=payer.document_number, participant="00000000")

        response = send(pix_payload(payer, same_person_other_bank, 800_000))

        assert "SAME_OWNER" in rules_of(response)
        assert response["status"] == "approved"


class TestAccountAndDevice:
    def test_new_paying_account_moving_money(self):
        payer = Account(opening_date=BASE_DATE - timedelta(days=2))

        response = send(pix_payload(payer, Account(), 200_000))

        assert response["status"] == "two_factor_required"
        assert "NEW_SOURCE_ACCOUNT" in rules_of(response)

    def test_one_device_many_payers(self):
        device = new_device()

        statuses = []
        for minute in range(3):
            response = send(pix_payload(Account(), Account(), 5_000, BASE_DATE + timedelta(minutes=minute), device))
            statuses.append(response["status"])

        assert statuses == ["approved", "two_factor_required", "rejected"]
        assert response["metadata"]["decided_by"] == "SHARED_DEVICE"

    def test_impossible_travel(self):
        payer, device = Account(), new_device()
        send(pix_payload(payer, Account(), 5_000, BASE_DATE, device, geolocation=(-23.56, -46.65)))

        response = send(
            pix_payload(payer, Account(), 5_000, BASE_DATE + timedelta(minutes=30), device, geolocation=(-8.05, -34.88))
        )

        assert response["status"] == "two_factor_required"
        assert "IMPOSSIBLE_TRAVEL" in rules_of(response)


class TestHistoryIsPointInTime:
    def test_a_later_pix_that_arrived_first_does_not_count(self):
        """O motor julga cada Pix com o que se sabia NAQUELE instante."""
        payer, device = Account(), new_device()

        for second in range(6):
            send(pix_payload(payer, Account(), 1_000, BASE_DATE + timedelta(hours=1, seconds=second), device))

        earlier = send(pix_payload(payer, Account(), 1_000, BASE_DATE, device))

        assert "VELOCITY" not in rules_of(earlier)
