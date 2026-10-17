from datetime import timedelta

from controllers.fraud_engine import parameters as p
from controllers.fraud_engine.rules import evaluate_rules
from controllers.fraud_engine.signals import History, PreviousLocation
from tests.unit.fraud_engine.factory import NOW, make_signals


def rule_codes(signals) -> list:
    return [hit.code for hit in evaluate_rules(signals)]


def find_hit(signals, code):
    for hit in evaluate_rules(signals):
        if hit.code == code:
            return hit
    return None


class TestCleanTransaction:
    def test_daytime_low_value_old_accounts_trigger_nothing(self):
        assert rule_codes(make_signals()) == []


class TestNight:
    def test_limit_applies_from_20h(self):
        at_20h = NOW.replace(hour=20, minute=0)
        at_19h59 = NOW.replace(hour=19, minute=59)
        amount = p.NIGHT_PF_LIMIT + 1

        assert "NIGHT_HIGH_VALUE" in rule_codes(make_signals(transaction_date=at_20h, amount=amount))
        assert "NIGHT_HIGH_VALUE" not in rule_codes(make_signals(transaction_date=at_19h59, amount=amount))

    def test_night_ends_at_6h(self):
        at_5h59 = NOW.replace(hour=5, minute=59)
        at_6h = NOW.replace(hour=6, minute=0)
        amount = p.NIGHT_PF_LIMIT + 1

        assert "NIGHT_HIGH_VALUE" in rule_codes(make_signals(transaction_date=at_5h59, amount=amount))
        assert "NIGHT_HIGH_VALUE" not in rule_codes(make_signals(transaction_date=at_6h, amount=amount))

    def test_exactly_the_limit_is_allowed(self):
        at_22h = NOW.replace(hour=22)
        assert "NIGHT_HIGH_VALUE" not in rule_codes(make_signals(transaction_date=at_22h, amount=p.NIGHT_PF_LIMIT))

    def test_utc_transaction_is_read_in_brasilia_time(self):
        # 01:30 em UTC são 22:30 em Brasília: é noite para o cliente.
        utc_moment = NOW.replace(hour=22, minute=30).astimezone(NOW.tzinfo.utc)
        signals = make_signals(transaction_date=utc_moment, amount=p.NIGHT_PF_LIMIT + 1)

        assert signals.local_hour == 22
        assert "NIGHT_HIGH_VALUE" in rule_codes(signals)

    def test_night_limit_is_only_for_pf(self):
        at_22h = NOW.replace(hour=22)
        signals = make_signals(transaction_date=at_22h, amount=p.NIGHT_PF_HARD_LIMIT + 1, source_document_number="11222333000181")

        assert "NIGHT_HIGH_VALUE" not in rule_codes(signals)
        assert "NIGHT_HARD_LIMIT" not in rule_codes(signals)

    def test_hard_limit_above_5k(self):
        at_23h = NOW.replace(hour=23)
        hit = find_hit(make_signals(transaction_date=at_23h, amount=p.NIGHT_PF_HARD_LIMIT + 1), "NIGHT_HARD_LIMIT")

        assert hit is not None and hit.hard

    def test_exactly_the_hard_limit_asks_for_two_factor_instead(self):
        codes = rule_codes(make_signals(transaction_date=NOW.replace(hour=23), amount=p.NIGHT_PF_HARD_LIMIT))

        assert "NIGHT_HARD_LIMIT" not in codes
        assert "NIGHT_HIGH_VALUE" in codes


class TestColdStart:
    def test_first_pix_device_is_unknown_not_new(self):
        signals = make_signals(History(approved_count_total=0, device_trusted=False))

        assert signals.is_new_device is False
        assert "NEW_DEVICE" not in rule_codes(signals)

    def test_profile_needs_minimum_history(self):
        few = History(approved_amounts_profile=[1_000] * (p.PROFILE_MIN_TRANSACTIONS - 1))
        signals = make_signals(few, amount=1_000_000)

        assert signals.profile_ratio is None
        assert "PROFILE_AMOUNT_SPIKE" not in rule_codes(signals)

    def test_missing_geolocation_is_neutral(self):
        previous = PreviousLocation(-23.56, -46.65, NOW - timedelta(minutes=10), "dvc-1")
        signals = make_signals(History(previous_location=previous), latitude=None, longitude=None)

        assert signals.travel_speed_kmh is None
        assert rule_codes(signals) == []


