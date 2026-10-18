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
        own_account_elsewhere = PayloadGenerator.create_account(
            participant="18236120", document_number=cpf, opening_date=PayloadGenerator.daytime(days_ago=60)
        )

        response = ObjectGenerator.analyze_pix(
            amount=500_000, source_account=source_account, destination_account=own_account_elsewhere
        )

        assert response["status"] == "approved"
        assert rules_of(response) == ["SELF_TRANSFER"]

    def test_five_friends_in_four_minutes_from_a_mature_account_is_not_a_bot(self):
        """Racha do bar: conta antiga com histórico paga 5 amigos em 4 minutos. Pede 2FA, não barra."""
        source_account, device_id = PayloadGenerator.create_account(), new_device()
        ObjectGenerator.build_history(source_account, PayloadGenerator.create_account(participant="60701190"), device_id, [40, 35, 20, 10, 5], 3_000)

        responses = [
            ObjectGenerator.analyze_pix(
                amount=4_500,
                transaction_date=PayloadGenerator.daytime(days_ago=1, hour=16, minute=minute),
                source_account=source_account,
                device_id=device_id,
            )
            for minute in range(5)
        ]

        assert [response["status"] for response in responses] == [
            "approved", "approved", "two_factor_required", "two_factor_required", "two_factor_required",
        ]
        assert "VELOCITY_BOT" not in rules_of(responses[4])

    def test_family_of_three_sharing_a_phone_asks_two_factor(self):
        """Três pessoas com conta antiga e histórico próprio passam a usar o mesmo celular."""
        shared_phone = new_device()
        responses = []
        for _ in range(3):
            member = PayloadGenerator.create_account()
            ObjectGenerator.build_history(member, PayloadGenerator.create_account(participant="60701190"), new_device(), [40, 35, 20, 10, 5], 3_000)
            responses.append(ObjectGenerator.analyze_pix(amount=5_000, source_account=member, device_id=shared_phone))

        assert responses[2]["status"] == "two_factor_required"
        assert "DEVICE_FARM" in rules_of(responses[2])

    def test_group_collection_forwarded_to_the_venue_is_approved(self):
        """Vaquinha: quatro amigos pagam o organizador, que repassa tudo para a pousada.

        Quem costuma pagar R$ 80 manda R$ 1.600 para um destino novo. O repasse
        pesa (+25), mas o valor fora do perfil é explicado pelo dinheiro que
        acabou de entrar e não conta de novo. Antes da v7, este Pix era rejeitado.
        """
        cpf = RandomGenerator.generate_cpf()
        organizer_as_destination = PayloadGenerator.create_account(participant="32402502", document_number=cpf)
        organizer = {key: value for key, value in organizer_as_destination.items() if key != "participant"}
        device_id = new_device()
        ObjectGenerator.build_history(organizer, PayloadGenerator.create_account(participant="60701190"), device_id, [40, 35, 20, 10, 5], 8_000)
        for minute in (0, 10, 20, 30):
            ObjectGenerator.analyze_pix(
                amount=40_000,
                transaction_date=PayloadGenerator.daytime(days_ago=1, hour=10, minute=minute),
                destination_account=organizer_as_destination,
            )

        response = ObjectGenerator.analyze_pix(
            amount=160_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=12),
            source_account=organizer,
            device_id=device_id,
        )

        assert response["status"] == "approved"
        assert "MULE_PASS_THROUGH" in rules_of(response)
        assert "PROFILE_AMOUNT_SPIKE" not in rules_of(response)

    def test_street_vendor_receives_from_many_customers_and_pays_the_supplier(self):
        """Pipoqueiro: 15 clientes pagam R$ 70 na mesma tarde, e à noite ele paga o fornecedor.

        Ticket baixo não entra no funil de pagadores. Antes da v8, do 10º
        cliente em diante todo Pix caía no 2FA, e o pagamento ao fornecedor
        era rejeitado como repasse de dinheiro suspeito.
        """
        cpf = RandomGenerator.generate_cpf()
        vendor_as_destination = PayloadGenerator.create_account(participant="32402502", document_number=cpf)
        vendor = {key: value for key, value in vendor_as_destination.items() if key != "participant"}
        device_id = new_device()
        ObjectGenerator.build_history(vendor, PayloadGenerator.create_account(participant="60701190"), device_id, [40, 35, 20, 10, 5], 30_000)

        sales = [
            ObjectGenerator.analyze_pix(
                amount=7_000,
                transaction_date=PayloadGenerator.daytime(days_ago=1, hour=15, minute=minute * 3),
                destination_account=vendor_as_destination,
            )
            for minute in range(15)
        ]
        supplier_payment = ObjectGenerator.analyze_pix(
            amount=80_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=19),
            source_account=vendor,
            device_id=device_id,
        )

        assert {sale["status"] for sale in sales} == {"approved"}
        assert all("DESTINATION_FAN_IN" not in rules_of(sale) for sale in sales)
        assert supplier_payment["status"] == "approved"


