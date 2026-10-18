
"""Audit pra-migrasi KURO WEAR. HANYA BACA database asli.

Jalankan dari root proyek: python -m scripts.audit_ciphertext_only
Semua ALTER TABLE hanya dijalankan pada salinan SQLite di memori.
"""

import sqlite3
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

from app.services.field_crypto import decrypt_field, get_cipher

TARGETS = {
    'customer_profiles': ('user_id', ('phone', 'birth_date')),
    'customer_addresses': ('id', ('recipient_name', 'phone', 'full_address')),
    'orders': ('id', ('customer_name', 'customer_email', 'recipient_name', 'phone', 'full_address')),
}


def quoted(identifier):
    if not identifier.replace('_', '').isalnum():
        raise ValueError('Nama kolom tidak valid.')
    return '"' + identifier + '"'


def checked(connection, label):
    integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    if integrity != 'ok':
        raise RuntimeError(f'{label}: integrity_check gagal: {integrity}')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise RuntimeError(f'{label}: foreign_key_check gagal.')


def verify_data(connection, require_plaintext):
    encrypted_count = 0
    empty_count = 0
    counts = {}

    for table, (primary_key, fields) in TARGETS.items():
        columns = {item[1] for item in connection.execute(f'PRAGMA table_info({quoted(table)})')}
        required = {primary_key} | {f'{field}_enc' for field in fields}
        if require_plaintext:
            required |= set(fields)
        missing = required - columns
        if missing:
            raise RuntimeError(f'{table}: kolom hilang: {", ".join(sorted(missing))}')

        selected = [primary_key]
        for field in fields:
            if require_plaintext:
                selected.append(field)
            selected.append(f'{field}_enc')

        sql_columns = ', '.join(quoted(field) for field in selected)
        rows = connection.execute(f'SELECT {sql_columns} FROM {quoted(table)}')
        counts[table] = 0

        for row in rows:
            counts[table] += 1
            for field in fields:
                ciphertext = row[f'{field}_enc']
                original = row[field] if require_plaintext else None

                if ciphertext is None:
                    if table != 'customer_profiles' or (require_plaintext and original is not None):
                        raise RuntimeError(f'{table}.{field}: ciphertext wajib belum tersedia.')
                    empty_count += 1
                    continue

                clear = decrypt_field(ciphertext, field=f'{table}.{field}')
                if require_plaintext and clear != original:
                    raise RuntimeError(f'{table}.{field}: isi plaintext dan ciphertext tidak cocok.')
                encrypted_count += 1

    return counts, encrypted_count, empty_count


def main():
    database = (ROOT / 'instance' / 'ecommerce_local.db').resolve()
    if not database.is_file():
        raise RuntimeError('Database instance/ecommerce_local.db tidak ditemukan.')

    get_cipher()
    original = sqlite3.connect(str(database), timeout=30)
    original.row_factory = sqlite3.Row
    original.execute('PRAGMA query_only = ON')
    snapshot = sqlite3.connect(':memory:')
    snapshot.row_factory = sqlite3.Row

    try:
        checked(original, 'Database asli')
        original.backup(snapshot)
        snapshot.execute('PRAGMA foreign_keys = ON')
        checked(snapshot, 'Salinan memori')

        before_counts, encrypted_count, empty_count = verify_data(snapshot, True)
        print('Database:', database)
        print('Jumlah baris per tabel:', before_counts)
        print('Ciphertext terverifikasi:', encrypted_count)
        print('Field opsional kosong:', empty_count)
        print('Integritas dan foreign key sebelum migrasi: OK')

        print('Menguji DROP COLUMN hanya di salinan memori ...')
        for table, (_, fields) in TARGETS.items():
            for field in fields:
                snapshot.execute(
                    f'ALTER TABLE {quoted(table)} DROP COLUMN {quoted(field)}'
                )

        after_counts, _, _ = verify_data(snapshot, False)
        if after_counts != before_counts:
            raise RuntimeError('Jumlah baris berubah dalam simulasi.')
        for table, (_, fields) in TARGETS.items():
            remaining = {c[1] for c in snapshot.execute(f'PRAGMA table_info({quoted(table)})')}
            if any(field in remaining for field in fields):
                raise RuntimeError(f'{table}: masih ada kolom plaintext setelah simulasi.')

        checked(snapshot, 'Salinan setelah DROP COLUMN')
        print('Simulasi hapus 10 kolom plaintext: OK')
        print('Jumlah baris tetap dan ciphertext dapat didekripsi: OK')
        print('Integritas dan foreign key setelah simulasi: OK')
        print('AMAN: database asli TIDAK DIUBAH.')
        print('CATATAN: jangan lakukan migrasi asli sebelum kode model/reader/writer siap.')

    finally:
        snapshot.close()
        original.close()


if __name__ == '__main__':
    main()
