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


class TestTrust:
    def test_trusted_counterparty_needs_time_and_repetition(self):
        young = History(counterparty_approved_count=5, counterparty_first_approved_at=NOW - timedelta(days=3))
        single = History(counterparty_approved_count=1, counterparty_first_approved_at=NOW - timedelta(days=60))
        trusted = History(counterparty_approved_count=2, counterparty_first_approved_at=NOW - timedelta(days=30))

        assert "TRUSTED_COUNTERPARTY" not in rule_codes(make_signals(young))
        assert "TRUSTED_COUNTERPARTY" not in rule_codes(make_signals(single))
        assert "TRUSTED_COUNTERPARTY" in rule_codes(make_signals(trusted))

    def test_self_transfer_skips_destination_age(self):
        signals = make_signals(
            destination_document_number="12345678909",
            destination_opening_date=NOW - timedelta(days=1),
            amount=100_000,
        )
        codes = rule_codes(signals)

        assert "SELF_TRANSFER" in codes
        assert "NEW_DESTINATION_ACCOUNT" not in codes
        assert "NEW_COUNTERPARTY" not in codes