class TestCustomerDailyLimit:
    def test_customer_ceiling_comes_from_the_biggest_approved_day(self):
        """O maior dia soma os Pix aprovados do mesmo dia e ignora as últimas 24 h."""
        source_account, device_id = PayloadGenerator.create_account(), new_device()
        payee = PayloadGenerator.create_account(participant="60701190")
        ObjectGenerator.build_history(source_account, payee, device_id, [40, 35, 30, 25], 30_000)
        for hour in (9, 15):
            ObjectGenerator.analyze_pix(
                amount=80_000,
                transaction_date=PayloadGenerator.daytime(days_ago=20, hour=hour),
                source_account=source_account,
                destination_account=payee,
                device_id=device_id,
            )
        ObjectGenerator.analyze_pix(
            amount=90_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=10),
            source_account=source_account,
            destination_account=payee,
            device_id=device_id,
        )

        response = ObjectGenerator.analyze_pix(
            amount=10_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=14),
            source_account=source_account,
            destination_account=payee,
            device_id=device_id,
        )

        signals = response["metadata"]["signals"]
        assert signals["max_daily_outflow_90d"] == 160_000
        assert signals["customer_daily_limit"] == 500_000

    def test_stolen_unlocked_phone_above_the_customer_ceiling_asks_two_factor(self):
        """Celular destravado no aparelho de sempre: o teto do cliente pede 2FA, que o ladrão não tem."""
        source_account, device_id = PayloadGenerator.create_account(), new_device()
        payee = PayloadGenerator.create_account(participant="60701190")
        ObjectGenerator.build_history(source_account, payee, device_id, [40, 35, 30, 25, 20], 30_000)

        responses = [
            ObjectGenerator.analyze_pix(
                amount=150_000,
                transaction_date=PayloadGenerator.daytime(days_ago=1, hour=13 + index),
                source_account=source_account,
                destination_account=payee,
                device_id=device_id,
            )
            for index in range(4)
        ]

        assert "DAILY_LIMIT" not in rules_of(responses[2])
        assert responses[3]["status"] == "two_factor_required"
        assert "DAILY_LIMIT" in rules_of(responses[3])


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

    def test_mule_chain_is_cut_at_the_second_link(self):
        """Vítima -> laranja -> conta central, como no caso real mostrado pelo BC.

        A vítima paga um laranja (conta de 3 dias) e passa no 2FA. Quando o
        laranja tenta repassar, o motor reconhece o dinheiro que ele mesmo
        achou arriscado e rejeita. A partir daí a conta central está ligada
        à cadeia, e o próximo cliente que tentar pagá-la é barrado.
        """
        mule_cpf = RandomGenerator.generate_cpf()
        mule_as_destination = PayloadGenerator.create_account(
            participant="60701190", opening_date=PayloadGenerator.daytime(days_ago=4), document_number=mule_cpf
        )
        mule_as_source = {key: value for key, value in mule_as_destination.items() if key != "participant"}
        hub = PayloadGenerator.create_account(participant="18236120")

        victim_pays = ObjectGenerator.analyze_pix(
            amount=300_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=14),
            destination_account=mule_as_destination,
        )
        mule_forwards = ObjectGenerator.analyze_pix(
            amount=290_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=14, minute=20),
            source_account=mule_as_source,
            destination_account=hub,
        )
        next_payer = ObjectGenerator.analyze_pix(
            amount=60_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=15),
            destination_account=hub,
        )

        assert victim_pays["status"] == "two_factor_required"
        assert victim_pays["metadata"]["beneficiary_score"] >= 40
        assert mule_forwards["status"] == "rejected"
        assert "MULE_PASS_THROUGH" in rules_of(mule_forwards)
        assert next_payer["status"] == "rejected"
        assert "MULE_LINK" in rules_of(next_payer)

    def test_paying_the_mule_after_it_was_caught_forwarding(self):
        """A ligação vale nos dois sentidos: quem pagar o próprio laranja depois do repasse barrado também é barrado."""
        mule_cpf = RandomGenerator.generate_cpf()
        mule_as_destination = PayloadGenerator.create_account(
            participant="60701190", opening_date=PayloadGenerator.daytime(days_ago=4), document_number=mule_cpf
        )
        mule_as_source = {key: value for key, value in mule_as_destination.items() if key != "participant"}

        ObjectGenerator.analyze_pix(
            amount=300_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=14),
            destination_account=mule_as_destination,
        )
        mule_forwards = ObjectGenerator.analyze_pix(
            amount=290_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=14, minute=20),
            source_account=mule_as_source,
        )
        second_victim = ObjectGenerator.analyze_pix(
            amount=200_000,
            transaction_date=PayloadGenerator.daytime(days_ago=1, hour=15),
            destination_account=mule_as_destination,
        )

        assert mule_forwards["status"] == "rejected"
        assert second_victim["status"] == "rejected"
        assert "MULE_LINK" in rules_of(second_victim)
        assert "FLAGGED_DESTINATION" not in rules_of(second_victim)

    def test_daily_ceiling_counts_what_already_left(self):
        """R$ 12 mil passa com 2FA; outros R$ 12 mil no mesmo dia estouram o teto de R$ 20 mil e também pedem 2FA."""
        source_account = PayloadGenerator.create_account()
        first = ObjectGenerator.analyze_pix(
            amount=1_200_000, transaction_date=PayloadGenerator.daytime(days_ago=1, hour=10), source_account=source_account
        )
        second = ObjectGenerator.analyze_pix(
            amount=1_200_000, transaction_date=PayloadGenerator.daytime(days_ago=1, hour=16), source_account=source_account
        )

        assert first["status"] == "two_factor_required"
        assert second["status"] == "two_factor_required"
        assert "DAILY_LIMIT" in rules_of(second)
        assert second["metadata"]["signals"]["exposure_last_24h"] == 1_200_000

    def test_rejection_that_is_not_about_the_destination_does_not_flag_it(self):
        """Pix barrado pelo teto noturno do pagador não suja a conta de quem ia receber."""
        destination = PayloadGenerator.create_account(participant="60701190")
        blocked = ObjectGenerator.analyze_pix(
            amount=600_000,
            transaction_date=PayloadGenerator.daytime(days_ago=2, hour=23),
            destination_account=destination,
        )

        response = ObjectGenerator.analyze_pix(destination_account=destination)

        assert blocked["status"] == "rejected"
        assert "FLAGGED_DESTINATION" not in rules_of(response)
        assert response["metadata"]["signals"]["destination_flagged_payers_30d"] == 0

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

    def test_mirror_account_in_the_victims_name_is_not_a_self_transfer(self):
        """Conta espelho: aberta ontem, em outro banco, NO CPF da vítima, para esvaziar a conta dela.

        Mesmo CPF dos dois lados não basta: a conta de destino precisa ser
        antiga ou já conhecida para valer como transferência entre contas
        próprias. Antes desta regra, este Pix era aprovado.
        """
        cpf = RandomGenerator.generate_cpf()
        victim, device_id = PayloadGenerator.create_account(document_number=cpf), new_device()
        ObjectGenerator.build_history(victim, PayloadGenerator.create_account(participant="60701190"), device_id, [40, 35, 20, 10, 5], 100_000)
        mirror = PayloadGenerator.create_account(
            participant="18236120", document_number=cpf, opening_date=PayloadGenerator.daytime(days_ago=2)
        )

        response = ObjectGenerator.analyze_pix(
            amount=1_500_000, source_account=victim, destination_account=mirror, device_id=device_id
        )

        assert response["status"] == "rejected"
        assert "SELF_TRANSFER" not in rules_of(response)
        assert "NEW_DESTINATION_ACCOUNT" in rules_of(response)
        assert response["metadata"]["signals"]["same_owner"] is True

    def test_draining_at_night_spread_over_many_destinations(self):
        """De madrugada, Pix de R$ 930 a R$ 970 para contas antigas diferentes, nenhum valor repetido.

        Nenhum passa do limite noturno, nenhum destino se repete e nenhum valor
        se repete: só a soma da noite enxerga. Acima de R$ 3.000 pede 2FA;
        acima de R$ 5.000, o teto de coação, barra.
        """
        source_account, device_id = PayloadGenerator.create_account(), new_device()
        ObjectGenerator.build_history(source_account, PayloadGenerator.create_account(participant="60701190"), device_id, [40, 35, 20, 10, 5], 100_000)

        amounts = [95_000, 93_000, 97_000, 94_000, 96_000, 98_000]
        responses = [
            ObjectGenerator.analyze_pix(
                amount=amount,
                transaction_date=PayloadGenerator.daytime(days_ago=1, hour=1, minute=8 * index),
                source_account=source_account,
                device_id=device_id,
            )
            for index, amount in enumerate(amounts)
        ]

        assert [response["status"] for response in responses] == [
            "approved", "approved", "approved", "two_factor_required", "two_factor_required", "rejected",
        ]
        assert "NIGHT_TOTAL" in rules_of(responses[3])
        assert "NIGHT_TOTAL_HARD_LIMIT" in rules_of(responses[5])

    def test_first_pix_of_high_value_asks_for_two_factor(self):
        """Sem histórico, R$ 6 mil pede 2FA (30 + 10); R$ 15 mil também, sem chegar a rejeitar."""
        high = ObjectGenerator.analyze_pix(amount=600_000)
        higher = ObjectGenerator.analyze_pix(amount=1_500_000)

        assert high["status"] == "two_factor_required"
        assert high["metadata"]["risk_score"] == 40
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
