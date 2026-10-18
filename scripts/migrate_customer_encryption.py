"""Isi kolom *_enc dari data lama tanpa menghapus kolom plaintext.

Jalankan dari root proyek:
    python -m scripts.migrate_customer_encryption
    python -m scripts.migrate_customer_encryption --apply
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

from app import create_app
from app.extensions import db
from app.services.field_crypto import decrypt_field, encrypt_field, get_cipher


TABLES = {
    "customer_profiles": (
        "user_id",
        ("phone", "birth_date")
    ),
    "customer_addresses": (
        "id",
        ("recipient_name", "phone", "full_address")
    ),
    "orders": (
        "id",
        (
            "customer_name",
            "customer_email",
            "recipient_name",
            "phone",
            "full_address"
        )
    ),
}


def verify_schema(connection):
    for table, (primary_key, fields) in TABLES.items():
        columns = {
            row[1]
            for row in connection.execute(
                f'PRAGMA table_info("{table}")'
            )
        }

        required = {primary_key}

        for field in fields:
            required.update((field, f"{field}_enc"))

        missing = required - columns

        if missing:
            raise RuntimeError(
                f"Struktur tabel {table} belum lengkap: "
                f"{', '.join(sorted(missing))}"
            )


def inspect_data(connection):
    pending = []
    encrypted_count = 0
    empty_count = 0

    for table, (primary_key, fields) in TABLES.items():
        selected = [primary_key]

        for field in fields:
            selected.extend((field, f"{field}_enc"))

        columns_sql = ", ".join(
            f'"{column}"'
            for column in selected
        )

        query = f'SELECT {columns_sql} FROM "{table}"'

        for row in connection.execute(query):
            for field in fields:
                original = row[field]
                encrypted = row[f"{field}_enc"]
                field_id = f"{table}.{field}"

                if original is not None and not isinstance(original, str):
                    raise RuntimeError(
                        f"Tipe data tidak sesuai pada {field_id}."
                    )

                if encrypted is not None:
                    decrypted = decrypt_field(
                        encrypted,
                        field=field_id
                    )

                    if original is not None and decrypted != original:
                        raise RuntimeError(
                            f"Hasil dekripsi tidak cocok pada {field_id}."
                        )

                    encrypted_count += 1

                elif original is not None:
                    pending.append(
                        (
                            table,
                            primary_key,
                            row[primary_key],
                            field,
                            original
                        )
                    )

                else:
                    empty_count += 1

    return pending, encrypted_count, empty_count


def make_backup(database_path):
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_path = backup_dir / (
        f"{database_path.stem}_before_data_encryption_"
        f"{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    )

    with sqlite3.connect(str(database_path)) as source:
        with sqlite3.connect(str(backup_path)) as destination:
            source.backup(destination)

            integrity = destination.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            if integrity != "ok":
                raise RuntimeError(
                    "Backup tidak lolos pemeriksaan integritas SQLite."
                )

    print("Backup baru:", backup_path)


def main():
    parser = argparse.ArgumentParser(
        description="Migrasi isi kolom AES-GCM KURO WEAR"
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Simpan ciphertext ke kolom *_enc"
    )

    args = parser.parse_args()

    # Memastikan kunci enkripsi dari .env valid.
    get_cipher()

    app = create_app()

    with app.app_context():
        if db.engine.dialect.name != "sqlite":
            raise RuntimeError(
                "Script ini hanya mendukung SQLite."
            )

        database_name = db.engine.url.database

        if not database_name or database_name == ":memory:":
            raise RuntimeError(
                "Database SQLite berbasis file diperlukan."
            )

        database_path = Path(database_name).resolve()

    if not database_path.is_file():
        raise RuntimeError(
            "File database tidak ditemukan."
        )

    connection = sqlite3.connect(
        str(database_path),
        timeout=30,
        isolation_level=None
    )

    connection.row_factory = sqlite3.Row

    try:
        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                "Database tidak lolos pemeriksaan integritas."
            )

        verify_schema(connection)

        pending, done, empty = inspect_data(connection)

        print("Database:", database_path)
        print("Nilai yang perlu dienkripsi:", len(pending))
        print(
            "Nilai yang sudah terenkripsi dan terverifikasi:",
            done
        )
        print("Nilai kosong / NULL:", empty)

        # Mode pemeriksaan: tidak mengubah data.
        if not args.apply:
            print(
                "Mode pemeriksaan: tidak ada data yang diubah."
            )
            return

        if not pending:
            print(
                "Tidak ada data baru yang perlu dienkripsi."
            )
            return

        # Backup dibuat sebelum perubahan database.
        make_backup(database_path)

        try:
            connection.execute("BEGIN IMMEDIATE")

            # Memeriksa kembali data setelah transaksi dimulai.
            pending, done, empty = inspect_data(connection)

            for table, primary_key, record_id, field, original in pending:
                field_id = f"{table}.{field}"

                encrypted = encrypt_field(
                    original,
                    field=field_id
                )

                # Memastikan hasil enkripsi dapat dikembalikan.
                if decrypt_field(
                    encrypted,
                    field=field_id
                ) != original:
                    raise RuntimeError(
                        f"Verifikasi enkripsi gagal pada {field_id}."
                    )

                result = connection.execute(
                    f'UPDATE "{table}" '
                    f'SET "{field}_enc" = ? '
                    f'WHERE "{primary_key}" = ? '
                    f'AND "{field}_enc" IS NULL',
                    (encrypted, record_id)
                )

                if result.rowcount != 1:
                    raise RuntimeError(
                        f"Gagal menyimpan ciphertext untuk {field_id}."
                    )

            # Verifikasi seluruh data setelah enkripsi.
            remaining, verified, _ = inspect_data(connection)

            if remaining:
                raise RuntimeError(
                    "Masih ada data yang belum berhasil dienkripsi."
                )

            integrity = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            if integrity != "ok":
                raise RuntimeError(
                    "Pemeriksaan integritas SQLite gagal."
                )

            connection.execute("COMMIT")

            print(
                "Enkripsi data selesai. Nilai baru yang disimpan:",
                len(pending)
            )
            print(
                "Total nilai terenkripsi yang berhasil diverifikasi:",
                verified
            )
            print("Integritas database: ok")
            print("Kolom plaintext belum dihapus.")

        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")

            raise

    finally:
        connection.close()


if __name__ == "__main__":
    main()