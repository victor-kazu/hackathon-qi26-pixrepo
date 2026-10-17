from typing import List, Optional
from uuid import uuid4

from database import Context
from models import PixAnalysis, PixRuleHit, PixTransaction


class PixAnalysisRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(
        self,
        pix: PixTransaction,
        status: str,
        score: int,
        decided_by: str,
        rules_version: str,
        hits: List,
    ) -> PixAnalysis:
        """Grava a decisão e cada regra que disparou, na mesma transação do Pix."""
        analysis = PixAnalysis()
        analysis.analysis_key = uuid4()
        analysis.pix_transaction_id = pix.id
        analysis.status = status
        analysis.score = score
        analysis.decided_by = decided_by
        analysis.rules_version = rules_version

        for hit in hits:
            rule_hit = PixRuleHit()
            rule_hit.rule_code = hit.code
            rule_hit.points = hit.points
            rule_hit.forced_status = hit.forced_status
            rule_hit.detail = hit.detail
            analysis.rule_hits.append(rule_hit)

        self.session.add(analysis)
        self.session.flush()

        return analysis

    def get_by_transaction(self, pix: PixTransaction) -> Optional[PixAnalysis]:
        return self.session.query(PixAnalysis).filter(PixAnalysis.pix_transaction_id == pix.id).first()
