#pengelolaan profil dan avatar, lalu menambahkan penyimpanan alamat serta pengambilan riwayat Order dan Shipment milik customer yang sedang login.
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.customer_address import CustomerAddress, validate_address
from app.models.customer_profile import CustomerProfile
from app.models.order import Order
from app.models.shipment import OrderShipment
from app.services.customer_encryption_reader import read_customer_private


customer_bp = Blueprint('customer', __name__)
WIB = timezone(timedelta(hours=7))


def avatar_folder():
    folder = Path(current_app.static_folder) / 'uploads' / 'avatars'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get_display_name(user, profile):
    return (profile.display_name if profile else None) or user.name or user.email.split('@')[0]


def save_avatar(upload):
    data = upload.read(2 * 1024 * 1024 + 1)

    if not data or len(data) > 2 * 1024 * 1024:
        raise ValueError('Ukuran foto maksimal 2 MB.')

    filename = f'{uuid.uuid4().hex}.webp'
    destination = avatar_folder() / filename

    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP') or source.width * source.height > 12000000:
                raise ValueError('Format atau resolusi foto tidak valid.')

            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((640, 640))
            image.save(destination, 'WEBP', quality=85)

    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        destination.unlink(missing_ok=True)
        raise ValueError('Foto harus JPG, PNG, atau WEBP valid dengan resolusi maksimal 12 MP.') from exc

    return filename


def delete_avatar_file(filename):
    if filename and Path(filename).name == filename and filename.endswith('.webp'):
        (avatar_folder() / filename).unlink(missing_ok=True)


def format_date(value):
    if not value:
        return '-'

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(WIB).strftime('%d/%m/%Y %H:%M WIB')


def rupiah(amount):
    return 'Rp ' + f'{int(amount or 0):,}'.replace(',', '.')


def order_payment_label(status):
    return {
        'initiating': 'Menyiapkan Pembayaran',
        'pending': 'Menunggu Pembayaran',
        'paid': 'Lunas',
        'expired': 'Kedaluwarsa',
        'canceled': 'Dibatalkan',
        'failed': 'Gagal',
        'token_error': 'Gagal Membuat Pembayaran',
        'refunded': 'Dikembalikan',
        'partial_refund': 'Refund Sebagian'
    }.get(status, status.replace('_', ' ').title())


def order_delivery_label(order):
    if order.payment_status != 'paid':
        return 'Menunggu Pembayaran' if order.payment_status in ('pending', 'initiating') else 'Belum Dikirim'

    if order.fulfillment_status == 'stock_issue' or not order.stock_deducted_at:
        return 'Menunggu Konfirmasi Stok'

    return {
        'unfulfilled': 'Perlu Diproses',
        'processing': 'Sedang Dikemas',
        'shipped': 'Dikirim',
        'completed': 'Selesai'
    }.get(order.fulfillment_status, 'Dalam Proses')


@customer_bp.route('/profile')
@login_required
def profile():
    return redirect(url_for('customer.my_profile'))


