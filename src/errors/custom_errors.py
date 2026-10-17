from errors import QIException


class NotFoundPixTransaction(QIException):
    code = "QIT001001"

    def __init__(self, transaction_id) -> None:
        title = "Pix transaction not Found"
        http_status = 404
        description = f"No analysis was recorded for the Pix {transaction_id}."
        translation = f"Nenhuma análise foi gravada para o Pix {transaction_id}."
        super().__init__(title, self.code, http_status, description, translation)


class PixTransactionIdReused(QIException):
    """O mesmo `id` de Pix chegou com um conteúdo diferente.

    O `id` é a chave de idempotência: o mesmo Pix reenviado devolve a
    mesma análise. Um `id` repetido com OUTRO valor, outro recebedor ou
    outra data não é reenvio — é outro Pix tentando usar um id que já tem
    dono, e reavaliar seria deixar uma decisão sobrescrever a outra.
    """

    code = "QIT001002"

    def __init__(self, transaction_id) -> None:
        title = "Pix transaction id reused"
        http_status = 409
        description = f"The Pix id {transaction_id} was already analysed with a different payload."
        translation = "Este id de Pix já foi analisado com outro conteúdo."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDocumentNumber(QIException):
    """O CPF ou CNPJ tem o formato certo e não existe.

    422, e não 400: a API leu o pedido e entendeu; o valor é que não pode
    existir, porque os dígitos verificadores não batem. Pix com documento
    impossível não chega a ser analisado — não é decisão antifraude, é
    entrada inválida.
    """

    code = "QIT001003"

    def __init__(self, field_name, document_number) -> None:
        title = "Invalid Document Number"
        http_status = 422
        description = f"The document number {document_number} in {field_name} is not a valid CPF or CNPJ."
        translation = "O CPF ou CNPJ informado não é válido."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDate(QIException):
    """A data tem o formato certo e não existe no calendário ("2026-02-30")."""

    code = "QIT001004"

    def __init__(self, field_name, raw_date) -> None:
        title = "Invalid Date"
        http_status = 422
        description = f"The date {raw_date} in {field_name} is not a real date."
        translation = "A data informada não existe."
        super().__init__(title, self.code, http_status, description, translation)