class TestNewSourceAccount:
    def test_age_tiers(self):
        amount = p.SOURCE_NEW_MIN_AMOUNT
        very_new = find_hit(make_signals(amount=amount, source_opening_date=NOW - timedelta(days=1)), "NEW_SOURCE_ACCOUNT")
        new = find_hit(make_signals(amount=amount, source_opening_date=NOW - timedelta(days=5)), "NEW_SOURCE_ACCOUNT")
        week_old = make_signals(amount=amount, source_opening_date=NOW - timedelta(days=p.SOURCE_NEW_DAYS))

        assert very_new.points == p.SOURCE_VERY_NEW_POINTS
        assert new.points == p.SOURCE_NEW_POINTS
        assert "NEW_SOURCE_ACCOUNT" not in rule_codes(week_old)

    def test_small_pix_from_a_new_account_is_left_alone(self):
        signals = make_signals(amount=p.SOURCE_NEW_MIN_AMOUNT - 1, source_opening_date=NOW - timedelta(days=1))

        assert "NEW_SOURCE_ACCOUNT" not in rule_codes(signals)


class TestDevice:
    def test_new_device_with_history(self):
        history = History(approved_count_total=10, device_trusted=False)
        hit = find_hit(make_signals(history, amount=p.NEW_DEVICE_TRANSACTION_LIMIT), "NEW_DEVICE")

        assert hit.points == p.NEW_DEVICE_POINTS

    def test_new_device_above_bcb_transaction_limit_weighs_more(self):
        history = History(approved_count_total=10, device_trusted=False)
        hit = find_hit(make_signals(history, amount=p.NEW_DEVICE_TRANSACTION_LIMIT + 1), "NEW_DEVICE")

        assert hit.points == p.NEW_DEVICE_POINTS + p.NEW_DEVICE_OVER_LIMIT_POINTS

    def test_new_device_daily_limit_counts_previous_pix_on_the_device(self):
        history = History(approved_count_total=10, device_trusted=False, device_exposure_24h=80_000)

        assert "NEW_DEVICE_DAILY_LIMIT" not in rule_codes(make_signals(history, amount=20_000))
        assert "NEW_DEVICE_DAILY_LIMIT" in rule_codes(make_signals(history, amount=20_001))

    def test_two_payers_on_a_device_weigh_three_block(self):
        assert "SHARED_DEVICE" in rule_codes(make_signals(History(device_distinct_documents=2)))

        farm = rule_codes(make_signals(History(device_distinct_documents=3)))
        assert "DEVICE_FARM" in farm
        assert "SHARED_DEVICE" not in farm

    def test_shared_ip_needs_five_payers(self):
        assert "SHARED_IP" not in rule_codes(make_signals(History(ip_distinct_documents=p.IP_SHARED_DOCUMENTS - 1)))
        assert "SHARED_IP" in rule_codes(make_signals(History(ip_distinct_documents=p.IP_SHARED_DOCUMENTS)))


