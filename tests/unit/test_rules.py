from dataclasses import replace

from fraud import parameters as p
from fraud import rules
from fraud.engine import decide
from fraud.signals import Signals

# Um Pix comum: R$ 150 às 14h, pagador com histórico, aparelho e recebedor
# conhecidos. Nenhuma regra dispara sobre ele — cada teste muda só o sinal
# que quer provar.
NEUTRAL = Signals(
    amount=15_000,
    local_hour=14,
    payer_document_type="CPF",
    destination_document_type="CPF",
    same_owner=False,
    source_account_age_days=400,
    destination_account_age_days=400,
    payer_count_window=1,
    same_destination_count_window=1,
    same_destination_sum_window=15_000,
    payer_sum_24h=15_000,
    payer_max_daily_approved=20_000,
    payer_approved_count=10,
    payer_approved_average=15_000,
    payer_approved_max_amount=30_000,
    destination_distinct_payers_24h=1,
    counterparty_approved_count=0,
    device_used_before_by_payer=True,
    device_days_with_payer=10,
    device_distinct_payers_7d=1,
    latitude=None,
    longitude=None,
    previous_latitude=None,
    previous_longitude=None,
    minutes_since_previous_location=None,
)


def codes(signals: Signals) -> list:
    return [hit.code for hit in decide(signals).hits]


class TestNeutral:
    def test_ordinary_pix_triggers_nothing(self):
        decision = decide(NEUTRAL)

        assert decision.hits == []
        assert decision.status == "approved"
        assert decision.score == 0


class TestNightLimit:
    def test_cpf_above_limit_at_night_is_rejected(self):
        hit = rules.night_limit(replace(NEUTRAL, amount=p.NIGHT_LIMIT + 1, local_hour=23))

        assert hit.forced_status == "rejected"

    def test_exactly_the_limit_passes(self):
        assert rules.night_limit(replace(NEUTRAL, amount=p.NIGHT_LIMIT, local_hour=23)) is None

    def test_night_window_edges(self):
        above = p.NIGHT_LIMIT + 1

        assert rules.night_limit(replace(NEUTRAL, amount=above, local_hour=20)) is not None
        assert rules.night_limit(replace(NEUTRAL, amount=above, local_hour=5)) is not None
        assert rules.night_limit(replace(NEUTRAL, amount=above, local_hour=6)) is None
        assert rules.night_limit(replace(NEUTRAL, amount=above, local_hour=19)) is None

    def test_cnpj_is_not_subject_to_the_rule(self):
        assert rules.night_limit(replace(NEUTRAL, amount=500_000, local_hour=23, payer_document_type="CNPJ")) is None

    def test_trust_cannot_compensate_a_hard_rule(self):
        """Contraparte conhecida e mesmo titular não desfazem o limite do BCB."""
        signals = replace(NEUTRAL, amount=200_000, local_hour=23, same_owner=True, counterparty_approved_count=10)

        assert decide(signals).status == "rejected"
        assert decide(signals).decided_by == "NIGHT_LIMIT"


class TestHighAmount:
    def test_without_precedent(self):
        hit = rules.high_amount(replace(NEUTRAL, amount=p.HIGH_AMOUNT + 1))

        assert hit.points == p.HIGH_AMOUNT_POINTS

    def test_very_high(self):
        hit = rules.high_amount(replace(NEUTRAL, amount=p.VERY_HIGH_AMOUNT + 1))

        assert hit.points == p.VERY_HIGH_AMOUNT_POINTS

    def test_payer_already_did_it_before(self):
        signals = replace(NEUTRAL, amount=800_000, payer_approved_max_amount=800_000)

        assert rules.high_amount(signals) is None


class TestVelocity:
    def test_below_threshold(self):
        assert rules.velocity(replace(NEUTRAL, payer_count_window=p.VELOCITY_TWO_FACTOR_COUNT - 1)) is None

    def test_two_factor_threshold(self):
        hit = rules.velocity(replace(NEUTRAL, payer_count_window=p.VELOCITY_TWO_FACTOR_COUNT))

        assert hit.points == p.VELOCITY_TWO_FACTOR_POINTS
        assert decide(replace(NEUTRAL, payer_count_window=p.VELOCITY_TWO_FACTOR_COUNT)).status == "two_factor_required"

    def test_reject_threshold(self):
        hit = rules.velocity(replace(NEUTRAL, payer_count_window=p.VELOCITY_REJECT_COUNT))

        assert hit.forced_status == "rejected"


class TestSplitPayment:
    def test_three_small_pix_that_together_pass_the_night_limit(self):
        signals = replace(
            NEUTRAL,
            local_hour=22,
            amount=90_000,
            same_destination_count_window=3,
            same_destination_sum_window=270_000,
        )

        assert rules.split_payment(signals).points == p.SPLIT_POINTS

    def test_same_count_but_sum_below_ceiling(self):
        signals = replace(NEUTRAL, same_destination_count_window=3, same_destination_sum_window=45_000)

        assert rules.split_payment(signals) is None

    def test_a_single_big_pix_is_not_splitting(self):
        """Quem manda acima do teto não está fragmentando — outras regras cuidam dele."""
        signals = replace(
            NEUTRAL, amount=600_000, same_destination_count_window=3, same_destination_sum_window=900_000
        )

        assert rules.split_payment(signals) is None


