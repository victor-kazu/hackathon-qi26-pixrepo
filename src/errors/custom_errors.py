from errors import QIException


class NotFoundPixTransaction(QIException):
    code = "QIT002001"

    def __init__(self, pix_id) -> None:
        title = "Pix transaction not Found"
        http_status = 404
        description = f"There is no analysis for the Pix transaction {pix_id}."
        translation = f"Não existe análise para o Pix {pix_id}."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDocumentNumber(QIException):
    """O CPF/CNPJ tem o formato certo e não existe (dígito verificador não bate)."""

    code = "QIT002002"

    def __init__(self, field, document_number) -> None:
        title = "Invalid Document Number"
        http_status = 422
        description = f"The document number {document_number} in {field} is not a valid CPF or CNPJ."
        translation = f"O CPF/CNPJ informado em {field} não é válido."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDate(QIException):
    """A data tem o formato certo e não existe no calendário (ex.: 2026-02-30)."""

    code = "QIT002003"

    def __init__(self, field, value) -> None:
        title = "Invalid Date"
        http_status = 422
        description = f"The value {value} in {field} is not a real date."
        translation = f"A data informada em {field} não existe."
        super().__init__(title, self.code, http_status, description, translation)


class AccountOpenedAfterTransaction(QIException):
    """Uma conta não pode ter sido aberta depois do Pix que ela fez ou recebeu."""

    code = "QIT002004"

    def __init__(self, field) -> None:
        title = "Inconsistent Opening Date"
        http_status = 422
        description = f"{field} is after transaction_date."
        translation = f"A data de abertura em {field} é posterior à data do Pix."
        super().__init__(title, self.code, http_status, description, translation)


class FutureTransactionDate(QIException):
    """O Pix diz que aconteceu no futuro.

    Aceitar seria deixar alguém plantar histórico adiantado e envenenar
    as janelas de velocity dos próximos Pix.
    """

    code = "QIT002005"

    def __init__(self, transaction_date, tolerance_minutes) -> None:
        title = "Future Transaction Date"
        http_status = 422
        description = f"transaction_date {transaction_date} is more than {tolerance_minutes} minutes in the future."
        translation = "A data do Pix está no futuro."
        super().__init__(title, self.code, http_status, description, translation)