class TestVelocity:
    def test_third_attempt_in_five_minutes_weighs(self):
        assert "VELOCITY_BURST" not in rule_codes(make_signals(History(attempts_short_window=1, attempts_long_window=1)))
        assert "VELOCITY_BURST" in rule_codes(make_signals(History(attempts_short_window=2, attempts_long_window=2)))

    def test_fifth_attempt_in_five_minutes_blocks(self):
        assert "VELOCITY_BOT" not in rule_codes(make_signals(History(attempts_short_window=3)))
        assert "VELOCITY_BOT" in rule_codes(make_signals(History(attempts_short_window=4)))

    def test_fourth_attempt_in_five_minutes_asks_for_two_factor_alone(self):
        hit = find_hit(make_signals(History(attempts_short_window=3, attempts_long_window=3)), "VELOCITY_BURST")
        assert hit.points == p.VELOCITY_STRONG_BURST_POINTS >= p.TWO_FACTOR_SCORE

    def test_burst_and_hour_volume_are_not_counted_twice(self):
        history = History(attempts_short_window=2, attempts_long_window=20)
        hits = [hit for hit in evaluate_rules(make_signals(history)) if hit.code == "VELOCITY_BURST"]

        assert len(hits) == 1
        assert hits[0].points == p.VELOCITY_BURST_POINTS

    def test_probing_points_are_capped(self):
        hit = find_hit(make_signals(History(rejected_recent=10)), "PROBING_AFTER_REJECTION")
        assert hit.points == p.PROBING_MAX_POINTS

    def test_split_below_the_night_limit_to_one_destination(self):
        at_23h = NOW.replace(hour=23)
        two_parts = History(destination_parts_recent=2, destination_amount_recent=2 * 99_000)

        hit = find_hit(make_signals(two_parts, transaction_date=at_23h, amount=99_000), "SPLIT_PAYMENT")

        assert hit.points == p.SPLIT_POINTS
        assert "NIGHT_HIGH_VALUE" not in rule_codes(make_signals(two_parts, transaction_date=at_23h, amount=99_000))

    def test_split_needs_three_parts_and_a_sum_above_the_limit(self):
        at_23h = NOW.replace(hour=23)
        one_part = History(destination_parts_recent=1, destination_amount_recent=99_000)
        small_parts = History(destination_parts_recent=2, destination_amount_recent=2 * 20_000)

        assert "SPLIT_PAYMENT" not in rule_codes(make_signals(one_part, transaction_date=at_23h, amount=99_000))
        assert "SPLIT_PAYMENT" not in rule_codes(make_signals(small_parts, transaction_date=at_23h, amount=20_000))

    def test_split_uses_the_high_value_limit_during_the_day(self):
        three_parts = History(destination_parts_recent=2, destination_amount_recent=2 * 200_000)

        assert "SPLIT_PAYMENT" in rule_codes(make_signals(three_parts, amount=200_000))

    def test_split_limit_is_higher_for_companies(self):
        company = dict(source_document_number="11222333000181")
        three_parts = History(destination_parts_recent=2, destination_amount_recent=2 * 200_000)
        above_company_limit = History(destination_parts_recent=2, destination_amount_recent=2 * 2_000_000)

        assert "SPLIT_PAYMENT" not in rule_codes(make_signals(three_parts, amount=200_000, **company))
        assert "SPLIT_PAYMENT" in rule_codes(make_signals(above_company_limit, amount=2_000_000, **company))

    def test_part_above_the_limit_is_left_to_the_limit_rule(self):
        at_23h = NOW.replace(hour=23)
        history = History(destination_parts_recent=2, destination_amount_recent=2 * 150_000)
        codes = rule_codes(make_signals(history, transaction_date=at_23h, amount=150_000))

        assert "NIGHT_HIGH_VALUE" in codes
        assert "SPLIT_PAYMENT" not in codes

    def test_equal_split_to_one_destination_is_not_counted_twice(self):
        at_23h = NOW.replace(hour=23)
        history = History(same_amount_recent=2, destination_parts_recent=2, destination_amount_recent=2 * 99_000)
        codes = rule_codes(make_signals(history, transaction_date=at_23h, amount=99_000))

        assert "SPLIT_PAYMENT" in codes
        assert "STRUCTURING" not in codes

    def test_structuring_needs_three_equal_amounts(self):
        assert "STRUCTURING" not in rule_codes(make_signals(History(same_amount_recent=1)))
        assert "STRUCTURING" in rule_codes(make_signals(History(same_amount_recent=2)))


class TestImpossibleTravel:
    def previous(self, device_id):
        # São Paulo, 30 minutos antes.
        return PreviousLocation(-23.56, -46.65, NOW - timedelta(minutes=30), device_id)

    def test_recife_thirty_minutes_after_sao_paulo_same_device(self):
        signals = make_signals(History(previous_location=self.previous("dvc-1")), latitude=-8.05, longitude=-34.88)
        codes = rule_codes(signals)

        assert signals.travel_distance_km > 2000
        assert "IMPOSSIBLE_TRAVEL" in codes
        assert "IMPOSSIBLE_TRAVEL_OTHER_DEVICE" not in codes

    def test_other_device_blocks(self):
        signals = make_signals(History(previous_location=self.previous("dvc-other")), latitude=-8.05, longitude=-34.88)
        assert "IMPOSSIBLE_TRAVEL_OTHER_DEVICE" in rule_codes(signals)

    def test_two_pix_in_the_same_second_do_not_divide_by_zero(self):
        previous = PreviousLocation(-23.56, -46.65, NOW, "dvc-1")
        signals = make_signals(History(previous_location=previous), latitude=-8.05, longitude=-34.88)

        assert signals.travel_speed_kmh > p.IMPOSSIBLE_TRAVEL_SPEED_KMH
        assert "IMPOSSIBLE_TRAVEL" in rule_codes(signals)

    def test_short_hop_is_gps_noise(self):
        # Guarulhos fica a ~20 km: rápido, mas dentro do erro de GPS/IP.
        previous = PreviousLocation(-23.56, -46.65, NOW - timedelta(seconds=30), "dvc-other")
        signals = make_signals(History(previous_location=previous), latitude=-23.43, longitude=-46.47)

        assert rule_codes(signals) == []


