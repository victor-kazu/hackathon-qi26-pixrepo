CPF_LENGTH = 11

CHECK_DIGIT_POSITIONS = [9, 10]


def is_valid_cpf(document_number: str) -> bool:
    """Diz se um CPF existe de verdade — não se ele tem a cara certa.

    O schema em src/schemas/post_pix_transaction.json já cobrou o formato:
    três pontos, um hífen, onze dígitos. Isto aqui é outra pergunta, e a
    diferença entre as duas é a lição deste arquivo.

    Os dois últimos dígitos de um CPF não são escolhidos: eles são o
    RESULTADO de uma conta feita sobre os nove primeiros. Por isso um
    número pode ter o formato perfeito e não existir — "111.222.333-44"
    passa no schema e não passa aqui.

    É o mesmo motivo pelo qual a resposta dessa recusa é 422 e não 400:
    não é que a API não conseguiu ler o pedido; ela leu, entendeu, e o
    valor não pode existir.

    A conta, para cada um dos dois dígitos: multiplica cada dígito
    anterior por um peso que decresce, soma tudo, tira o resto da divisão
    por 11. Resto menor que 2 vira dígito 0; nos outros casos, o dígito é
    11 menos o resto.
    """
    digits = []
    for character in document_number:
        if character.isdigit():
            digits.append(int(character))

    if len(digits) != CPF_LENGTH:
        return False

    # Um CPF de dígitos todos iguais ("111.111.111-11") passa na conta
    # dos dígitos verificadores e mesmo assim não vale. São onze números
    # conhecidos, e a Receita não emite nenhum deles.
    all_digits_are_equal = True
    for digit in digits:
        if digit != digits[0]:
            all_digits_are_equal = False
            break

    if all_digits_are_equal:
        return False

    for position in CHECK_DIGIT_POSITIONS:
        total = 0
        first_weight = position + 1

        for index in range(position):
            total = total + digits[index] * (first_weight - index)

        remainder = total % 11

        if remainder < 2:
            expected_digit = 0
        else:
            expected_digit = 11 - remainder

        if digits[position] != expected_digit:
            return False

    return True


CNPJ_LENGTH = 14
CNPJ_FIRST_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
CNPJ_SECOND_WEIGHTS = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def normalize_document(document_number: str) -> str:
    """Tira a máscara: "123.456.789-09" e "12345678909" são o mesmo documento.

    Sem isso, o mesmo pagador escrito com e sem pontuação viraria dois
    pagadores no histórico, e o motor perderia o perfil dele.
    """
    normalized = ""
    for character in document_number.upper():
        if character.isalnum():
            normalized = normalized + character

    return normalized


def document_type(document_number: str) -> str:
    """CPF tem 11 caracteres; CNPJ tem 14. O comprimento sem máscara decide."""
    if len(normalize_document(document_number)) == CPF_LENGTH:
        return "CPF"

    return "CNPJ"


def is_valid_cnpj(document_number: str) -> bool:
    """Diz se um CNPJ existe de verdade, inclusive no formato alfanumérico.

    Desde julho de 2026 a Receita emite CNPJ com LETRAS nas 12 primeiras
    posições; os dois dígitos verificadores continuam numéricos. A conta é
    a mesma de sempre, com uma troca: cada caractere vale o seu código
    ASCII menos 48. Para os dígitos isso dá o próprio dígito ("7" vale 7),
    e é por isso que a mesma função valida os dois formatos.
    """
    characters = normalize_document(document_number)

    if len(characters) != CNPJ_LENGTH:
        return False

    if not characters[12:].isdigit():
        return False

    for character in characters[:12]:
        if not ("0" <= character <= "9" or "A" <= character <= "Z"):
            return False

    if len(set(characters)) == 1:
        return False

    values = [ord(character) - 48 for character in characters]

    for weights, position in [(CNPJ_FIRST_WEIGHTS, 12), (CNPJ_SECOND_WEIGHTS, 13)]:
        total = 0
        for index, weight in enumerate(weights):
            total = total + values[index] * weight

        remainder = total % 11

        if remainder < 2:
            expected_digit = 0
        else:
            expected_digit = 11 - remainder

        if values[position] != expected_digit:
            return False

    return True


def is_valid_document(document_number: str) -> bool:
    if document_type(document_number) == "CPF":
        return is_valid_cpf(document_number)

    return is_valid_cnpj(document_number)
