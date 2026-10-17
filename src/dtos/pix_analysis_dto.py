from copy import deepcopy

from models import PixAnalysis


class PixAnalysisDTO:
    """A análise no formato do contrato: `key`, `status` e `metadata`.

    O POST e o GET devolvem exatamente o mesmo corpo. A metadata é a que
    foi gravada no momento da decisão, com a versão da política daquele
    dia, e não é recalculada na consulta.
    """

    @staticmethod
    def obj_to_dict(pix_analysis: PixAnalysis) -> dict:
        metadata = deepcopy(pix_analysis.analysis_metadata)
        metadata["pix_id"] = pix_analysis.pix_transaction.pix_id

        return {
            "key": pix_analysis.analysis_key,
            "status": pix_analysis.status.enumerator,
            "metadata": metadata,
        }
