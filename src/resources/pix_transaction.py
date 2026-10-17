from fastapi import status as http_status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from controllers import PixTransactionController
from utils.schema_handler import SchemaHandler


class PixTransactionResource:
    """A porta HTTP do motor antifraude: confere o corpo, chama o controller, devolve."""

    @SchemaHandler.validate("post_pix_transaction.json")
    def on_post(self, payload: dict) -> JSONResponse:
        controller = PixTransactionController()
        pix_analysis, created = controller.analyze(payload)

        # 201 quando a análise nasceu agora; 200 quando o mesmo id chegou
        # de novo e a resposta é a análise que já existia.
        status_code = http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK

        return JSONResponse(content=jsonable_encoder(pix_analysis), status_code=status_code)

    def on_get_by_id(self, pix_id: str) -> JSONResponse:
        controller = PixTransactionController()
        pix_analysis = controller.get_by_pix_id(pix_id)

        return JSONResponse(content=jsonable_encoder(pix_analysis), status_code=http_status.HTTP_200_OK)
