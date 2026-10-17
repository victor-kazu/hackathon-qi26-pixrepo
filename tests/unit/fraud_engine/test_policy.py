import json
from datetime import timedelta

from controllers.fraud_engine import parameters as p
from controllers.fraud_engine import policy
from controllers.fraud_engine.policy import APPROVED, REJECTED, TWO_FACTOR_REQUIRED, decide
from controllers.fraud_engine.rules import BENEFICIARY, IDENTITY, RuleHit
from controllers.fraud_engine.signals import History, PreviousLocation
from tests.unit.fraud_engine.factory import NOW, make_signals


def identity(points: int) -> RuleHit:
    return RuleHit("TEST_IDENTITY", IDENTITY, points, False, "teste")


def beneficiary(points: int) -> RuleHit:
    return RuleHit("TEST_BENEFICIARY", BENEFICIARY, points, False, "teste")


def decide_hits(monkeypatch, *hits):
    """Decide com regras inventadas: testa a tabela de decisão sem depender de nenhuma regra real."""
    monkeypatch.setattr(policy, "evaluate_rules", lambda signals: list(hits))
    return decide(make_signals())


class TestDecisionTable:
    """As fronteiras de policy.py, ponto a ponto."""

    def test_two_factor_starts_at_40(self, monkeypatch):
        assert decide_hits(monkeypatch, identity(39)).status == APPROVED
        assert decide_hits(monkeypatch, identity(40)).status == TWO_FACTOR_REQUIRED

    def test_total_rejects_at_90(self, monkeypatch):
        assert decide_hits(monkeypatch, identity(50), beneficiary(39)).status == TWO_FACTOR_REQUIRED
        assert decide_hits(monkeypatch, identity(50), beneficiary(40)).status == REJECTED

    def test_destination_alone_rejects_at_60(self, monkeypatch):
        assert decide_hits(monkeypatch, beneficiary(59)).status == TWO_FACTOR_REQUIRED
        assert decide_hits(monkeypatch, beneficiary(60)).status == REJECTED

    def test_identity_alone_never_rejects_below_90(self, monkeypatch):
        """Dúvida de identidade se resolve com 2FA, por mais alta que seja."""
        assert decide_hits(monkeypatch, identity(89)).status == TWO_FACTOR_REQUIRED

    def test_hard_rule_rejects_with_zero_points(self, monkeypatch):
        decision = decide_hits(monkeypatch, RuleHit("TEST_HARD", IDENTITY, 0, True, "regra dura"))

        assert decision.risk_score == 0
        assert decision.status == REJECTED

    def test_bonus_is_floored_at_zero_in_its_own_bucket(self, monkeypatch):
        decision = decide_hits(monkeypatch, identity(45), beneficiary(-30))

        assert decision.beneficiary_score == 0
        assert decision.risk_score == 45
        assert decision.status == TWO_FACTOR_REQUIRED

    def test_bonus_offsets_points_in_the_same_bucket(self, monkeypatch):
        decision = decide_hits(monkeypatch, beneficiary(65), beneficiary(-30))

        assert decision.beneficiary_score == 35
        assert decision.status == APPROVED


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

    def test_metadata_with_every_signal_filled_is_valid_json(self):
        """A metadata vai para uma coluna JSONB: nenhum datetime ou objeto pode vazar para ela."""
        evening = NOW.replace(hour=21)
        history = History(
            approved_count_total=12,
            approved_amounts_profile=[10_000] * 12,
            counterparty_approved_count=3,
            counterparty_first_approved_at=evening - timedelta(days=40),
            previous_location=PreviousLocation(-23.56, -46.65, evening - timedelta(hours=2), "dvc-1"),
            source_inbound_payers=2,
            source_inbound_amount=200_000,
            destination_mule_links=1,
            night_outflow=50_000,
        )
        signals = make_signals(history, latitude=-23.50, longitude=-46.60, transaction_date=evening)
        metadata = decide(signals).to_metadata(signals)

        assert json.loads(json.dumps(metadata)) == metadata
        assert metadata["signals"]["travel_distance_km"] is not None
        assert metadata["signals"]["counterparty_relationship_days"] == 40
