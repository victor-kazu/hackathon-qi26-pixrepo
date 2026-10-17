from datetime import timedelta

from controllers.fraud_engine import parameters as p
from controllers.fraud_engine.policy import APPROVED, REJECTED, TWO_FACTOR_REQUIRED, decide
from controllers.fraud_engine.signals import History
from tests.unit.fraud_engine.factory import NOW, make_signals


class TestDecision:
    def test_clean_pix_is_approved_without_points(self):
        decision = decide(make_signals())

        assert decision.status == APPROVED
        assert decision.risk_score == 0
        assert decision.hits == []

    def test_challenge_example_asks_for_two_factor(self):
        """O Pix do enunciado: R$ 1.500 às 22h15 para uma conta de 7 dias."""
        signals = make_signals(
            amount=150_000,
            transaction_date=NOW.replace(hour=22, minute=15),
            destination_opening_date=NOW.replace(hour=22, minute=15) - timedelta(days=7, hours=13),
        )
        decision = decide(signals)

        assert decision.status == TWO_FACTOR_REQUIRED
        assert decision.identity_score == p.NIGHT_HIGH_VALUE_POINTS

    def test_single_weak_signal_does_not_add_friction(self):
        decision = decide(make_signals(destination_opening_date=NOW - timedelta(days=20), amount=60_000))

        assert decision.status == APPROVED
        assert decision.risk_score == p.DESTINATION_NEW_POINTS + p.NEW_COUNTERPARTY_POINTS

    def test_hard_rule_rejects_even_with_trust_bonus(self):
        history = History(
            attempts_short_window=4,
            counterparty_approved_count=10,
            counterparty_first_approved_at=NOW - timedelta(days=90),
        )
        decision = decide(make_signals(history))

        assert decision.status == REJECTED
        assert "5 tentativas" in decision.reason

    def test_scam_signals_reject_even_when_identity_is_clean(self):
        """Conta laranja recebendo de vários: 2FA não ajudaria, a vítima confirmaria."""
        history = History(destination_distinct_payers=p.FAN_IN_PF_PAYERS)
        decision = decide(make_signals(history, destination_opening_date=NOW - timedelta(days=2), amount=60_000))

        assert decision.identity_score == 0
        assert decision.beneficiary_score >= p.REJECT_BENEFICIARY_SCORE
        assert decision.status == REJECTED
        assert "engenharia social" in decision.reason

    def test_identity_doubt_asks_for_two_factor_instead_of_rejecting(self):
        history = History(approved_count_total=20, device_trusted=False, rejected_recent=1, attempts_short_window=2)
        decision = decide(make_signals(history, amount=15_000))

        # aparelho novo (30) + rajada (25) + sondagem (20) = 75: dúvida, não certeza.
        assert decision.identity_score == 75
        assert decision.status == TWO_FACTOR_REQUIRED

    def test_many_independent_signals_reject_by_total(self):
        history = History(approved_count_total=20, device_trusted=False, attempts_short_window=2, device_exposure_24h=0)
        decision = decide(
            make_signals(
                history,
                amount=19_000,
                transaction_date=NOW.replace(hour=23),
                destination_opening_date=NOW - timedelta(days=3),
            )
        )

        # identidade: aparelho novo (30) + rajada (25); destino: conta nova (30) = 85
        assert decision.status == TWO_FACTOR_REQUIRED

        history = History(approved_count_total=20, device_trusted=False, attempts_short_window=2, rejected_recent=1)
        decision = decide(make_signals(history, amount=19_000, destination_opening_date=NOW - timedelta(days=3)))

        assert decision.risk_score >= p.REJECT_SCORE
        assert decision.status == REJECTED

    def test_trusted_counterparty_removes_friction_from_rent(self):
        """Aluguel de R$ 2.500, bem acima da mediana do cliente, para quem ele paga todo mês."""
        history = History(
            approved_count_total=30,
            device_trusted=True,
            approved_amounts_profile=[8_000] * 20,
            counterparty_approved_count=3,
            counterparty_first_approved_at=NOW - timedelta(days=90),
        )
        decision = decide(make_signals(history, amount=250_000))

        # O valor é 31x a mediana (+35), e a relação antiga compensa (-30).
        assert decision.status == APPROVED
        assert decision.risk_score < p.TWO_FACTOR_SCORE

    def test_bonus_on_destination_does_not_pay_for_identity_doubt(self):
        history = History(
            approved_count_total=30,
            device_trusted=False,
            counterparty_approved_count=3,
            counterparty_first_approved_at=NOW - timedelta(days=90),
        )
        decision = decide(make_signals(history, amount=30_000))

        assert decision.beneficiary_score == 0
        assert decision.identity_score == p.NEW_DEVICE_POINTS + p.NEW_DEVICE_OVER_LIMIT_POINTS
        assert decision.status == TWO_FACTOR_REQUIRED

    def test_metadata_explains_the_decision(self):
        signals = make_signals(amount=150_000, transaction_date=NOW.replace(hour=22))
        metadata = decide(signals).to_metadata(signals)

        assert metadata["policy_version"] == p.POLICY_VERSION
        assert metadata["triggered_rules"][0]["rule"] == "NIGHT_HIGH_VALUE"
        assert metadata["signals"]["local_hour"] == 22
        assert metadata["decision_reason"]
