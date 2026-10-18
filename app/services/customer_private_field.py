"""Descriptor ciphertext-only untuk atribut pribadi milik Customer/Order.

Versi kandidat: JANGAN diaktifkan sebelum skema *_enc-only terpasang.
"""
from datetime import date

from app.services.field_crypto import decrypt_field, encrypt_field


class CustomerPrivateField:
    def __init__(self, field_name, *, date_value=False, required=False):
        self.field_name = field_name
        self.date_value = date_value
        self.required = required

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        encrypted = getattr(instance, f'{self.field_name}_enc')
        if encrypted is None:
            if self.required:
                raise RuntimeError(f'Ciphertext wajib tidak tersedia: {instance.__tablename__}.{self.field_name}')
            return None
        result = decrypt_field(encrypted, field=f'{instance.__tablename__}.{self.field_name}')
        return date.fromisoformat(result) if self.date_value else result

    def __set__(self, instance, value):
        if value is None:
            if self.required:
                raise ValueError(f'Field wajib tidak boleh kosong: {instance.__tablename__}.{self.field_name}')
            setattr(instance, f'{self.field_name}_enc', None)
            return
        if self.date_value:
            if not isinstance(value, date):
                raise ValueError(f'{self.field_name} harus berupa date.')
            value = value.isoformat()
        elif not isinstance(value, str):
            raise ValueError(f'{self.field_name} harus berupa teks.')
        if self.required and not value.strip():
            raise ValueError(f'Field wajib tidak boleh kosong: {instance.__tablename__}.{self.field_name}')
        encrypted = encrypt_field(value, field=f'{instance.__tablename__}.{self.field_name}')
        setattr(instance, f'{self.field_name}_enc', encrypted)
