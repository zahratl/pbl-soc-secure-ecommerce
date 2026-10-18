#Alamat disimpan di tabel khusus agar lebih rapi dan tidak mengganggu foto profil, gender, atau tanggal lahir.
from datetime import datetime, timezone

from app.extensions import db

def utc_now():
    return datetime.now(timezone.utc)
class CustomerAddress(db.Model):
    __tablename__ = "customer_addresses"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False, index=True)

    recipient_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(25), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    postal_code = db.Column(db.String(5))
    full_address = db.Column(db.String(500), nullable=False)

    recipient_name_enc = db.Column(db.Text, nullable=True)
    phone_enc = db.Column(db.Text, nullable=True)
    full_address_enc = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    user = db.relationship(
        'User',
        backref=db.backref('default_shipping_address', uselist=False)
    )

    def as_dict(self):
        from app.services.customer_encryption_reader import read_customer_private

        return {
            'recipient_name': read_customer_private(self, 'recipient_name'),
            'phone': read_customer_private(self, 'phone'),
            'city': self.city,
            'postal_code': self.postal_code or '',
            'full_address': read_customer_private(self, 'full_address')
        }


def validate_address(data):
    values = {}

    for key in ('recipient_name', 'phone', 'city', 'postal_code', 'full_address'):
        raw = data.get(key, '')

        if raw is None:
            raw = ''

        if not isinstance(raw, str):
            raise ValueError('Format alamat tidak valid.')

        values[key] = raw.strip()

    if not 2 <= len(values['recipient_name']) <= 100:
        raise ValueError('Nama penerima harus terdiri dari 2 sampai 100 karakter.')

    if not 5 <= len(values['phone']) <= 25 or any(char not in '0123456789+ -()' for char in values['phone']):
        raise ValueError('Nomor telepon penerima tidak valid.')

    if not 2 <= len(values['city']) <= 100 or not 5 <= len(values['full_address']) <= 500:
        raise ValueError('Lengkapi kota dan alamat pengiriman dengan benar.')

    if values['postal_code'] and (len(values['postal_code']) != 5 or not values['postal_code'].isdigit()):
        raise ValueError('Kode pos harus terdiri dari 5 angka.')

    return values