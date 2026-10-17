from models import PixAnalysis, PixTransaction


class PixAnalysisDTO:
    """A resposta do contrato: só `key`, `status` e `metadata`.

    Tudo o que explica a decisão vai em `metadata`, montado SÓ a partir do
    que ficou gravado. É isso que faz o POST, o reenvio do mesmo Pix e o
    GET devolverem exatamente o mesmo corpo.
    """

    @staticmethod
    def analysis_to_dict(analysis: PixAnalysis, pix: PixTransaction) -> dict:
        reasons = []
        for hit in analysis.rule_hits:
            reasons.append(
                {
                    "rule": hit.rule_code,
                    "points": hit.points,
                    "forced_status": hit.forced_status,
                    "detail": hit.detail,
                }
            )

        return {
            "key": str(analysis.analysis_key),
            "status": analysis.status,
            "metadata": {
                "transaction_id": pix.transaction_id,
                "score": analysis.score,
                "decided_by": analysis.decided_by,
                "rules_version": analysis.rules_version,
                "reasons": reasons,
            },
        }
