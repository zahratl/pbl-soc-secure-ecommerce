"""Akses terkontrol untuk data pribadi pada model ciphertext-only."""

from app.models.customer_address import CustomerAddress
from app.models.customer_profile import CustomerProfile
from app.models.order import Order

PRIVATE_FIELDS = {
    CustomerProfile: frozenset(("phone", "birth_date")),
    CustomerAddress: frozenset(("recipient_name", "phone", "full_address")),
    Order: frozenset(("customer_name", "customer_email", "recipient_name", "phone", "full_address")),
}

def read_customer_private(record, field):
    fields = PRIVATE_FIELDS.get(type(record))
    if fields is None or field not in fields:
        raise ValueError("Field pribadi tidak dikenal.")
    return getattr(record, field)