class TestDailySum:
    def test_with_history_uses_three_times_the_biggest_day(self):
        signals = replace(NEUTRAL, payer_max_daily_approved=20_000, payer_sum_24h=60_001)

        assert rules.daily_sum(signals).detail["limit"] == 60_000

    def test_without_history_uses_the_fixed_ceiling(self):
        signals = replace(NEUTRAL, payer_max_daily_approved=0, payer_sum_24h=p.DAILY_SUM_WITHOUT_HISTORY + 1)

        assert rules.daily_sum(signals).detail["limit"] == p.DAILY_SUM_WITHOUT_HISTORY

    def test_alone_it_does_not_ask_for_two_factor(self):
        """Sinal fraco sozinho não trava o cliente."""
        signals = replace(NEUTRAL, payer_sum_24h=10_000_000)

        assert decide(signals).status == "approved"


class TestProfile:
    def test_far_above_average(self):
        signals = replace(NEUTRAL, amount=15_000 * p.PROFILE_FACTOR + 1)

        assert rules.out_of_profile(signals) is not None

    def test_needs_minimum_history(self):
        signals = replace(NEUTRAL, amount=1_000_000, payer_approved_count=p.PROFILE_MIN_APPROVED - 1)

        assert rules.out_of_profile(signals) is None


class TestDestinationAndSourceAccounts:
    def test_brand_new_destination_with_relevant_amount_asks_two_factor(self):
        signals = replace(NEUTRAL, destination_account_age_days=0.5, amount=150_000)

        assert rules.new_destination_account(signals).points == p.BRAND_NEW_DESTINATION_POINTS
        assert decide(signals).status == "two_factor_required"

    def test_trust_in_the_payer_does_not_cancel_a_brand_new_receiver(self):
        """Golpe autorizado: a vítima paga do aparelho de sempre. Confiança no pagador não absolve o recebedor."""
        signals = replace(
            NEUTRAL,
            destination_account_age_days=0.2,
            amount=200_000,
            payer_approved_max_amount=300_000,
            payer_approved_average=100_000,
            device_days_with_payer=200,
            counterparty_approved_count=0,
        )

        decision = decide(signals)

        assert "TRUSTED_DEVICE" in codes(signals)
        assert decision.score < p.TWO_FACTOR_SCORE
        assert decision.status == "two_factor_required"
        assert decision.decided_by == "NEW_DESTINATION_ACCOUNT"

    def test_new_destination_small_amount(self):
        signals = replace(NEUTRAL, destination_account_age_days=0.5, amount=5_000)

        assert rules.new_destination_account(signals).points == p.NEW_DESTINATION_POINTS

    def test_week_old_destination_is_fine(self):
        assert rules.new_destination_account(replace(NEUTRAL, destination_account_age_days=7)) is None

    def test_fan_in_only_for_cpf_receivers(self):
        many = replace(NEUTRAL, destination_distinct_payers_24h=p.FAN_IN_PAYERS)

        assert rules.destination_fan_in(many) is not None
        assert rules.destination_fan_in(replace(many, destination_document_type="CNPJ")) is None

    def test_new_source_account_with_relevant_amount(self):
        signals = replace(NEUTRAL, source_account_age_days=2, amount=150_000)

        assert rules.new_source_account(signals) is not None


class TestDevice:
    def test_new_device_small_and_high_amount(self):
        new = replace(NEUTRAL, device_used_before_by_payer=False, device_days_with_payer=None)

        assert rules.new_device(new).points == p.NEW_DEVICE_POINTS
        assert rules.new_device(replace(new, amount=150_000)).points == p.NEW_DEVICE_HIGH_AMOUNT_POINTS

    def test_shared_device(self):
        assert rules.shared_device(replace(NEUTRAL, device_distinct_payers_7d=2)).points == p.SHARED_DEVICE_TWO_PAYERS_POINTS
        assert rules.shared_device(replace(NEUTRAL, device_distinct_payers_7d=3)).forced_status == "rejected"

    def test_trusted_device_after_thirty_days(self):
        assert rules.trusted_device(replace(NEUTRAL, device_days_with_payer=29)) is None
        assert rules.trusted_device(replace(NEUTRAL, device_days_with_payer=31)).points == p.TRUSTED_DEVICE_POINTS


class TestImpossibleTravel:
    SAO_PAULO = (-23.56, -46.65)
    RECIFE = (-8.05, -34.88)

    def located(self, previous, current, minutes):
        return replace(
            NEUTRAL,
            latitude=current[0],
            longitude=current[1],
            previous_latitude=previous[0],
            previous_longitude=previous[1],
            minutes_since_previous_location=minutes,
        )

    def test_sao_paulo_to_recife_in_thirty_minutes(self):
        hit = rules.impossible_travel(self.located(self.SAO_PAULO, self.RECIFE, 30))

        assert hit is not None
        assert hit.detail["distance_km"] > 2000

    def test_same_trip_in_five_hours_is_possible(self):
        assert rules.impossible_travel(self.located(self.SAO_PAULO, self.RECIFE, 300)) is None

    def test_gps_jitter_is_ignored(self):
        nearby = (-23.57, -46.66)

        assert rules.impossible_travel(self.located(self.SAO_PAULO, nearby, 0)) is None


class TestTrust:
    def test_history_absorbs_a_moderately_high_amount(self):
        """O cenário das direcionais: cliente com histórico não deveria passar por 2FA."""
        signals = replace(
            NEUTRAL,
            amount=600_000,
            payer_approved_max_amount=20_000,
            payer_approved_average=20_000,
            counterparty_approved_count=5,
            device_days_with_payer=40,
        )

        decision = decide(signals)

        assert {"HIGH_AMOUNT", "OUT_OF_PROFILE", "KNOWN_COUNTERPARTY", "TRUSTED_DEVICE"} <= set(codes(signals))
        assert decision.status == "approved"

    def test_score_never_goes_below_zero(self):
        signals = replace(NEUTRAL, same_owner=True, counterparty_approved_count=10, device_days_with_payer=90)

        assert decide(signals).score == 0
