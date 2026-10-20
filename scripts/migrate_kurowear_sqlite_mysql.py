"""KURO WEAR: conservative SQLite snapshot -> MySQL transfer.

Default / --check does not write to any database.
--apply requires an explicit typed confirmation, inserts within a single
InnoDB transaction, checks all rows, and rolls back if anything differs.

Run from the root of the KURO WEAR project on Windows with Python 3:
    python scripts/migrate_kurowear_sqlite_mysql.py --check
    python scripts/migrate_kurowear_sqlite_mysql.py --apply

Keep Flask and any other writers stopped through migration and cutover.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
from datetime import datetime
from importlib import import_module
from pathlib import Path

from sqlalchemy import Boolean, DateTime, JSON, create_engine, func, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import null as sql_null

from app.extensions import db

SOURCE = Path("instance/backups/FINAL_pre_mysql_20261020_170902.db")
MYSQL_HOST = "10.10.12.10"
MYSQL_DATABASE = "db_toko_pakaian"
MYSQL_USER = "admin_toko"
MYSQL_CA = Path.home() / ".kurowear" / "certs" / "mysql-ca.pem"
EXPECTED_TABLES = 15
EXPECTED_ROWS = 77
EXPECTED_DATETIME_COLUMNS = 26


def load_models():
    models_dir = Path("app/models")
    if not models_dir.is_dir():
        raise RuntimeError("Jalankan script dari direktori root proyek KURO WEAR.")
    for file in sorted(models_dir.glob("*.py")):
        if file.stem != "__init__":
            import_module(f"app.models.{file.stem}")
    tables = list(db.metadata.sorted_tables)
    if len(tables) != EXPECTED_TABLES:
        raise RuntimeError(f"Model SQLAlchemy {len(tables)} tabel; diharapkan 15.")
    return tables


def adapt(col, original):
    """Produce (insert value, expected value) without changing ciphertext."""
    if original is None:
        # Preserve SQL NULL, not JSON literal `null`, in JSON columns.
        return (sql_null() if isinstance(col.type, JSON) else None), None
    if isinstance(col.type, JSON):
        parsed = json.loads(original)
        return parsed, parsed
    if isinstance(col.type, DateTime):
        parsed = datetime.fromisoformat(original)
        if parsed.utcoffset() is not None:
            raise ValueError("Datetime memiliki zona waktu; butuh kebijakan konversi eksplisit.")
        if not 1000 <= parsed.year <= 9999:
            raise ValueError("Tahun DATETIME di luar rentang MySQL.")
        return parsed, parsed
    if isinstance(col.type, Boolean):
        return bool(original), bool(original)
    return original, original


def prepare_source(tables):
    if not SOURCE.is_file() or not MYSQL_CA.is_file():
        raise RuntimeError("Snapshot final SQLite atau sertifikat CA tidak ditemukan.")
    uri = f"file:{SOURCE.resolve().as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("PRAGMA query_only=ON")
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Integritas snapshot SQLite bermasalah.")
        if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("Ada Foreign Key rusak pada snapshot SQLite.")
        sqlite_names = {
            row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if sqlite_names != {table.name for table in tables}:
            raise RuntimeError("Daftar tabel SQLite dan model SQLAlchemy berbeda.")

        data = {}
        expected = {}
        null_json_counts = {}
        total = 0
        for table in tables:
            sqlite_columns = {
                row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')
            }
            if sqlite_columns != {column.name for column in table.columns}:
                raise RuntimeError(f"Perbedaan nama kolom di {table.name}.")
            rows = source.execute(f'SELECT * FROM "{table.name}"').fetchall()
            inserts = []
            checks = []
            nulls = {}
            for row in rows:
                insert_values = {}
                check_values = {}
                for col in table.columns:
                    original = row[col.name]
                    value, check = adapt(col, original)
                    insert_values[col.name] = value
                    check_values[col.name] = check
                    if isinstance(col.type, JSON) and original is None:
                        nulls[col.name] = nulls.get(col.name, 0) + 1
                inserts.append(insert_values)
                checks.append(check_values)
            data[table.name] = inserts
            expected[table.name] = checks
            null_json_counts[table.name] = nulls
            total += len(rows)
            print(f"SUMBER {table.name}: {len(rows)} baris")
        if total != EXPECTED_ROWS:
            raise RuntimeError(f"Baris snapshot {total}, bukan {EXPECTED_ROWS}. Jangan lanjut otomatis.")
        return data, expected, null_json_counts, total
    finally:
        source.close()


def mysql_checks(conn, tables):
    if conn.execute(text("SELECT DATABASE()")).scalar_one() != MYSQL_DATABASE:
        raise RuntimeError("Database tujuan tidak sesuai.")
    cipher = conn.exec_driver_sql("SHOW SESSION STATUS LIKE 'Ssl_cipher'").fetchone()
    if not cipher or not cipher[1]:
        raise RuntimeError("TLS MySQL tidak aktif.")
    print(f"TLS MySQL: {cipher[1]}")
    names = set(inspect(conn).get_table_names())
    if names != {table.name for table in tables}:
        raise RuntimeError("Daftar tabel MySQL tidak sesuai dengan 15 model.")
    for table in tables:
        columns = {item["name"] for item in inspect(conn).get_columns(table.name)}
        if columns != {col.name for col in table.columns}:
            raise RuntimeError(f"Kolom MySQL berbeda pada tabel {table.name}.")

    precision = conn.execute(text("""
        SELECT COUNT(*) AS total, SUM(DATETIME_PRECISION = 6) AS six
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :schema AND DATA_TYPE = 'datetime'
    """), {"schema": MYSQL_DATABASE}).one()
    if precision[0] != EXPECTED_DATETIME_COLUMNS or precision[1] != EXPECTED_DATETIME_COLUMNS:
        raise RuntimeError("Tidak semua 26 kolom MySQL menggunakan DATETIME(6).")

    for table in tables:
        count = conn.execute(select(func.count()).select_from(table)).scalar_one()
        if count:
            raise RuntimeError(f"Tabel MySQL {table.name} sudah berisi {count} baris; batalkan.")


def verify_copy(conn, tables, expected, null_json_counts):
    """Check every value, key, and JSON SQL-NULL status before commit."""
    total = 0
    for table in tables:
        primary = [col.name for col in table.primary_key.columns]
        if not primary:
            raise RuntimeError(f"Tidak ada primary key: {table.name}")
        expected_by_key = {}
        for item in expected[table.name]:
            key = tuple(item[col] for col in primary)
            if key in expected_by_key:
                raise RuntimeError(f"Primary key ganda pada sumber {table.name}")
            expected_by_key[key] = item
        actual_items = conn.execute(select(table)).mappings().all()
        if len(actual_items) != len(expected_by_key):
            raise RuntimeError(f"Jumlah baris berbeda pada {table.name}.")
        for actual in actual_items:
            key = tuple(actual[col] for col in primary)
            check = expected_by_key.pop(key, None)
            if check is None:
                raise RuntimeError(f"Primary key MySQL tidak cocok pada {table.name}.")
            for column in table.columns:
                if actual[column.name] != check[column.name]:
                    raise RuntimeError(
                        f"Nilai tidak identik: {table.name}.{column.name} (nilai tidak ditampilkan)."
                    )
        if expected_by_key:
            raise RuntimeError(f"Ada primary key SQLite yang hilang dari {table.name}.")
        for col_name, num_null in null_json_counts[table.name].items():
            actual_null = conn.execute(
                select(func.count()).select_from(table).where(table.c[col_name].is_(None))
            ).scalar_one()
            if actual_null != num_null:
                raise RuntimeError(f"SQL NULL JSON berbeda pada {table.name}.{col_name}.")
        total += len(actual_items)
    if total != EXPECTED_ROWS:
        raise RuntimeError("Total baris setelah transfer tidak sesuai.")
    print(f"VERIFIKASI: seluruh {total} baris dan seluruh nilai kolom cocok.")


def main():
    parser = argparse.ArgumentParser(description="Migrasi database KURO WEAR secara terkontrol")
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--check", action="store_true", help="Hanya pemeriksaan, tidak menulis data")
    choices.add_argument("--apply", action="store_true", help="Transfer dalam satu transaksi + verifikasi")
    args = parser.parse_args()

    tables = load_models()
    inserts, expected, null_json_counts, total = prepare_source(tables)
    print(f"SNAPSHOT: {SOURCE} ({len(tables)} tabel, {total} baris)")
    password = getpass.getpass("Password MySQL admin_toko: ")
    engine = create_engine(
        URL.create("mysql+pymysql", username=MYSQL_USER, password=password,
                   host=MYSQL_HOST, port=3306, database=MYSQL_DATABASE,
                   query={"charset": "utf8mb4"}),
        poolclass=NullPool,
        connect_args={"ssl_ca": str(MYSQL_CA), "ssl_verify_cert": True,
                      "ssl_verify_identity": True, "connect_timeout": 10},
    )
    try:
        if args.check:
            with engine.connect() as conn:
                mysql_checks(conn, tables)
            print("HASIL: PEMERIKSAAN LULUS. BELUM ADA DATA YANG DIPINDAHKAN.")
            return
        with engine.connect() as conn:
            mysql_checks(conn, tables)
        print(f"AKAN MEMINDAHKAN {total} BARIS KE MYSQL. Flask harus tetap dihentikan.")
        if input("Untuk melanjutkan, ketik SALIN-77: ").strip() != "SALIN-77":
            print("DIBATALKAN, tidak ada data yang dipindahkan.")
            return
        # Every insert and comparison happens in one InnoDB transaction.
        # Any exception rolls back. Never disable foreign-key checks.
        with engine.begin() as conn:
            mysql_checks(conn, tables)  # prevent accidental rerun or race
            for table in tables:
                for item in inserts[table.name]:
                    conn.execute(table.insert().values(**item))
                print(f"TRANSFER {table.name}: {len(inserts[table.name])} baris")
            verify_copy(conn, tables, expected, null_json_counts)
        print("HASIL: TRANSFER BERHASIL, TRANSAKSI TELAH DI-COMMIT.")
        print("JANGAN langsung mengganti DATABASE_URL. Uji aplikasi dan rencana cutover dulu.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"GAGAL: {type(exc).__name__}: {exc}")
        print("Jika mode --apply gagal sebelum commit, transaksi INSERT dibatalkan.")
        raise SystemExit(1)
