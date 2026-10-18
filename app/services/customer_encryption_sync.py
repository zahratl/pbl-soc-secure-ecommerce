"""Sinkronisasi otomatis data pribadi ke kolom *_enc.

Listener ini bekerja ketika model SQLAlchemy disimpan melalui
Session.add(), perubahan atribut ORM, dan Session.commit().

Kolom plaintext sengaja belum dihapus pada tahap transisi.
"""

from datetime import date

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.customer_address import CustomerAddress
from app.models.customer_profile import CustomerProfile
from app.models.order import Order
from app.services.field_crypto import encrypt_field


ENCRYPTED_MODELS = {
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


def normalize_value(field, value):
    if value is None:
        return None

    if field == "birth_date":
        if not isinstance(value, date):
            raise ValueError("Tanggal lahir harus berupa date.")

        return value.isoformat()

    if not isinstance(value, str):
        raise ValueError(
            f"Nilai field {field} harus berupa teks."
        )

    return value


def encrypt_changed_customer_fields(session, flush_context, instances):
    records = list(session.new) + list(session.dirty)

    for record in records:
        fields = next(
            (
                names
                for model, names in ENCRYPTED_MODELS.items()
                if isinstance(record, model)
            ),
            None,
        )

        if fields is None:
            continue

        is_new = record in session.new
        state = inspect(record)

        for field in fields:
            if not is_new:
                history = state.attrs[field].history

                if not history.has_changes():
                    continue

            value = normalize_value(
                field,
                getattr(record, field),
            )

            encrypted_field = f"{field}_enc"

            if value is None:
                encrypted = None
            else:
                encrypted = encrypt_field(
                    value,
                    field=f"{record.__tablename__}.{field}",
                )

            setattr(record, encrypted_field, encrypted)


def register_customer_encryption_sync():
    if not event.contains(
        Session,
        "before_flush",
        encrypt_changed_customer_fields,
    ):
        event.listen(
            Session,
            "before_flush",
            encrypt_changed_customer_fields,
        )