
import base64
import os
from functools import lru_cache
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def get_cipher():
    encoded_key = os.environ.get("FIELD_ENCRYPTION_KEY")

    if not encoded_key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY belum dikonfigurasi.")

    try:
        key = base64.b64decode(encoded_key, altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("Format FIELD_ENCRYPTION_KEY tidak valid.") from exc

    if len(key) != 32:
        raise RuntimeError("FIELD_ENCRYPTION_KEY harus berukuran 256-bit.")

    return AESGCM(key)


def encrypt_field(value, *, field):
    if value is None:
        return None

    if not isinstance(value, str) or not field:
        raise ValueError("Nilai dan nama field tidak valid.")

    nonce = os.urandom(12)
    ciphertext = get_cipher().encrypt(
        nonce,
        value.encode("utf-8"),
        field.encode("utf-8")
    )

    payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return PREFIX + payload


def decrypt_field(value, *, field):
    if value is None:
        return None

    if not isinstance(value, str) or not value.startswith(PREFIX) or not field:
        raise ValueError("Data terenkripsi tidak valid.")

    payload = base64.b64decode(
        value[len(PREFIX):],
        altchars=b"-_",
        validate=True
    )

    if len(payload) < 28:
        raise ValueError("Data terenkripsi tidak lengkap.")

    nonce, ciphertext = payload[:12], payload[12:]

    plaintext = get_cipher().decrypt(
        nonce,
        ciphertext,
        field.encode("utf-8")
    )

    return plaintext.decode("utf-8")