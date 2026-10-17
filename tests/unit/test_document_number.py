from utils.document_number import is_valid_cnpj, is_valid_cpf, is_valid_document, normalize_document

# O exemplo oficial da Receita para o CNPJ alfanumérico.
OFFICIAL_ALPHANUMERIC_CNPJ = "12.ABC.345/01DE-35"


class TestAlphanumericCnpj:
    def test_official_example_is_valid(self):
        assert is_valid_cnpj(OFFICIAL_ALPHANUMERIC_CNPJ)
        assert is_valid_document(OFFICIAL_ALPHANUMERIC_CNPJ)

    def test_mask_and_case_do_not_matter(self):
        assert is_valid_document("12ABC34501DE35")
        assert is_valid_document("12.abc.345/01de-35")

    def test_wrong_check_digit_is_invalid(self):
        assert not is_valid_cnpj("12.ABC.345/01DE-36")

    def test_check_digits_must_be_numbers(self):
        assert not is_valid_cnpj("12.ABC.345/01DE-3A")

    def test_numeric_cnpj_keeps_working(self):
        """O valor ASCII − 48 de um dígito é o próprio dígito: o CNPJ antigo vale como antes."""
        assert is_valid_cnpj("11.222.333/0001-81")
        assert not is_valid_cnpj("11.222.333/0001-82")
        assert not is_valid_cnpj("11.111.111/1111-11")


class TestCpf:
    def test_letters_are_not_accepted_in_a_cpf(self):
        assert is_valid_cpf("123.456.789-09")
        assert not is_valid_cpf("123.456.78A-09")


class TestNormalization:
    def test_same_document_with_or_without_mask(self):
        assert normalize_document("123.456.789-09") == normalize_document("12345678909") == "12345678909"
        assert normalize_document("12.abc.345/01de-35") == "12ABC34501DE35"