class TestDestination:
    def test_destination_age_tiers(self):
        very_new = find_hit(make_signals(destination_opening_date=NOW - timedelta(days=3)), "NEW_DESTINATION_ACCOUNT")
        new = find_hit(make_signals(destination_opening_date=NOW - timedelta(days=20)), "NEW_DESTINATION_ACCOUNT")

        assert very_new.points == p.DESTINATION_VERY_NEW_POINTS
        assert new.points == p.DESTINATION_NEW_POINTS

    def test_fan_in_threshold_is_higher_for_companies(self):
        payers = History(destination_distinct_payers=p.FAN_IN_PF_PAYERS)

        assert "DESTINATION_FAN_IN" in rule_codes(make_signals(payers))
        assert "DESTINATION_FAN_IN" not in rule_codes(make_signals(payers, destination_document_number="11222333000181"))

    def test_fan_in_weighs_more_from_ten_payers(self):
        hit = find_hit(make_signals(History(destination_distinct_payers=p.FAN_IN_PF_HIGH_PAYERS)), "DESTINATION_FAN_IN")

        assert hit.points == p.FAN_IN_HIGH_POINTS

    def test_flagged_destination(self):
        one = find_hit(make_signals(History(destination_flagged_payers=1)), "FLAGGED_DESTINATION")
        three = find_hit(make_signals(History(destination_flagged_payers=3)), "FLAGGED_DESTINATION")

        assert one.points == p.FLAGGED_DESTINATION_POINTS
        assert three.points == p.FLAGGED_DESTINATION_HIGH_POINTS

    def test_profile_spike_uses_median_with_floor(self):
        # Mediana de R$ 5 sobe para o piso de R$ 100: R$ 250 é 2,5x, não 50x.
        history = History(approved_amounts_profile=[500] * 10)
        assert "PROFILE_AMOUNT_SPIKE" not in rule_codes(make_signals(history, amount=25_000))

    def test_profile_median_ignores_one_big_outlier(self):
        history = History(approved_amounts_profile=[10_000] * 9 + [5_000_000])
        signals = make_signals(history, amount=50_000)

        assert signals.profile_median == 10_000
        assert find_hit(signals, "PROFILE_AMOUNT_SPIKE").points == p.PROFILE_SPIKE_POINTS

    def test_amount_ten_times_the_median_is_extreme(self):
        history = History(approved_amounts_profile=[10_000] * 10)

        assert find_hit(make_signals(history, amount=100_000), "PROFILE_AMOUNT_SPIKE").points == p.PROFILE_SPIKE_POINTS
        assert find_hit(make_signals(history, amount=100_001), "PROFILE_AMOUNT_SPIKE").points == p.PROFILE_EXTREME_POINTS

    def test_high_value_without_profile_starts_at_5k(self):
        hit = find_hit(make_signals(amount=p.HIGH_VALUE_AMOUNT), "HIGH_VALUE_WITHOUT_PROFILE")

        assert "HIGH_VALUE_WITHOUT_PROFILE" not in rule_codes(make_signals(amount=p.HIGH_VALUE_AMOUNT - 1))
        assert hit.points == p.HIGH_VALUE_POINTS

    def test_high_value_is_left_to_the_profile_when_there_is_one(self):
        history = History(approved_amounts_profile=[1_000_000] * p.PROFILE_MIN_TRANSACTIONS)
        codes = rule_codes(make_signals(history, amount=2 * p.HIGH_VALUE_AMOUNT))

        assert "HIGH_VALUE_WITHOUT_PROFILE" not in codes
        assert "PROFILE_AMOUNT_SPIKE" not in codes

    def test_high_value_threshold_is_higher_for_companies(self):
        signals = make_signals(amount=2 * p.HIGH_VALUE_AMOUNT, source_document_number="11222333000181")
        assert "HIGH_VALUE_WITHOUT_PROFILE" not in rule_codes(signals)

    def test_new_counterparty_only_above_minimum(self):
        assert "NEW_COUNTERPARTY" not in rule_codes(make_signals(amount=p.NEW_COUNTERPARTY_MIN_AMOUNT))
        assert "NEW_COUNTERPARTY" in rule_codes(make_signals(amount=p.NEW_COUNTERPARTY_MIN_AMOUNT + 1))


