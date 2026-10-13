import pytest

from verter.core.security import (
    generate_password,
    generate_secret,
    hash_password,
    verify_password,
)


def test_hash_round_trip() -> None:
    encoded = hash_password("s3cret")
    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password("s3cret", encoded)
    assert not verify_password("S3cret", encoded)


def test_hashes_are_salted() -> None:
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "plain-text",
        "md5$abc",
        "scrypt$a$b$c$d$e",
        "scrypt$16384$8$1$not*base64$AAAA",
        "scrypt$3$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAA",
    ],
)
def test_malformed_hashes_never_match(encoded: str) -> None:
    assert not verify_password("anything", encoded)


def test_generated_values_are_unique_and_url_safe() -> None:
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) == 16 and p.replace("-", "").replace("_", "").isalnum() for p in passwords)
    assert len(generate_secret()) >= 43
