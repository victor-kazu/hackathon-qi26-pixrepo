CPF_LENGTH = 11
CNPJ_LENGTH = 14

CPF_CHECK_DIGIT_POSITIONS = [9, 10]
CNPJ_FIRST_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
CNPJ_SECOND_WEIGHTS = [6] + CNPJ_FIRST_WEIGHTS

# Desde julho de 2026 a Receita emite CNPJ ALFANUMÉRICO: as 12 primeiras
# posições podem ter letras maiúsculas; os 2 dígitos verificadores
# continuam numéricos. A conta é a mesma de sempre, trocando o valor de
# cada caractere pelo código ASCII menos 48: "7" vale 7 (por isso o CNPJ
# só de números continua valendo exatamente como antes) e "A" vale 17.
ASCII_OFFSET = 48


def normalize_document(document_number: str) -> str:
    """Tira a máscara e põe as letras em maiúscula.

    "123.456.789-09" e "12345678909" são o mesmo CPF, e "12.abc.345/01de-35"
    é o mesmo CNPJ que "12ABC34501DE35": é esta forma que vai para o banco,
    para o mesmo documento não virar dois no histórico.
    """
    normalized = ""
    for character in document_number.upper():
        if character.isalnum():
            normalized = normalized + character

    return normalized


def is_valid_document(document_number: str) -> bool:
    normalized = normalize_document(document_number)

    if len(normalized) == CPF_LENGTH:
        return is_valid_cpf(normalized)

    if len(normalized) == CNPJ_LENGTH:
        return is_valid_cnpj(normalized)

    return False


def is_valid_cpf(document_number: str) -> bool:
    """Diz se um CPF existe de verdade, e não só se tem a cara certa.

    Os dois últimos dígitos são o RESULTADO de uma conta sobre os nove
    primeiros: para cada um, multiplica os anteriores por pesos
    decrescentes, soma, e tira o resto por 11. Resto menor que 2 vira 0;
    senão, o dígito é 11 menos o resto.
    """
    normalized = normalize_document(document_number)

    # CPF continua só de números: letra num CPF é documento inválido.
    if len(normalized) != CPF_LENGTH or not normalized.isdigit():
        return False

    digits = [int(character) for character in normalized]

    if _all_equal(digits):
        return False

    for position in CPF_CHECK_DIGIT_POSITIONS:
        first_weight = position + 1
        total = 0
        for index in range(position):
            total = total + digits[index] * (first_weight - index)

        if digits[position] != _check_digit(total):
            return False

    return True


def is_valid_cnpj(document_number: str) -> bool:
    """Mesma ideia do CPF, com os pesos da Receita para CNPJ — numérico ou alfanumérico."""
    normalized = normalize_document(document_number)

    if len(normalized) != CNPJ_LENGTH:
        return False

    # Letra só nas 12 primeiras posições (a base e a ordem); os dois
    # verificadores são sempre dígitos.
    base, check_digits = normalized[:12], normalized[12:]
    if not check_digits.isdigit():
        return False

    for character in base:
        if not ("0" <= character <= "9" or "A" <= character <= "Z"):
            return False

    values = [ord(character) - ASCII_OFFSET for character in normalized]

    if _all_equal(values):
        return False

    for weights in (CNPJ_FIRST_WEIGHTS, CNPJ_SECOND_WEIGHTS):
        position = len(weights)
        total = 0
        for index, weight in enumerate(weights):
            total = total + values[index] * weight

        if values[position] != _check_digit(total):
            return False

    return True


def _check_digit(total: int) -> int:
    remainder = total % 11
    if remainder < 2:
        return 0

    return 11 - remainder


def _all_equal(digits: list) -> bool:
    # "111.111.111-11" passa na conta e não vale: a Receita não emite.
    return len(set(digits)) == 1
