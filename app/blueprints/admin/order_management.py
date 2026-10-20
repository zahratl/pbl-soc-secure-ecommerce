#File ini menyediakan route detail pesanan serta aksi Admin untuk mengemas, mengirim, memperbarui resi, dan menyelesaikan pesanan.
import secrets
from datetime import datetime, timedelta, timezone

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.order import Order
from app.models.shipment import OrderShipment
from .routes import admin_bp, admin_only


WIB = timezone(timedelta(hours=7))
JNE_COURIER = 'JNE Express Reguler'


def format_order_date(value):
    if value is None:
        return '-'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(WIB).strftime('%d/%m/%Y %H:%M WIB')


def format_order_price(amount):
    return 'Rp ' + f'{int(amount or 0):,}'.replace(',', '.')


def fulfillment_description(order):
    if order.payment_status != 'paid':
        return 'Menunggu Pembayaran'
    if order.stock_deducted_at is None or order.fulfillment_status == 'stock_issue':
        return 'Perlu Cek Stok'
    return {
        'unfulfilled': 'Perlu Diproses',
        'processing': 'Sedang Dikemas',
        'shipped': 'Dikirim',
        'completed': 'Selesai'
    }.get(order.fulfillment_status, 'Belum Diproses')


def generate_internal_reference(order):
    date_code = datetime.now(WIB).strftime('%Y%m%d')
    return f'KW-INT-{date_code}-{order.id:06d}-{secrets.token_hex(5).upper()}'


@admin_bp.route('/orders/<string:order_number>', methods=['GET'])
@admin_only
def order_detail(order_number):
    if len(order_number) > 64:
        abort(404)

    order = Order.query.filter_by(order_number=order_number).first_or_404()
    shipment = OrderShipment.query.filter_by(order_id=order.id).first()
    payments = sorted(order.payments, key=lambda payment: payment.id or 0, reverse=True)

    return render_template(
        'admin/order_detail.html',
        order=order,
        shipment=shipment,
        payments=payments,
        fulfillment_label=fulfillment_description(order),
        format_date=format_order_date,
        rupiah=format_order_price,
        jne_courier=JNE_COURIER
    )


@admin_bp.route('/orders/<string:order_number>/update', methods=['POST'])
@admin_only
def order_update(order_number):
    if not FlaskForm().validate_on_submit():
        abort(400)
    if len(order_number) > 64:
        abort(404)

    order = Order.query.filter_by(order_number=order_number).first_or_404()
    action = (request.form.get('action') or '').strip()
    target = url_for('admin.order_detail', order_number=order.order_number)
    transitions = {'processing': 'unfulfilled', 'shipped': 'processing', 'completed': 'shipped'}

    if action not in transitions:
        abort(400)

    if order.payment_status != 'paid' or order.stock_deducted_at is None or order.fulfillment_status == 'stock_issue':
        flash('Pesanan harus lunas dan stok harus tersinkronkan sebelum diproses.', 'warning')
        return redirect(target)

    expected_status = transitions[action]
    if order.fulfillment_status != expected_status:
        flash('Urutan status tidak sesuai. Refresh halaman dan periksa kembali pesanan.', 'warning')
        return redirect(target)

    notes = (request.form.get('notes') or '').strip() if action == 'shipped' else ''
    if len(notes) > 500:
        flash('Catatan pengiriman maksimal 500 karakter.', 'warning')
        return redirect(target)

    shipment = OrderShipment.query.filter_by(order_id=order.id).first()
    now = datetime.now(timezone.utc)

    try:
        result = db.session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.fulfillment_status == expected_status,
                Order.payment_status == 'paid',
                Order.stock_deducted_at.is_not(None)
            )
            .values(fulfillment_status=action)
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            db.session.rollback()
            flash('Status pesanan telah berubah. Silakan refresh halaman.', 'warning')
            return redirect(target)

        if shipment is None:
            shipment = OrderShipment(order_id=order.id)
            db.session.add(shipment)

        if action == 'processing':
            shipment.courier = JNE_COURIER
            shipment.processing_at = shipment.processing_at or now

        elif action == 'shipped':
            shipment.courier = JNE_COURIER
            shipment.tracking_number = generate_internal_reference(order)
            shipment.notes = notes or None
            shipment.shipped_at = now

        elif action == 'completed':
            shipment.completed_at = now

        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal memperbarui status pesanan %s', order.order_number)
        flash('Gagal menyimpan status pengiriman. Silakan coba lagi.', 'danger')
        return redirect(target)

    messages = {
        'processing': 'Pesanan berhasil masuk ke proses pengemasan.',
        'shipped': 'Pengiriman simulasi JNE dicatat dan kode internal otomatis berhasil dibuat.',
        'completed': 'Pesanan simulasi berhasil ditandai Selesai.'
    }
    current_app.logger.info('Order %s fulfillment changed to %s', order.order_number, action)
    flash(messages[action], 'success')
    return redirect(target)