from dataclasses import replace
from datetime import datetime, timedelta, timezone

from engine.signals import History, TransactionFacts, build_signals

BRASILIA = timezone(timedelta(hours=-3))
NOW = datetime(2026, 10, 17, 14, 0, tzinfo=BRASILIA)


def make_facts(**overrides) -> TransactionFacts:
    """Um Pix sem nenhum sinal de risco: dia, valor baixo, contas antigas."""
    facts = TransactionFacts(
        pix_id="pix-unit",
        amount=10_000,
        transaction_date=NOW,
        source_branch="0001",
        source_account_number="1111",
        source_opening_date=NOW - timedelta(days=700),
        source_document_number="12345678909",
        destination_participant="60701190",
        destination_branch="0001",
        destination_account_number="2222",
        destination_opening_date=NOW - timedelta(days=700),
        destination_document_number="98765432100",
        device_id="dvc-1",
        channel="app",
        ip="10.0.0.1",
    )
    return replace(facts, **overrides)


def make_signals(history: History = None, **fact_overrides):
    if history is None:
        history = History()

    return build_signals(make_facts(**fact_overrides), history)