@customer_bp.route('/my-profile', methods=['GET', 'POST'])
@login_required
def my_profile():
    if current_user.role == 'admin':
        return redirect(url_for('admin.dashboard'))

    if current_user.role != 'customer':
        return redirect(url_for('shop.index'))

    profile = db.session.get(CustomerProfile, current_user.id)
    address = CustomerAddress.query.filter_by(user_id=current_user.id).first()

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        if action not in ('save', 'upload_avatar', 'delete_avatar', 'save_address'):
            flash('Permintaan tidak valid.', 'danger')
            return redirect(url_for('customer.my_profile'))

        if action == 'save':
            name = request.form.get('display_name', '').strip()
            phone = request.form.get('phone', '').strip()
            gender = request.form.get('gender', '').strip().lower()
            birth_text = request.form.get('birth_date', '').strip()

            if not 2 <= len(name) <= 100:
                flash('Nama harus 2–100 karakter.', 'danger')
                return redirect(url_for('customer.my_profile'))

            if len(phone) > 25 or any(char not in '0123456789+ -()' for char in phone):
                flash('Nomor WhatsApp tidak valid.', 'danger')
                return redirect(url_for('customer.my_profile'))

            if gender not in ('', 'men', 'women', 'other'):
                flash('Pilihan gender tidak valid.', 'danger')
                return redirect(url_for('customer.my_profile'))

            try:
                birth_date = date.fromisoformat(birth_text) if birth_text else None
            except ValueError:
                flash('Tanggal lahir tidak valid.', 'danger')
                return redirect(url_for('customer.my_profile'))

            if birth_date and (birth_date > date.today() or birth_date.year < 1900):
                flash('Tanggal lahir tidak valid.', 'danger')
                return redirect(url_for('customer.my_profile'))

            if profile is None:
                profile = CustomerProfile(user_id=current_user.id)
                db.session.add(profile)

            profile.display_name = name
            profile.phone = phone or None
            profile.birth_date = birth_date
            profile.gender = gender or None
            profile.updated_at = datetime.now(timezone.utc)
            current_user.name = name
            db.session.commit()

            flash('Profil berhasil diperbarui.', 'success')
            return redirect(url_for('customer.my_profile'))

        if action == 'save_address':
            try:
                fields = validate_address(request.form)

                if address is None:
                    address = CustomerAddress(user_id=current_user.id, **fields)
                    db.session.add(address)
                else:
                    for key, value in fields.items():
                        setattr(address, key, value)

                if profile is None:
                    profile = CustomerProfile(user_id=current_user.id)
                    db.session.add(profile)

                if not read_customer_private(profile, 'phone'):
                    profile.phone = fields['phone']

                db.session.commit()
                flash('Alamat pengiriman berhasil disimpan dan akan digunakan saat checkout.', 'success')

            except ValueError as exc:
                flash(str(exc), 'danger')

            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception('Gagal menyimpan alamat customer')
                flash('Gagal menyimpan alamat ke database.', 'danger')

            return redirect(url_for('customer.my_profile') + '#address-section')

        if action == 'upload_avatar':
            upload = request.files.get('avatar')

            if not upload or not upload.filename:
                flash('Silakan pilih foto profil.', 'danger')
                return redirect(url_for('customer.my_profile'))

            try:
                filename = save_avatar(upload)
            except ValueError as exc:
                flash(str(exc), 'danger')
                return redirect(url_for('customer.my_profile'))

            if profile is None:
                profile = CustomerProfile(user_id=current_user.id)
                db.session.add(profile)

            old_filename = profile.avatar_filename
            profile.avatar_filename = filename
            profile.updated_at = datetime.now(timezone.utc)
            current_user.avatar_path = f'uploads/avatars/{filename}'

            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                delete_avatar_file(filename)
                current_app.logger.exception('Gagal menyimpan avatar')
                flash('Gagal menyimpan foto profil.', 'danger')
                return redirect(url_for('customer.my_profile'))

            delete_avatar_file(old_filename)
            flash('Foto profil berhasil diperbarui.', 'success')
            return redirect(url_for('customer.my_profile'))

        if profile and profile.avatar_filename:
            old_filename = profile.avatar_filename
            profile.avatar_filename = None
            profile.updated_at = datetime.now(timezone.utc)
            current_user.avatar_path = None
            db.session.commit()
            delete_avatar_file(old_filename)
            flash('Foto profil berhasil dihapus.', 'success')
        else:
            flash('Belum ada foto profil yang diunggah.', 'warning')

        return redirect(url_for('customer.my_profile'))

    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.id.desc()).all()
    shipment_rows = OrderShipment.query.filter(OrderShipment.order_id.in_([order.id for order in orders])).all() if orders else []
    shipments = {row.order_id: row for row in shipment_rows}
    fallback_order = orders[0] if orders and address is None else None

    return render_template(
        'customer/profile.html',
        profile=profile,
        display_name=get_display_name(current_user, profile),
        address=address,
        fallback_order=fallback_order,
        orders=orders,
        shipments=shipments,
        format_date=format_date,
        rupiah=rupiah,
        order_payment_label=order_payment_label,
        order_delivery_label=order_delivery_label
    )


@customer_bp.route('/order-history')
@login_required
def order_history():
    return redirect(url_for('customer.my_profile') + '#orders-section')