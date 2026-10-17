"""Cenários de ponta a ponta: histórico gravado pela API, decisão lida da API.

Cada teste usa contas e aparelhos aleatórios, então nenhum herda o
histórico de outro e não é preciso limpar o banco.
"""

from uuid import uuid4

from tests.utils import ObjectGenerator, PayloadGenerator, RandomGenerator


def rules_of(response: dict) -> list:
    return [hit["rule"] for hit in response["metadata"]["triggered_rules"]]


def new_device() -> str:
    return f"dvc-{uuid4().hex[:12]}"


class TestGoodCustomers:
    def test_first_pix_of_a_customer_is_not_suspicious(self):
        """Cold start: sem histórico, sem GPS, valor comum. Ausência de dado não é fraude."""
        response = ObjectGenerator.analyze_pix(amount=30_000)

        assert response["status"] == "approved"
        assert response["metadata"]["signals"]["has_history"] is False

    def test_monthly_rent_to_a_known_landlord_is_approved(self):
        source_account = PayloadGenerator.create_account()
        landlord = PayloadGenerator.create_account(participant="60701190")
        device_id = new_device()
        ObjectGenerator.build_history(source_account, landlord, device_id, days_ago=[62, 32], amount=250_000)

        response = ObjectGenerator.analyze_pix(
            amount=250_000,
            transaction_date=PayloadGenerator.daytime(days_ago=2, hour=9),
            source_account=source_account,
            destination_account=landlord,
            device_id=device_id,
        )

        assert response["status"] == "approved"
        assert "TRUSTED_COUNTERPARTY" in rules_of(response)

    def test_moving_money_between_own_accounts_is_approved(self):
        cpf = RandomGenerator.generate_cpf()
        source_account = PayloadGenerator.create_account(document_number=cpf)
        own_new_account = PayloadGenerator.create_account(
            participant="18236120", document_number=cpf, opening_date=PayloadGenerator.daytime(days_ago=3)
        )

        response = ObjectGenerator.analyze_pix(
            amount=500_000, source_account=source_account, destination_account=own_new_account
        )

        assert response["status"] == "approved"
        assert rules_of(response) == ["SELF_TRANSFER"]


class TestAccountTakeover:
    def test_new_device_asks_for_two_factor_and_then_hits_the_daily_limit(self):
        source_account = PayloadGenerator.create_account()
        usual_device = new_device()
        ObjectGenerator.build_history(
            source_account, PayloadGenerator.create_account(participant="60701190"), usual_device, days_ago=[20, 10], amount=8_000
        )
        stolen_session = new_device()

        first = ObjectGenerator.analyze_pix(
            amount=80_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=15, minute=0),
            source_account=source_account,
            device_id=stolen_session,
        )
        second = ObjectGenerator.analyze_pix(
            amount=30_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=15, minute=10),
            source_account=source_account,
            device_id=stolen_session,
        )

        assert first["status"] == "two_factor_required"
        assert "NEW_DEVICE" in rules_of(first)
        assert second["status"] == "rejected"
        assert "NEW_DEVICE_DAILY_LIMIT" in rules_of(second)

    def test_impossible_travel_on_another_device_is_rejected(self):
        source_account = PayloadGenerator.create_account()
        sao_paulo = {"latitude": -23.56, "longitude": -46.65}
        recife = {"latitude": -8.05, "longitude": -34.88}

        ObjectGenerator.analyze_pix(
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=10, minute=0),
            source_account=source_account,
            geolocation=sao_paulo,
        )
        response = ObjectGenerator.analyze_pix(
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=10, minute=30),
            source_account=source_account,
            geolocation=recife,
        )

        assert response["status"] == "rejected"
        assert "IMPOSSIBLE_TRAVEL_OTHER_DEVICE" in rules_of(response)

    def test_coercion_at_night_is_blocked_even_on_the_usual_phone(self):
        """Sequestro relâmpago: a vítima passaria no 2FA, então acima do teto noturno só barrar protege."""
        response = ObjectGenerator.analyze_pix(
            amount=600_000, transaction_date=PayloadGenerator.daytime(days_ago=1, hour=23, minute=30)
        )

        assert response["status"] == "rejected"
        assert "NIGHT_HARD_LIMIT" in rules_of(response)


class TestAutomation:
    def test_burst_of_pix_weighs_on_the_third_and_blocks_the_fifth(self):
        source_account = PayloadGenerator.create_account()
        device_id = new_device()
        responses = []
        for minute in range(5):
            responses.append(
                ObjectGenerator.analyze_pix(
                    amount=1_000 + minute,
                    transaction_date=PayloadGenerator.daytime(days_ago=1, hour=16, minute=minute),
                    source_account=source_account,
                    device_id=device_id,
                )
            )

        assert "VELOCITY_BURST" not in rules_of(responses[1])
        assert "VELOCITY_BURST" in rules_of(responses[2])
        assert responses[4]["status"] == "rejected"
        assert "VELOCITY_BOT" in rules_of(responses[4])

    def test_trying_again_after_a_rejection_is_probing(self):
        source_account = PayloadGenerator.create_account()
        rejected = ObjectGenerator.analyze_pix(
            amount=700_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=22, minute=0),
            source_account=source_account,
        )
        retry = ObjectGenerator.analyze_pix(
            amount=90_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=22, minute=20),
            source_account=source_account,
        )

        assert rejected["status"] == "rejected"
        assert "PROBING_AFTER_REJECTION" in rules_of(retry)

    def test_same_device_for_three_payers_is_a_device_farm(self):
        device_id = new_device()
        responses = [ObjectGenerator.analyze_pix(device_id=device_id) for _ in range(3)]

        assert responses[0]["status"] == "approved"
        assert "SHARED_DEVICE" in rules_of(responses[1])
        assert responses[2]["status"] == "rejected"
        assert "DEVICE_FARM" in rules_of(responses[2])