class TestDailyLimit:
    def test_daily_ceiling_counts_what_already_left(self):
        history = History(exposure_24h=p.DAILY_PF_HARD_LIMIT - 10_000)

        assert "DAILY_HARD_LIMIT" not in rule_codes(make_signals(history, amount=10_000))
        hit = find_hit(make_signals(history, amount=10_001), "DAILY_HARD_LIMIT")
        assert hit.hard and hit.category == "beneficiary"

    def test_more_than_half_the_ceiling_weighs_without_blocking(self):
        half = p.DAILY_PF_HARD_LIMIT // 2
        codes_at_half = rule_codes(make_signals(History(exposure_24h=half - 10_000), amount=10_000))
        codes_above_half = rule_codes(make_signals(History(exposure_24h=half), amount=10_000))

        assert "DAILY_EXPOSURE" not in codes_at_half
        assert "DAILY_EXPOSURE" in codes_above_half
        assert "DAILY_HARD_LIMIT" not in codes_above_half

    def test_companies_have_a_ten_times_higher_ceiling(self):
        history = History(exposure_24h=p.DAILY_PF_HARD_LIMIT)
        codes = rule_codes(make_signals(history, amount=100_000, source_document_number="11222333000181"))

        assert "DAILY_HARD_LIMIT" not in codes
        assert "DAILY_EXPOSURE" not in codes


class TestMuleChain:
    def test_forwarding_money_from_many_payers_weighs(self):
        history = History(source_inbound_payers=3, source_inbound_amount=300_000)
        hit = find_hit(make_signals(history, amount=200_000), "MULE_PASS_THROUGH")

        assert hit.points == p.MULE_PASS_THROUGH_POINTS
        assert hit.category == "beneficiary"

    def test_forwarding_money_the_engine_already_found_risky_rejects_alone(self):
        history = History(source_inbound_payers=1, source_inbound_amount=300_000, source_inbound_suspicious_payers=1)
        hit = find_hit(make_signals(history, amount=280_000), "MULE_PASS_THROUGH")

        assert hit.points == p.MULE_DIRTY_MONEY_POINTS >= p.REJECT_BENEFICIARY_SCORE

    def test_keeping_the_money_is_not_forwarding(self):
        history = History(source_inbound_payers=3, source_inbound_amount=300_000, source_inbound_suspicious_payers=1)
        assert "MULE_PASS_THROUGH" not in rule_codes(make_signals(history, amount=100_000))

    def test_forwarding_counts_what_already_left_today(self):
        history = History(
            source_inbound_payers=3, source_inbound_amount=300_000, exposure_24h=100_000
        )
        assert "MULE_PASS_THROUGH" in rule_codes(make_signals(history, amount=60_000))

    def test_small_collections_and_companies_are_not_mules(self):
        small = History(source_inbound_payers=4, source_inbound_amount=60_000)
        company = History(source_inbound_payers=50, source_inbound_amount=5_000_000)

        assert "MULE_PASS_THROUGH" not in rule_codes(make_signals(small, amount=60_000))
        assert "MULE_PASS_THROUGH" not in rule_codes(
            make_signals(company, amount=4_000_000, source_document_number="11222333000181")
        )

    def test_a_company_forwarding_money_the_engine_found_risky_is_still_caught(self):
        """Empresa de fachada também serve de laranja: o +25 é só para PF, o +60 vale para todos."""
        history = History(source_inbound_payers=1, source_inbound_amount=300_000, source_inbound_suspicious_payers=1)
        signals = make_signals(history, amount=280_000, source_document_number="11222333000181")

        assert find_hit(signals, "MULE_PASS_THROUGH").points == p.MULE_DIRTY_MONEY_POINTS

    def test_group_collection_does_not_count_the_profile_spike_twice(self):
        """Vaquinha: R$ 1.500 de 4 amigos repassados para a pousada, por quem costuma pagar R$ 80."""
        history = History(
            approved_amounts_profile=[8_000] * 10, source_inbound_payers=4, source_inbound_amount=150_000
        )
        codes = rule_codes(make_signals(history, amount=150_000))

        assert "MULE_PASS_THROUGH" in codes
        assert "PROFILE_AMOUNT_SPIKE" not in codes

    def test_dirty_money_keeps_the_profile_spike(self):
        history = History(
            approved_amounts_profile=[8_000] * 10,
            source_inbound_payers=1,
            source_inbound_amount=150_000,
            source_inbound_suspicious_payers=1,
        )
        codes = rule_codes(make_signals(history, amount=150_000))

        assert "MULE_PASS_THROUGH" in codes
        assert "PROFILE_AMOUNT_SPIKE" in codes

    def test_paying_an_account_linked_to_a_chain(self):
        hit = find_hit(make_signals(History(destination_mule_links=1)), "MULE_LINK")
        assert hit.points == p.MULE_LINK_POINTS


