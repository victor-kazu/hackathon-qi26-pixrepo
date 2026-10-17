from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from controllers import PixTransactionController
from utils.schema_handler import SchemaHandler


class PixTransactionResource:
    """A porta de entrada HTTP do motor antifraude.

    Confere o corpo, chama o controller e devolve a resposta com o status
    certo. Nenhuma regra mora aqui — a política inteira está em
    src/controllers/fraud/.
    """

    @SchemaHandler.validate("post_pix_transaction.json")
    def on_post(self, payload: dict) -> JSONResponse:
        """201 quando o Pix foi analisado agora; 200 quando é o reenvio de um Pix já analisado.

        Os dois devolvem o mesmo corpo. A diferença de status é como quem
        chamou sabe, sem comparar corpos, se a chamada dele gerou uma
        análise ou só a confirmou.
        """
        controller = PixTransactionController()
        analysis, created = controller.analyze(payload)

        if created:
            status_code = http_status.HTTP_201_CREATED
        else:
            status_code = http_status.HTTP_200_OK

        return JSONResponse(content=jsonable_encoder(analysis), status_code=status_code)

    def on_get_by_id(self, transaction_id: str) -> JSONResponse:
        controller = PixTransactionController()
        analysis = controller.get_by_id(transaction_id)

        return JSONResponse(content=jsonable_encoder(analysis), status_code=http_status.HTTP_200_OK)