class TestScams:
    def test_mule_account_receiving_from_many_victims(self):
        """Conta aberta há 3 dias recebendo R$ 600 de vários CPFs na mesma tarde."""
        mule = PayloadGenerator.create_account(
            participant="60701190", opening_date=PayloadGenerator.daytime(days_ago=4)
        )
        responses = []
        for minute in range(6):
            responses.append(
                ObjectGenerator.analyze_pix(
                    amount=60_000,
                    transaction_date=PayloadGenerator.daytime(days_ago=1, hour=17, minute=minute * 5),
                    destination_account=mule,
                )
            )

        # Primeiras vítimas: dúvida (conta nova + primeiro Pix para ela).
        assert responses[0]["status"] == "two_factor_required"
        # A quinta revela o funil, e 2FA não protege vítima de golpe.
        assert responses[4]["status"] == "rejected"
        assert "DESTINATION_FAN_IN" in rules_of(responses[4])
        # A partir daí o destino está marcado pela rejeição de outro pagador.
        assert "FLAGGED_DESTINATION" in rules_of(responses[5])

    def test_amount_far_above_the_customer_pattern_to_a_new_destination(self):
        source_account = PayloadGenerator.create_account()
        device_id = new_device()
        ObjectGenerator.build_history(
            source_account,
            PayloadGenerator.create_account(participant="60701190"),
            device_id,
            days_ago=[40, 30, 20, 10, 5],
            amount=10_000,
        )

        response = ObjectGenerator.analyze_pix(
            amount=120_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1),
            source_account=source_account,
            device_id=device_id,
        )

        assert response["status"] == "two_factor_required"
        assert "PROFILE_AMOUNT_SPIKE" in rules_of(response)
        assert response["metadata"]["signals"]["profile_median"] == 10_000

    def test_splitting_an_amount_into_equal_pix(self):
        source_account = PayloadGenerator.create_account()
        device_id = new_device()
        responses = []
        for minute in (0, 15, 30):
            responses.append(
                ObjectGenerator.analyze_pix(
                    amount=49_000,
                    transaction_date=PayloadGenerator.daytime(days_ago=1, hour=13, minute=minute),
                    source_account=source_account,
                    device_id=device_id,
                )
            )

        assert "STRUCTURING" in rules_of(responses[2])

    def test_splitting_under_the_night_limit_to_one_destination(self):
        """3 x R$ 990 às 23h para a mesma conta: cada um passa, a soma não."""
        source_account = PayloadGenerator.create_account()
        destination = PayloadGenerator.create_account(participant="60701190")
        device_id = new_device()
        ObjectGenerator.build_history(
            source_account,
            PayloadGenerator.create_account(participant="18236120"),
            device_id,
            days_ago=[30, 20],
            amount=10_000,
        )
        responses = []
        for minute in (0, 20, 40):
            responses.append(
                ObjectGenerator.analyze_pix(
                    amount=99_000,
                    transaction_date=PayloadGenerator.daytime(days_ago=1, hour=22, minute=minute),
                    source_account=source_account,
                    destination_account=destination,
                    device_id=device_id,
                )
            )

        assert [response["status"] for response in responses[:2]] == ["approved", "approved"]
        assert responses[2]["status"] == "two_factor_required"
        assert "SPLIT_PAYMENT" in rules_of(responses[2])
        assert "STRUCTURING" not in rules_of(responses[2])

    def test_high_value_without_history_weighs_but_does_not_block_alone(self):
        """R$ 6 mil soma 30 e passa; R$ 15 mil já é mais da metade do teto diário e pede 2FA."""
        high = ObjectGenerator.analyze_pix(amount=600_000)
        higher = ObjectGenerator.analyze_pix(amount=1_500_000)

        assert high["status"] == "approved"
        assert rules_of(high) == ["HIGH_VALUE_WITHOUT_PROFILE", "NEW_COUNTERPARTY"]
        assert higher["status"] == "two_factor_required"
        assert rules_of(higher) == ["HIGH_VALUE_WITHOUT_PROFILE", "DAILY_EXPOSURE", "NEW_COUNTERPARTY"]

    def test_history_is_read_at_the_pix_date_not_today(self):
        """Um Pix antigo não enxerga o que aconteceu depois dele."""
        destination = PayloadGenerator.create_account(participant="60701190")
        for _ in range(5):
            ObjectGenerator.analyze_pix(destination_account=destination)

        older = ObjectGenerator.analyze_pix(
            destination_account=destination, transaction_date=PayloadGenerator.daytime(days_ago=3)
        )

        assert older["metadata"]["signals"]["destination_distinct_payers_24h"] == 1
