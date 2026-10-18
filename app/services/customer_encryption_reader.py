"""Membaca data pribadi dari kolom *_enc secara aman."""

from datetime import date

from app.models.customer_address import CustomerAddress
from app.models.customer_profile import CustomerProfile
from app.models.order import Order
from app.services.field_crypto import decrypt_field


PRIVATE_FIELDS = {
    CustomerProfile: ("phone", "birth_date"),
    CustomerAddress: (
        "recipient_name",
        "phone",
        "full_address",
    ),
    Order: (
        "customer_name",
        "customer_email",
        "recipient_name",
        "phone",
        "full_address",
    ),
}


def read_customer_private(record, field):
    model = type(record)
    allowed_fields = PRIVATE_FIELDS.get(model)

    if allowed_fields is None or field not in allowed_fields:
        raise ValueError("Field pribadi tidak dikenal.")

    encrypted = getattr(record, f"{field}_enc")
    original = getattr(record, field)

    # Data kosong memang tidak perlu mempunyai ciphertext.
    if encrypted is None:
        if original is not None:
            raise RuntimeError(
                f"Data {record.__tablename__}.{field} "
                "belum memiliki ciphertext."
            )

        return None

    value = decrypt_field(
        encrypted,
        field=f"{record.__tablename__}.{field}",
    )

    # Tanggal lahir disimpan sebagai teks ISO pada ciphertext,
    # tetapi aplikasi membutuhkan objek date.
    if model is CustomerProfile and field == "birth_date":
        value = date.fromisoformat(value)

    # Pemeriksaan sementara selama kolom plaintext masih tersedia.
    if original is not None and original != value:
        raise RuntimeError(
            f"Data {record.__tablename__}.{field} "
            "tidak cocok dengan ciphertext."
        )

    return value