"""Como as regras viram uma decisão."""

from dataclasses import dataclass
from typing import List, Optional

from fraud import parameters as p
from fraud.rules import RULES, RuleHit
from fraud.signals import Signals
from models import PixAnalysis

# Da mais branda para a mais severa.
SEVERITY = [PixAnalysis.APPROVED, PixAnalysis.TWO_FACTOR_REQUIRED, PixAnalysis.REJECTED]


@dataclass(frozen=True)
class Decision:
    status: str
    score: int
    decided_by: str
    hits: List[RuleHit]


def _most_severe(a: str, b: str) -> str:
    if SEVERITY.index(a) >= SEVERITY.index(b):
        return a
    return b


def status_for_score(score: int) -> str:
    if score >= p.REJECT_SCORE:
        return PixAnalysis.REJECTED

    if score >= p.TWO_FACTOR_SCORE:
        return PixAnalysis.TWO_FACTOR_REQUIRED

    return PixAnalysis.APPROVED


def decide(signals: Signals) -> Decision:
    """Roda todas as regras e escolhe a decisão mais severa entre as duas fontes.

    1. Todas as regras rodam, sempre — a análise registra cada uma que
       disparou, mesmo depois de uma regra dura já ter decidido.
    2. O score é a soma dos pontos, com os de confiança subtraindo, e
       nunca fica abaixo de zero.
    3. A decisão é a MAIS SEVERA entre o que o score pede e o que alguma
       regra dura impôs. Uma regra dura não pode ser compensada por
       pontos de confiança: o limite noturno vale para o cliente fiel
       também.
    """
    hits = []
    for rule in RULES:
        hit = rule(signals)
        if hit is not None:
            hits.append(hit)

    score = max(sum(hit.points for hit in hits), 0)
    status = status_for_score(score)
    decided_by = "score"

    forced_by: Optional[RuleHit] = None
    for hit in hits:
        if hit.forced_status is None:
            continue

        if forced_by is None or SEVERITY.index(hit.forced_status) > SEVERITY.index(forced_by.forced_status):
            forced_by = hit

    if forced_by is not None and _most_severe(forced_by.forced_status, status) != status:
        status = forced_by.forced_status
        decided_by = forced_by.code

    return Decision(status=status, score=score, decided_by=decided_by, hits=hits)
