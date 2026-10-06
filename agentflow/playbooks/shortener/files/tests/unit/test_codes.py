import pytest

from shortener.codes import ALPHABET, InvalidAlias, generate_code, validate_alias


def test_generated_codes_use_base62_and_requested_length():
    code = generate_code(9)
    assert len(code) == 9
    assert set(code) <= set(ALPHABET)


def test_generated_codes_are_not_sequential():
    assert len({generate_code() for _ in range(500)}) == 500


@pytest.mark.parametrize("alias", ["abc", "spring-sale", "Q4_promo", "a" * 32])
def test_valid_aliases(alias):
    assert validate_alias(alias) == alias


@pytest.mark.parametrize(
    "alias", ["ab", "a" * 33, "has space", "emoji🙂", "../etc", "API", "healthz"]
)
def test_invalid_or_reserved_aliases(alias):
    with pytest.raises(InvalidAlias):
        validate_alias(alias)
