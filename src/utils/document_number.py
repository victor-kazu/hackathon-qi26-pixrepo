CPF_LENGTH = 11
CNPJ_LENGTH = 14

CPF_CHECK_DIGIT_POSITIONS = [9, 10]
CNPJ_FIRST_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
CNPJ_SECOND_WEIGHTS = [6] + CNPJ_FIRST_WEIGHTS


def only_digits(document_number: str) -> str:
    """"123.456.789-09" e "12345678909" são o mesmo CPF: guardamos só os dígitos."""
    digits = ""
    for character in document_number:
        if character.isdigit():
            digits = digits + character

    return digits


def is_valid_document(document_number: str) -> bool:
    digits = only_digits(document_number)

    if len(digits) == CPF_LENGTH:
        return is_valid_cpf(digits)

    if len(digits) == CNPJ_LENGTH:
        return is_valid_cnpj(digits)

    return False


def is_valid_cpf(document_number: str) -> bool:
    """Diz se um CPF existe de verdade, e não só se tem a cara certa.

    Os dois últimos dígitos são o RESULTADO de uma conta sobre os nove
    primeiros: para cada um, multiplica os anteriores por pesos
    decrescentes, soma, e tira o resto por 11. Resto menor que 2 vira 0;
    senão, o dígito é 11 menos o resto.
    """
    digits = [int(character) for character in only_digits(document_number)]

    if len(digits) != CPF_LENGTH or _all_equal(digits):
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
    """Mesma ideia do CPF, com os pesos que a Receita definiu para CNPJ."""
    digits = [int(character) for character in only_digits(document_number)]

    if len(digits) != CNPJ_LENGTH or _all_equal(digits):
        return False

    for weights in (CNPJ_FIRST_WEIGHTS, CNPJ_SECOND_WEIGHTS):
        position = len(weights)
        total = 0
        for index, weight in enumerate(weights):
            total = total + digits[index] * weight

        if digits[position] != _check_digit(total):
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