class TestTrust:
    def test_trusted_counterparty_needs_time_and_repetition(self):
        young = History(counterparty_approved_count=5, counterparty_first_approved_at=NOW - timedelta(days=3))
        single = History(counterparty_approved_count=1, counterparty_first_approved_at=NOW - timedelta(days=60))
        trusted = History(counterparty_approved_count=2, counterparty_first_approved_at=NOW - timedelta(days=30))

        assert "TRUSTED_COUNTERPARTY" not in rule_codes(make_signals(young))
        assert "TRUSTED_COUNTERPARTY" not in rule_codes(make_signals(single))
        assert "TRUSTED_COUNTERPARTY" in rule_codes(make_signals(trusted))

    def test_self_transfer_to_an_old_own_account_skips_destination_rules(self):
        signals = make_signals(
            destination_document_number="12345678909",
            destination_opening_date=NOW - timedelta(days=p.SELF_TRANSFER_MIN_DESTINATION_DAYS),
            amount=100_000,
        )
        codes = rule_codes(signals)

        assert "SELF_TRANSFER" in codes
        assert "NEW_DESTINATION_ACCOUNT" not in codes
        assert "NEW_COUNTERPARTY" not in codes

    def test_self_transfer_is_not_split_high_value_or_a_mule(self):
        """Juntar o salário que entrou e mandar para a própria poupança não é repasse de laranja."""
        history = History(
            destination_parts_recent=2,
            destination_amount_recent=2 * 400_000,
            source_inbound_payers=3,
            source_inbound_amount=1_500_000,
            source_inbound_suspicious_payers=1,
            destination_mule_links=1,
        )
        signals = make_signals(
            history,
            amount=1_200_000,
            destination_document_number="12345678909",
            destination_opening_date=NOW - timedelta(days=400),
        )
        codes = rule_codes(signals)

        assert "SELF_TRANSFER" in codes
        for code in ("SPLIT_PAYMENT", "HIGH_VALUE_WITHOUT_PROFILE", "MULE_PASS_THROUGH", "MULE_LINK"):
            assert code not in codes

    def test_same_cpf_on_a_brand_new_account_is_not_a_self_transfer(self):
        """Conta espelho: aberta ontem no CPF da vítima. Mesmo dono no papel, não na prática."""
        signals = make_signals(
            destination_document_number="12345678909",
            destination_opening_date=NOW - timedelta(days=1),
            amount=100_000,
        )
        codes = rule_codes(signals)

        assert signals.same_owner is True
        assert signals.is_self_transfer is False
        assert "SELF_TRANSFER" not in codes
        assert "NEW_DESTINATION_ACCOUNT" in codes

    def test_new_own_account_already_paid_a_week_ago_is_a_self_transfer(self):
        history = History(counterparty_approved_count=1, counterparty_first_approved_at=NOW - timedelta(days=8))
        signals = make_signals(
            history,
            destination_document_number="12345678909",
            destination_opening_date=NOW - timedelta(days=10),
        )

        assert "SELF_TRANSFER" in rule_codes(signals)


MATURE = dict(approved_count_total=p.MATURE_MIN_APPROVED)


