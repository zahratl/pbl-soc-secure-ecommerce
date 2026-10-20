"""KURO WEAR ciphertext-only cutover (maintenance window).

python -m scripts.stage10_cutover                 # READ-ONLY preflight
python -m scripts.stage10_cutover --apply --confirm YES-I-HAVE-BACKUP
python -m scripts.stage10_cutover --verify        # post-cutover READ-ONLY
python -m scripts.stage10_cutover --restore <cutover-backup-directory>

STOP Flask and SQLite tools before --apply or --restore.
This tool NEVER prints private data or encryption keys.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'instance' / 'ecommerce_local.db'
PREVIEW = ROOT / 'kuro_ciphertext_preview' / 'app'
BACKUPS = ROOT / 'instance' / 'backups'
load_dotenv(ROOT / '.env')

from app.services.field_crypto import decrypt_field, get_cipher

TABLES = {
    'customer_profiles': ('user_id', ('phone', 'birth_date')),
    'customer_addresses': ('id', ('recipient_name', 'phone', 'full_address')),
    'orders': ('id', ('customer_name', 'customer_email', 'recipient_name', 'phone', 'full_address')),
}
REPLACEMENTS = {
    'app/models/customer_profile.py': 'models/customer_profile.py',
    'app/models/customer_address.py': 'models/customer_address.py',
    'app/models/order.py': 'models/order.py',
    'app/services/customer_encryption_reader.py': 'services/customer_encryption_reader.py',
    'app/services/customer_private_field.py': 'services/customer_private_field.py',
}
INIT_PATH = 'app/__init__.py'
DELETE_INIT_IMPORT = 'from app.services.customer_encryption_sync import register_customer_encryption_sync\n'
DELETE_INIT_CALL = '    register_customer_encryption_sync()\n'


def check(value, reason):
    if not value:
        raise RuntimeError(reason)


def quote(name):
    check(name.replace('_', '').isalnum(), 'Identifier tidak valid')
    return f'"{name}"'


def database_connection(path, *, readonly):
    if readonly:
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    else:
        connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=5000')
    return connection


def database_integrity(connection):
    check(connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'integrity_check gagal')
    check(connection.execute('PRAGMA foreign_key_check').fetchone() is None, 'foreign_key_check gagal')


def verification(connection, *, before):
    totals = {}
    checked_count = 0
    empty_count = 0
    for table, (pk, fields) in TABLES.items():
        columns = {item[1] for item in connection.execute(f'PRAGMA table_info({quote(table)})')}
        expected = {pk} | {f'{field}_enc' for field in fields}
        if before:
            expected |= set(fields)
        check(expected <= columns, f'{table}: field wajib belum tersedia: {expected - columns}')
        if not before:
            check(not any(field in columns for field in fields), f'{table}: plaintext masih ada')
        names = [pk]
        for field in fields:
            if before:
                names.append(field)
            names.append(f'{field}_enc')
        rows = connection.execute(f'SELECT {", ".join(quote(name) for name in names)} FROM {quote(table)}')
        totals[table] = 0
        for row in rows:
            totals[table] += 1
            for field in fields:
                data = row[f'{field}_enc']
                if data is None:
                    check(table == 'customer_profiles', f'{table}.{field}: ciphertext wajib kosong')
                    if before:
                        check(row[field] is None, f'{table}.{field}: plaintext belum terenkripsi')
                    empty_count += 1
                else:
                    value = decrypt_field(data, field=f'{table}.{field}')
                    if before:
                        original = row[field]
                        if field == 'birth_date' and original is not None:
                            original = str(original)
                        check(original == value, f'{table}.{field}: plaintext dan ciphertext berbeda')
                    checked_count += 1
    return totals, checked_count, empty_count


def git_guard():
    result = subprocess.run(['git', 'branch', '--show-current'], cwd=ROOT, capture_output=True, text=True)
    check(result.returncode == 0 and result.stdout.strip() == 'feature/customer-data-encryption',
          'Harus di branch feature/customer-data-encryption')
    result = subprocess.run(['git', 'diff', '--name-only'], cwd=ROOT, capture_output=True, text=True)
    check(result.returncode == 0 and not result.stdout.strip(), 'Ada file tracked yang belum di-commit')
    result = subprocess.run(['git', 'diff', '--cached', '--name-only'], cwd=ROOT, capture_output=True, text=True)
    check(result.returncode == 0 and not result.stdout.strip(), 'Ada file staged yang belum di-commit')
    result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    check(result.returncode == 0, 'Git HEAD tidak dapat dibaca')
    return result.stdout.strip()


def source_files():
    changes = {}
    for destination, candidate in REPLACEMENTS.items():
        if destination != 'app/services/customer_private_field.py':
            check((ROOT / destination).is_file(), f'File aplikasi tidak ada: {destination}')
        location = PREVIEW / candidate
        check(location.is_file(), f'Kandidat tidak ada: {location}')
        content = location.read_bytes()
        check(content, f'Kandidat kosong: {location}')
        ast.parse(content.decode('utf-8'), filename=str(location))
        changes[destination] = content

    init_file = ROOT / INIT_PATH
    text = init_file.read_text(encoding='utf-8')
    for old in (DELETE_INIT_IMPORT, DELETE_INIT_CALL):
        check(text.count(old) == 1, 'app/__init__.py: pola listener tidak cocok; JANGAN lanjutkan')
        text = text.replace(old, '', 1)
    ast.parse(text, filename=INIT_PATH)
    changes[INIT_PATH] = text.encode('utf-8')

    # Pastikan helper lama memang reader transisi dan 3 model lama menggunakan kolom langsung.
    old_reader = (ROOT / 'app/services/customer_encryption_reader.py').read_text(encoding='utf-8')
    check('original = getattr(record, field)' in old_reader, 'Reader bukan versi transisi yang diharapkan')
    for model, field in (('customer_profile', 'phone'), ('customer_address', 'recipient_name'), ('order', 'customer_name')):
        text = (ROOT / f'app/models/{model}.py').read_text(encoding='utf-8')
        check(f'{field} = db.Column' in text, f'{model}: kolom lama tidak ditemukan')
    return changes


def rollback_sqlite_filenames():
    suffixes = ('-wal', '-shm', '-journal')
    return [DB.parent / (DB.name + suffix) for suffix in suffixes]


def quiet_database_required():
    check(DB.is_file(), f'Database tidak ditemukan: {DB}')
    extra = [str(p) for p in rollback_sqlite_filenames() if p.exists()]
    check(not extra, 'Database berpotensi masih aktif (WAL/journal): ' + ', '.join(extra) + '. Hentikan server/DB Browser dahulu.')


def migrate_only(connection):
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('BEGIN EXCLUSIVE')
    try:
        before_counts, _, _ = verification(connection, before=True)
        for table, (_, fields) in TABLES.items():
            for field in fields:
                connection.execute(f'ALTER TABLE {quote(table)} DROP COLUMN {quote(field)}')
        after_counts, _, _ = verification(connection, before=False)
        check(before_counts == after_counts, 'Jumlah baris berubah')
        database_integrity(connection)
        connection.execute('COMMIT')
        return after_counts
    except Exception:
        connection.execute('ROLLBACK')
        raise


def preflight(*, full=True):
    get_cipher()  # validasi key tanpa menampilkannya
    git_commit = git_guard()
    changes = source_files()
    quiet_database_required()
    with closing(database_connection(DB, readonly=True)) as source:
        database_integrity(source)
        with closing(sqlite3.connect(':memory:', isolation_level=None)) as memory:
            memory.row_factory = sqlite3.Row
            source.backup(memory)
            before, count, empty = verification(memory, before=True)
            after = migrate_only(memory)
            check(before == after, 'Jumlah baris berubah dalam simulasi')
    print('Branch dan checkpoint Git:', git_commit)
    print('Jumlah baris:', before)
    print('Ciphertext terverifikasi:', count, '| Nilai kosong:', empty)
    print('Simulasi SQLite + foreign key + integritas: OK')
    print('Enam file kandidat lulus parsing: OK')
    if full:
        preview_script = ROOT / 'scripts/preview_ciphertext_only.py'
        check(preview_script.is_file(), 'Script simulasi tahap sebelumnya tidak ada')
        result = subprocess.run([sys.executable, '-m', 'scripts.preview_ciphertext_only'], cwd=ROOT)
        check(result.returncode == 0, 'Pengujian model kandidat gagal')
        print('Simulasi ORM kandidat: OK')
    print('PRE-FLIGHT SELESAI. KODE APLIKASI & DATABASE ASLI TIDAK DIUBAH.')
    return changes, before


def write_bytes_atomically(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.new', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as dest:
            dest.write(payload)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def new_backup(paths):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    folder = BACKUPS / ('ciphertext_only_cutover_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'database_before.db').touch(exist_ok=False)
    # Important: SQL Server/WAL-safe backup API instead of copying raw database bytes.
    with closing(database_connection(DB, readonly=True)) as source, closing(sqlite3.connect(folder / 'database_before.db')) as destination:
        source.backup(destination)
        database_integrity(destination)
    with zipfile.ZipFile(folder / 'code_before.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in paths:
            file = ROOT / relative
            if file.exists():
                archive.write(file, arcname=relative)
    manifest = {'code_files': list(paths), 'original_db_sha256': hashlib.sha256((folder / 'database_before.db').read_bytes()).hexdigest()}
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return folder


def restore_files(folder):
    folder = folder.resolve()
    check(folder.parent == BACKUPS.resolve(), 'Restore hanya boleh dari direktori instance/backups')
    check(folder.name.startswith('ciphertext_only_cutover_'), 'Nama backup cutover tidak dikenal')
    archive_path = folder / 'code_before.zip'
    db_backup = folder / 'database_before.db'
    check(archive_path.is_file() and db_backup.is_file(), 'File backup pemulihan tidak lengkap')
    with closing(database_connection(db_backup, readonly=True)) as check_db:
        database_integrity(check_db)
    quiet_database_required()
    with zipfile.ZipFile(archive_path) as archive:
        check(all(name.startswith(('app/models/', 'app/services/', 'app/__init__.py')) for name in archive.namelist()), 'ZIP backup tidak valid')
        # Pulihkan file sebelum database, hanya saat aplikasi sudah offline.
        for entry in archive.infolist():
            check(not entry.is_dir(), 'ZIP backup tidak valid')
            write_bytes_atomically(ROOT / entry.filename, archive.read(entry.filename))
    # File yang tidak ada sebelum cutover harus dihapus (khusus helper descriptor baru).
    descriptor = ROOT / 'app/services/customer_private_field.py'
    with zipfile.ZipFile(archive_path) as archive:
        if 'app/services/customer_private_field.py' not in archive.namelist() and descriptor.exists():
            descriptor.unlink()
    with closing(database_connection(db_backup, readonly=True)) as src:
        with closing(sqlite3.connect(str(DB))) as dest:
            src.backup(dest)
    print('Pemulihan selesai. Periksa python -m flask routes dan database sebelum menyalakan server.')


def apply(changes, original_rows):
    quiet_database_required()
    folder = new_backup(changes)
    print('Backup untuk rollback:', folder)
    migrated = folder / 'database_after.db'
    try:
        with closing(database_connection(DB, readonly=True)) as src, closing(sqlite3.connect(migrated)) as dst:
            src.backup(dst)
        with closing(database_connection(migrated, readonly=False)) as conn:
            rows = migrate_only(conn)
            check(rows == original_rows, 'Jumlah baris berbeda')
        with closing(database_connection(migrated, readonly=True)) as conn:
            database_integrity(conn)
            verification(conn, before=False)
        # Application files and database swapped only after cloned migrated DB passes.
        for relative, content in changes.items():
            write_bytes_atomically(ROOT / relative, content)
        os.replace(migrated, DB)
        # subprocess loads new app and new database, DB now ciphertext-only.
        check(subprocess.run([sys.executable, '-m', 'flask', 'routes'], cwd=ROOT).returncode == 0,
              'Flask tidak bisa dimuat sesudah cutover')
        with closing(database_connection(DB, readonly=True)) as conn:
            database_integrity(conn)
            check(verification(conn, before=False)[0] == original_rows, 'Verifikasi hasil akhir gagal')
        print('CUTOVER SELESAI: plaintext 10 kolom dihapus dari database aktif.')
        print('Backup pemulihan:', folder)
        print('JANGAN hapus backup. Uji halaman Flask, Checkout, Admin dan Midtrans Sandbox.')
    except Exception:
        print('CUTOVER GAGAL, mencoba memulihkan code dan database dari backup ...')
        try:
            restore_files(folder)
            print('Pemulihan otomatis selesai. Lakukan pemeriksaan sebelum menjalankan Flask.')
        except Exception as recovery_error:
            print('PEMULIHAN OTOMATIS GAGAL:', recovery_error)
            print('SIMPAN backup dan lakukan pemulihan manual sebelum menjalankan server.')
        raise


def verify_postcutover():
    get_cipher()
    with closing(database_connection(DB, readonly=True)) as conn:
        database_integrity(conn)
        totals, count, empty = verification(conn, before=False)
    print('Jumlah baris:', totals)
    print('Ciphertext valid:', count, '| Kosong:', empty)
    print('Sepuluh kolom plaintext tidak lagi ada: OK')
    print('Integritas SQLite: OK')


def main():
    parser = argparse.ArgumentParser(description='KURO WEAR: ciphertext-only cutover')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true', help='Migrasi permanen (JANGAN sebelum diperintahkan)')
    group.add_argument('--verify', action='store_true', help='Baca database setelah migrasi')
    group.add_argument('--restore', metavar='BACKUP_DIR', help='Pulihkan dari folder backup cutover')
    parser.add_argument('--confirm', default='', help='Konfirmasi eksplisit --apply')
    args = parser.parse_args()
    try:
        if args.verify:
            verify_postcutover()
        elif args.restore:
            restore_files(Path(args.restore))
        elif args.apply:
            check(args.confirm == 'YES-I-HAVE-BACKUP', 'Konfirmasi --apply tidak sesuai')
            changes, counts = preflight(full=True)
            apply(changes, counts)
        else:
            preflight(full=True)
    except Exception as exc:
        print('GAGAL:', exc, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
