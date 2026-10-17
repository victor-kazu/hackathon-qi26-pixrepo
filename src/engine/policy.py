"""Como as regras acionadas viram UMA decisão.

A ordem é a da tabela abaixo; a primeira linha que bate decide.

  1. alguma regra dura disparou              -> rejected
  2. risco de destino >= 60                  -> rejected
  3. risco total (identidade + destino) >= 90 -> rejected
  4. risco total >= 40                       -> two_factor_required
  5. o resto                                 -> approved

A linha 2 é o centro da política: 2FA prova que é o dono da conta quem
está pagando, e no golpe de engenharia social é exatamente o dono quem
paga. Quando o problema é o destino, pedir 2FA só atrasa a vítima.
"""

from dataclasses import dataclass
from typing import List

from engine import parameters as p
from engine.rules import BENEFICIARY, IDENTITY, RuleHit, evaluate_rules
from engine.signals import Signals

APPROVED = "approved"
TWO_FACTOR_REQUIRED = "two_factor_required"
REJECTED = "rejected"


@dataclass(frozen=True)
class Decision:
    status: str
    identity_score: int
    beneficiary_score: int
    hits: List[RuleHit]
    reason: str

    @property
    def risk_score(self) -> int:
        return self.identity_score + self.beneficiary_score

    def to_metadata(self, signals: Signals) -> dict:
        return {
            "risk_score": self.risk_score,
            "identity_score": self.identity_score,
            "beneficiary_score": self.beneficiary_score,
            "decision_reason": self.reason,
            "triggered_rules": [hit.to_dict() for hit in self.hits],
            "signals": signals.to_dict(),
            "thresholds": {
                "two_factor_required": p.TWO_FACTOR_SCORE,
                "rejected": p.REJECT_SCORE,
                "rejected_by_beneficiary": p.REJECT_BENEFICIARY_SCORE,
            },
            "policy_version": p.POLICY_VERSION,
        }


def decide(signals: Signals) -> Decision:
    hits = evaluate_rules(signals)

    # Bônus nunca deixa uma categoria negativa: confiar no destino não
    # pode "pagar" a dúvida sobre a identidade, e vice-versa.
    identity_score = max(0, _sum_points(hits, IDENTITY))
    beneficiary_score = max(0, _sum_points(hits, BENEFICIARY))
    total = identity_score + beneficiary_score

    hard_hits = [hit for hit in hits if hit.hard]
    if hard_hits:
        reason = "Bloqueado por regra de risco claro: " + "; ".join(hit.detail for hit in hard_hits) + "."
        return Decision(REJECTED, identity_score, beneficiary_score, hits, reason)

    if beneficiary_score >= p.REJECT_BENEFICIARY_SCORE:
        reason = (
            f"Risco do destino em {beneficiary_score} pontos (limite {p.REJECT_BENEFICIARY_SCORE}). "
            "Golpe de engenharia social passa no 2FA, então o Pix é bloqueado."
        )
        return Decision(REJECTED, identity_score, beneficiary_score, hits, reason)

    if total >= p.REJECT_SCORE:
        reason = f"Combinação de sinais somou {total} pontos (limite {p.REJECT_SCORE})."
        return Decision(REJECTED, identity_score, beneficiary_score, hits, reason)

    if total >= p.TWO_FACTOR_SCORE:
        reason = (
            f"Dúvida moderada ({total} pontos, 2FA a partir de {p.TWO_FACTOR_SCORE}): "
            "o cliente confirma que é ele antes do Pix sair."
        )
        return Decision(TWO_FACTOR_REQUIRED, identity_score, beneficiary_score, hits, reason)

    reason = f"Risco baixo ({total} pontos, 2FA a partir de {p.TWO_FACTOR_SCORE})."
    return Decision(APPROVED, identity_score, beneficiary_score, hits, reason)


def _sum_points(hits: List[RuleHit], category: str) -> int:
    total = 0
    for hit in hits:
        if hit.category == category:
            total = total + hit.points

    return total