class TestMaturePayer:
    """Racha do bar, família no mesmo celular, folha de pagamento: conta madura não é robô."""

    def test_maturity_needs_old_account_and_history(self):
        assert make_signals(History(**MATURE)).is_mature_payer is True
        assert make_signals(History(approved_count_total=p.MATURE_MIN_APPROVED - 1)).is_mature_payer is False
        young = make_signals(History(**MATURE), source_opening_date=NOW - timedelta(days=p.MATURE_ACCOUNT_DAYS - 1))
        assert young.is_mature_payer is False

    def test_five_quick_pix_from_a_mature_account_ask_two_factor_instead_of_rejecting(self):
        burst = History(attempts_short_window=p.VELOCITY_BOT_ATTEMPTS - 1, **MATURE)
        signals = make_signals(burst)

        assert "VELOCITY_BOT" not in rule_codes(signals)
        assert "VELOCITY_BURST" in rule_codes(signals)

    def test_mature_individual_is_still_blocked_at_ten(self):
        signals = make_signals(History(attempts_short_window=p.VELOCITY_BOT_MATURE_PF_ATTEMPTS - 1, **MATURE))

        assert find_hit(signals, "VELOCITY_BOT").hard is True

    def test_mature_company_paying_payroll_has_a_higher_ceiling(self):
        company = dict(source_document_number="11222333000181")
        payroll = History(attempts_short_window=p.VELOCITY_BOT_MATURE_PF_ATTEMPTS, **MATURE)
        robot = History(attempts_short_window=p.VELOCITY_BOT_MATURE_PJ_ATTEMPTS - 1, **MATURE)

        assert "VELOCITY_BOT" not in rule_codes(make_signals(payroll, **company))
        assert "VELOCITY_BOT" in rule_codes(make_signals(robot, **company))

    def test_new_account_keeps_the_strict_limit(self):
        assert "VELOCITY_BOT" in rule_codes(make_signals(History(attempts_short_window=p.VELOCITY_BOT_ATTEMPTS - 1)))

    def test_family_of_three_on_one_phone_asks_two_factor(self):
        hit = find_hit(make_signals(History(device_distinct_documents=3, **MATURE)), "DEVICE_FARM")

        assert hit.hard is False
        assert hit.points == p.DEVICE_FARM_MATURE_POINTS

    def test_five_payers_on_one_phone_is_a_farm_even_for_mature_accounts(self):
        hit = find_hit(make_signals(History(device_distinct_documents=p.DEVICE_FARM_HARD_DOCUMENTS, **MATURE)), "DEVICE_FARM")

        assert hit.hard is True

    def test_three_payers_with_a_new_account_is_still_a_farm(self):
        assert find_hit(make_signals(History(device_distinct_documents=3)), "DEVICE_FARM").hard is True


class TestNightTotal:
    """Drenagem de madrugada espalhada por destinos diferentes, cada Pix abaixo do limite."""

    NIGHT = NOW.replace(hour=1)

    def test_night_sum_above_three_times_the_limit_weighs(self):
        signals = make_signals(History(night_outflow=285_000), amount=95_000, transaction_date=self.NIGHT)

        assert find_hit(signals, "NIGHT_TOTAL").points == p.NIGHT_TOTAL_POINTS

    def test_below_the_soft_limit_nothing_happens(self):
        signals = make_signals(History(night_outflow=190_000), amount=95_000, transaction_date=self.NIGHT)

        assert "NIGHT_TOTAL" not in rule_codes(signals)

    def test_night_sum_above_the_coercion_ceiling_blocks(self):
        signals = make_signals(History(night_outflow=480_000), amount=95_000, transaction_date=self.NIGHT)

        assert find_hit(signals, "NIGHT_TOTAL_HARD_LIMIT").hard is True
        assert "NIGHT_TOTAL" not in rule_codes(signals)

    def test_a_single_pix_above_the_night_limit_is_not_counted_twice(self):
        signals = make_signals(History(night_outflow=250_000), amount=150_000, transaction_date=self.NIGHT)

        assert "NIGHT_HIGH_VALUE" in rule_codes(signals)
        assert "NIGHT_TOTAL" not in rule_codes(signals)

    def test_daytime_and_companies_are_out(self):
        day = make_signals(History(night_outflow=480_000), amount=95_000)
        company = make_signals(
            History(night_outflow=480_000), amount=95_000, transaction_date=self.NIGHT, source_document_number="11222333000181"
        )

        assert "NIGHT_TOTAL_HARD_LIMIT" not in rule_codes(day)
        assert "NIGHT_TOTAL_HARD_LIMIT" not in rule_codes(company)

    def test_night_starts_at_20h_of_the_previous_day_after_midnight(self):
        from controllers.fraud_engine.signals import night_start

        assert night_start(NOW.replace(hour=1)) == (NOW - timedelta(days=1)).replace(hour=20)
        assert night_start(NOW.replace(hour=22)) == NOW.replace(hour=20)
