import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from app import create_app
from app.extensions import db


ENCRYPTED_FIELDS = {
    "customer_profiles": ("phone", "birth_date"),
    "customer_addresses": ("recipient_name", "phone", "full_address"),
    "orders": ("customer_name", "customer_email", "recipient_name", "phone", "full_address"),
}


def table_columns(connection, table):
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row[1] for row in rows}


def inspect_schema(connection):
    missing = []

    for table, fields in ENCRYPTED_FIELDS.items():
        columns = table_columns(connection, table)

        if not columns:
            raise RuntimeError(f"Tabel {table} tidak ditemukan.")

        for field in fields:
            if field not in columns:
                raise RuntimeError(f"Kolom {table}.{field} tidak ditemukan.")

            encrypted_column = f"{field}_enc"

            if encrypted_column not in columns:
                missing.append((table, encrypted_column))

    return missing


def backup_database(database_path):
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{database_path.stem}_before_encryption_{datetime.now():%Y%m%d_%H%M%S}.db"
    backup_path = backup_dir / filename

    with sqlite3.connect(str(database_path)) as source:
        with sqlite3.connect(str(backup_path)) as destination:
            source.backup(destination)

            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]

            if integrity != "ok":
                raise RuntimeError("Backup gagal melewati pemeriksaan integritas.")

    print("Backup berhasil dibuat:", backup_path)
    return backup_path


def main():
    parser = argparse.ArgumentParser(description="Persiapan kolom enkripsi KURO WEAR")
    parser.add_argument("--apply", action="store_true", help="Terapkan perubahan skema database")
    args = parser.parse_args()

    app = create_app()

    with app.app_context():
        if db.engine.dialect.name != "sqlite":
            raise RuntimeError("Script ini hanya diperuntukkan bagi SQLite.")

        database_path = Path(db.engine.url.database).resolve()

    if not database_path.is_file():
        raise RuntimeError("File database SQLite tidak ditemukan.")

    connection = sqlite3.connect(str(database_path), timeout=30, isolation_level=None)

    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

        if integrity != "ok":
            raise RuntimeError("Database gagal melewati pemeriksaan integritas.")

        missing = inspect_schema(connection)

        print("Database:", database_path)
        print("Jumlah kolom baru:", len(missing))

        for table, column in missing:
            print(f"  + {table}.{column}")

        if not missing:
            print("Semua kolom enkripsi sudah tersedia.")
            return

        if not args.apply:
            print("Mode pemeriksaan: database belum diubah.")
            print("Jalankan ulang dengan --apply untuk menambahkan kolom.")
            return

        backup_database(database_path)

        try:
            connection.execute("BEGIN IMMEDIATE")

            for table, column in missing:
                connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" TEXT')

            connection.execute("COMMIT")

        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

        if inspect_schema(connection):
            raise RuntimeError("Beberapa kolom belum berhasil dibuat.")

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

        if integrity != "ok":
            raise RuntimeError("Database gagal pemeriksaan integritas setelah migrasi.")

        print("Migrasi skema berhasil.")
        print("Integritas database: ok")
        print("Data lama tidak dihapus.")

    finally:
        connection.close()


if __name__ == "__main__":
    main()