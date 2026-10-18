
"""Integrasi pembacaan data terenkripsi untuk KURO WEAR.

Jalankan dari root proyek:
    python -m scripts.integrate_encrypted_reads
    python -m scripts.integrate_encrypted_reads --apply
"""

import argparse
import ast
import re
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FILES = {
    "init": "app/__init__.py",
    "customer": "app/blueprints/customer/routes.py",
    "cart": "app/blueprints/cart/routes.py",
    "admin": "app/blueprints/admin/routes.py",
    "profile": "app/templates/customer/profile.html",
    "detail": "app/templates/admin/order_detail.html",
}


def swap(text, old, new, label):
    count = text.count(old)

    if count != 1:
        raise RuntimeError(
            f"{label}: teks yang diharapkan muncul {count} kali, "
            "bukan 1. Tidak ada file diubah."
        )

    return text.replace(old, new, 1)


def replace_fields(text, substitutions, label):
    for old, new in substitutions.items():
        pattern = r"\b" + re.escape(old) + r"\b"
        text, hits = re.subn(pattern, new, text)

        if hits < 1:
            raise RuntimeError(
                f"{label}: ekspresi {old} tidak ditemukan."
            )

    return text


def build_edits():
    original = {}
    changed = {}

    for key, relative in FILES.items():
        path = ROOT / relative

        if not path.is_file():
            raise RuntimeError(
                f"File tidak ditemukan: {relative}"
            )

        original[key] = path.read_text(encoding="utf-8")

    # 1. Registrasi fungsi dekripsi untuk template Jinja.
    text = original["init"]

    text = swap(
        text,
        "from flask import Flask, abort, app, redirect, request, url_for",
        "from flask import Flask, abort, redirect, request, session, url_for",
        "app/__init__.py",
    )

    anchor = (
        "from app.services.audit_logger "
        "import log_security_event, setup_audit_logger"
    )

    text = swap(
        text,
        anchor,
        anchor + "\nfrom app.services.customer_encryption_reader "
        "import read_customer_private",
        "app/__init__.py",
    )

    text = swap(
        text,
        "    db.init_app(app)\n",
        "    db.init_app(app)\n"
        '    app.add_template_global(read_customer_private, '
        'name="private_field")\n',
        "app/__init__.py",
    )

    text = swap(
        text,
        "    def protect_application_routes():\n",
        "    def protect_application_routes():\n"
        '        session.pop("shipping_address", None)\n'
        '        session.pop("shipping_address_user_id", None)\n',
        "app/__init__.py",
    )

    if "register_customer_encryption_sync()" not in text:
        raise RuntimeError(
            "Listener enkripsi belum terdaftar pada app/__init__.py."
        )

    changed["init"] = text

    # 2. Pembacaan nomor HP pada route Profil Customer.
    text = original["customer"]
    anchor = "from app.models.shipment import OrderShipment"

    text = swap(
        text,
        anchor,
        anchor + "\nfrom app.services.customer_encryption_reader "
        "import read_customer_private",
        "customer/routes.py",
    )

    text = swap(
        text,
        "if not profile.phone:",
        "if not read_customer_private(profile, 'phone'):",
        "customer/routes.py",
    )

    changed["customer"] = text

    # 3. Checkout membaca alamat terenkripsi.
    text = original["cart"]
    anchor = "from app.models.wishlist import WishlistItem"

    text = swap(
        text,
        anchor,
        anchor + "\nfrom app.services.customer_encryption_reader "
        "import read_customer_private",
        "cart/routes.py",
    )

    start = text.find("def current_address():\n")
    end = text.find(
        "\ndef pending_fingerprint(",
        start,
    )

    if start < 0 or end < 0:
        raise RuntimeError(
            "cart/routes.py: fungsi current_address tidak ditemukan."
        )

    new_block = """def current_address():
    saved = CustomerAddress.query.filter_by(
        user_id=current_user.id
    ).first()

    if saved:
        return {
            'recipient_name': read_customer_private(
                saved, 'recipient_name'
            ),
            'phone': read_customer_private(saved, 'phone'),
            'city': saved.city,
            'postal_code': saved.postal_code or '',
            'full_address': read_customer_private(
                saved, 'full_address'
            ),
        }

    last_order = Order.query.filter_by(
        user_id=current_user.id
    ).order_by(Order.id.desc()).first()

    if last_order:
        return {
            'recipient_name': read_customer_private(
                last_order, 'recipient_name'
            ),
            'phone': read_customer_private(last_order, 'phone'),
            'city': last_order.city,
            'postal_code': '',
            'full_address': read_customer_private(
                last_order, 'full_address'
            ),
        }

    return None

"""

    text = text[:start] + new_block + text[end:]

    text = swap(
        text,
        "if not profile.phone:",
        "if not read_customer_private(profile, 'phone'):",
        "cart/routes.py",
    )

    text = swap(
        text,
        "    session['shipping_address'] = saved.as_dict()\n"
        "    session['shipping_address_user_id'] = current_user.id",
        "    session.pop('shipping_address', None)\n"
        "    session.pop('shipping_address_user_id', None)",
        "cart/routes.py",
    )

    text = swap(
        text,
        "'address': saved.as_dict()",
        "'address': current_address()",
        "cart/routes.py",
    )

    changed["cart"] = text

    # 4. Pencarian, daftar, dan ekspor customer Admin.
    text = original["admin"]
    anchor = (
        "from app.services.audit_logger import log_security_event"
    )

    text = swap(
        text,
        anchor,
        anchor + "\nfrom app.services.customer_encryption_reader "
        "import read_customer_private",
        "admin/routes.py",
    )

    text = swap(
        text,
        "        if search.casefold() not in "
        "f'{order.order_number} {order.customer_name} "
        "{order.customer_email}'.casefold():\n"
        "            continue",
        "        order_name = read_customer_private("
        "order, 'customer_name') or ''\n"
        "        order_email = read_customer_private("
        "order, 'customer_email') or ''\n"
        "        if search.casefold() not in "
        "f'{order.order_number} {order_name} "
        "{order_email}'.casefold():\n"
        "            continue",
        "admin/routes.py",
    )

    text = swap(
        text,
        "            'customer_name': order.customer_name,",
        "            'customer_name': order_name,",
        "admin/routes.py",
    )

    text = swap(
        text,
        "            'customer_email': order.customer_email,",
        "            'customer_email': order_email,",
        "admin/routes.py",
    )

    text = swap(
        text,
        '        if search.casefold() not in '
        'f\'{payment.midtrans_transaction_id or ""} '
        '{order.order_number} {order.customer_email}\'.casefold():',
        "        order_email = read_customer_private("
        "order, 'customer_email') or ''\n"
        '        if search.casefold() not in '
        'f\'{payment.midtrans_transaction_id or ""} '
        '{order.order_number} {order_email}\'.casefold():',
        "admin/routes.py",
    )

    text = swap(
        text,
        "            'phone': profile.phone if profile else None,",
        "            'phone': read_customer_private("
        "profile, 'phone') if profile else None,",
        "admin/routes.py",
    )

    text = swap(
        text,
        "            'birth_date': profile.birth_date "
        "if profile else None,",
        "            'birth_date': read_customer_private("
        "profile, 'birth_date') if profile else None,",
        "admin/routes.py",
    )

    changed["admin"] = text

    # 5. Profil Customer: ganti akses plaintext di Jinja.
    text = original["profile"]

    text = replace_fields(
        text,
        {
            "profile.phone":
                "private_field(profile, 'phone')",
            "profile.birth_date":
                "private_field(profile, 'birth_date')",
            "address.recipient_name":
                "private_field(address, 'recipient_name')",
            "address.phone":
                "private_field(address, 'phone')",
            "address.full_address":
                "private_field(address, 'full_address')",
            "fallback_order.recipient_name":
                "private_field(fallback_order, 'recipient_name')",
            "fallback_order.phone":
                "private_field(fallback_order, 'phone')",
            "fallback_order.full_address":
                "private_field(fallback_order, 'full_address')",
            "order.recipient_name":
                "private_field(order, 'recipient_name')",
            "order.phone":
                "private_field(order, 'phone')",
            "order.full_address":
                "private_field(order, 'full_address')",
        },
        "profile.html",
    )

    changed["profile"] = text

    # 6. Detail Pesanan Admin: ganti akses plaintext.
    text = original["detail"]

    text = replace_fields(
        text,
        {
            "order.customer_name":
                "private_field(order, 'customer_name')",
            "order.customer_email":
                "private_field(order, 'customer_email')",
            "order.recipient_name":
                "private_field(order, 'recipient_name')",
            "order.phone":
                "private_field(order, 'phone')",
            "order.full_address":
                "private_field(order, 'full_address')",
        },
        "order_detail.html",
    )

    changed["detail"] = text

    # 7. Validasi sintaks sebelum file diubah.
    for key, filename in FILES.items():
        if filename.endswith(".py"):
            ast.parse(changed[key], filename=filename)

    from jinja2 import Environment

    environment = Environment()

    for key in ("profile", "detail"):
        environment.parse(changed[key])

    return original, changed


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Terapkan perubahan setelah pemeriksaan.",
    )

    args = parser.parse_args()

    original, changed = build_edits()

    print("Pemeriksaan sintaks dan pola penggantian: OK")
    print("File yang akan diperbarui:")

    for relative in FILES.values():
        print(" -", relative)

    if not args.apply:
        print("Mode pemeriksaan: belum ada file diubah.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup = (
        ROOT
        / "instance"
        / "backups"
        / f"code_before_encrypted_reads_{stamp}.zip"
    )

    backup.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(
        backup, "x", zipfile.ZIP_DEFLATED
    ) as archive:
        for relative in FILES.values():
            archive.write(
                ROOT / relative,
                arcname=relative,
            )

    try:
        for key, relative in FILES.items():
            (ROOT / relative).write_text(
                changed[key],
                encoding="utf-8",
            )
    except Exception:
        for key, relative in FILES.items():
            (ROOT / relative).write_text(
                original[key],
                encoding="utf-8",
            )
        raise

    print("Backup kode:", backup)
    print("Integrasi file selesai.")
    print("Lanjutkan dengan pemeriksaan Flask dan halaman.")


if __name__ == "__main__":
    main()
