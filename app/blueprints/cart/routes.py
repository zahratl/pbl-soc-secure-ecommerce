import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import csrf, db
from app.models.cart import CartItem
from app.models.customer_address import CustomerAddress, validate_address
from app.models.customer_profile import CustomerProfile
from app.models.order import Order, OrderItem
from app.models.payment import PaymentTransaction
from app.models.product import Product, ProductVariant
from app.models.shipment import OrderShipment
from app.models.wishlist import WishlistItem
from app.services.customer_encryption_reader import read_customer_private


cart_bp = Blueprint('cart', __name__)
PAYMENT_METHODS = ['other_qris', 'gopay', 'bca_va', 'bni_va', 'bri_va']


@cart_bp.app_context_processor
def inject_navbar_cart_count():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return {'navbar_cart_count': 0}

    count = db.session.query(db.func.coalesce(db.func.sum(CartItem.quantity), 0)).filter(CartItem.user_id == current_user.id).scalar()
    return {'navbar_cart_count': int(count or 0)}


def rupiah(amount):
    return 'Rp ' + f'{int(amount or 0):,}'.replace(',', '.')


def find_product_variant(product, color_name, size):
    if product is None:
        return None

    color = (color_name or '').strip().casefold()
    size = (size or '').strip().upper()

    return next((variant for variant in product.variants if (variant.color_name or '').strip().casefold() == color and (variant.size or '').strip().upper() == size), None)


def customer_required():
    if not current_user.is_authenticated:
        flash('Silakan login terlebih dahulu.', 'warning')
        return redirect(url_for('auth.login'))

    if current_user.role != 'customer':
        flash('Fitur belanja hanya tersedia untuk customer.', 'warning')
        return redirect(url_for('shop.index'))

    return None


def get_own_cart_item(item_id):
    return CartItem.query.filter_by(id=item_id, user_id=current_user.id).first_or_404()


def selected_cart_ids(raw, required=False):
    if raw is None and not required:
        return None

    if not isinstance(raw, str) or not raw or len(raw) > 1000:
        raise ValueError('Pilih produk dari keranjang terlebih dahulu.')

    parts = raw.split(',')

    if len(parts) > 100 or any(not part.isdigit() for part in parts):
        raise ValueError('ID produk yang dipilih tidak valid.')

    ids = list(dict.fromkeys(int(part) for part in parts))

    if any(item_id <= 0 for item_id in ids):
        raise ValueError('ID produk tidak valid.')

    return ids


def checkout_items(selected_ids=None):
    query = CartItem.query.filter_by(user_id=current_user.id)

    if selected_ids is not None:
        query = query.filter(CartItem.id.in_(selected_ids))

    records = query.order_by(CartItem.id.desc()).all()

    if not records or (selected_ids is not None and len(records) != len(selected_ids)):
        raise ValueError('Produk di keranjang tidak ditemukan. Periksa kembali pilihanmu.')

    items = []

    for record in records:
        product = record.product
        variant = find_product_variant(product, record.color_name, record.size)
        stock = max(0, int(variant.stock or 0)) if variant else 0

        if product is None or product.status != 'published' or not variant or record.quantity < 1 or record.quantity > stock or int(product.price or 0) <= 0:
            raise ValueError('Ada produk yang tidak tersedia atau stoknya berubah. Silakan periksa keranjang.')

        price = int(product.price)
        path = (product.images or [None])[0]
        image = path if path and path.startswith(('https://', 'http://')) else url_for('static', filename=path) if path else ''

        items.append({
            'id': record.id,
            'product_id': product.id,
            'variant_id': variant.id,
            'cart_created_at': record.created_at.isoformat() if record.created_at else '',
            'sku': product.sku,
            'image_path': path or '',
            'name': product.name,
            'category': product.subcategory or '',
            'image': image,
            'price': price,
            'price_text': rupiah(price),
            'quantity': record.quantity,
            'line_total': price * record.quantity,
            'line_total_text': rupiah(price * record.quantity),
            'color': record.color_name,
            'size': record.size,
            'stock': stock
        })

    return items


def current_address():
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


def pending_fingerprint(items, amount, address):
    snapshots = [
        {
            'cart_id': item['id'],
            'cart_created_at': item['cart_created_at'],
            'product_id': item['product_id'],
            'variant_id': item['variant_id'],
            'price': item['price'],
            'quantity': item['quantity'],
            'color': item['color'],
            'size': item['size']
        }
        for item in items
    ]

    payload = {
        'items': sorted(snapshots, key=lambda item: item['cart_id']),
        'amount': amount,
        'address': address
    }

    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def latest_matching_order(fingerprint):
    return Order.query.filter_by(user_id=current_user.id, checkout_fingerprint=fingerprint).order_by(Order.id.desc()).first()


def latest_order_payment(order):
    return max(order.payments, key=lambda payment: payment.id or 0) if order.payments else None


def remember_pending_order(order, payment):
    session['pending_snap'] = {
        'order_id': order.order_number,
        'token': payment.snap_token,
        'user_id': current_user.id,
        'amount': order.total,
        'fingerprint': order.checkout_fingerprint
    }


def order_age_seconds(order):
    created_at = order.created_at or datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return (datetime.now(timezone.utc) - created_at).total_seconds()


def build_order(items, address, total, fingerprint):
    order = Order(
        order_number=f'KW-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8].upper()}',
        user_id=current_user.id,
        customer_name=address['recipient_name'],
        customer_email=current_user.email,
        recipient_name=address['recipient_name'],
        phone=address['phone'],
        city=address['city'],
        full_address=address['full_address'],
        subtotal=total,
        shipping_cost=0,
        total=total,
        payment_status='initiating',
        checkout_fingerprint=fingerprint
    )

    order.items = [
        OrderItem(
            source_cart_item_id=item['id'],
            product_id=item['product_id'],
            variant_id=item['variant_id'],
            sku=item['sku'],
            product_name=item['name'],
            image_path=item['image_path'],
            color_name=item['color'],
            size=item['size'],
            unit_price=item['price'],
            quantity=item['quantity'],
            line_total=item['line_total']
        )
        for item in items
    ]

    payment = PaymentTransaction(gross_amount=total, transaction_status='initiating')
    order.payments.append(payment)

    db.session.add(order)
    db.session.flush()
    db.session.add(OrderShipment(order_id=order.id, courier='JNE Express Reguler'))
    db.session.commit()

    return order, payment


class StockUnavailable(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def date_as_utc(value):
    if value is None:
        return None

    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def payment_state(result):
    status = str(result.get('transaction_status') or '').lower()
    fraud = str(result.get('fraud_status') or '').lower()
    success_code = str(result.get('status_code') or '') == '200'

    if status == 'settlement' and success_code and fraud in ('', 'accept'):
        return 'paid'

    if status == 'capture' and fraud in ('', 'accept') and success_code:
        return 'paid'

    if status in ('pending', 'authorize') or (status == 'capture' and fraud == 'challenge'):
        return 'pending'

    if status == 'expire':
        return 'expired'

    if status == 'cancel':
        return 'canceled'

    if status in ('deny', 'failure'):
        return 'failed'

    if status == 'refund':
        return 'refunded'

    if status == 'partial_refund':
        return 'partial_refund'

    if status in ('chargeback', 'partial_chargeback'):
        return status

    raise ValueError('Status pembayaran Midtrans belum dikenal atau belum terverifikasi.')


def verified_order_status(order, result):
    try:
        amount = Decimal(str(result.get('gross_amount', '')))
        valid = amount.is_finite() and amount == Decimal(order.total)
    except (InvalidOperation, ValueError, TypeError):
        valid = False

    if result.get('order_id') != order.order_number or not valid:
        raise ValueError('Nomor pesanan atau nominal Midtrans tidak sesuai.')

    return payment_state(result)


def synchronize_paid_stock(order):
    if order.stock_deducted_at is not None:
        return True

    quantities = defaultdict(int)

    for item in order.items:
        if not item.variant_id or item.quantity < 1:
            raise StockUnavailable('Varian pesanan tidak valid.')

        quantities[item.variant_id] += item.quantity

    if not quantities:
        raise StockUnavailable('Pesanan tidak memiliki produk.')

    mark_time = utc_now()

    with db.session.begin_nested():
        claimed = db.session.execute(
            update(Order)
            .where(Order.id == order.id, Order.stock_deducted_at.is_(None))
            .values(stock_deducted_at=mark_time)
            .execution_options(synchronize_session=False)
        )

        if claimed.rowcount != 1:
            return False

        for variant_id, quantity in quantities.items():
            deducted = db.session.execute(
                update(ProductVariant)
                .where(ProductVariant.id == variant_id, ProductVariant.stock >= quantity)
                .values(stock=ProductVariant.stock - quantity)
                .execution_options(synchronize_session=False)
            )

            if deducted.rowcount != 1:
                raise StockUnavailable('Stok varian tidak mencukupi atau sudah dihapus.')

        for item in order.items:
            if not item.source_cart_item_id:
                continue

            record = db.session.get(CartItem, item.source_cart_item_id)

            if record is None or record.user_id != order.user_id or record.product_id != item.product_id:
                continue

            if (record.color_name or '').casefold() != (item.color_name or '').casefold() or (record.size or '').upper() != (item.size or '').upper():
                continue

            if date_as_utc(record.created_at) and date_as_utc(order.created_at) and date_as_utc(record.created_at) > date_as_utc(order.created_at):
                continue

            if record.quantity <= item.quantity:
                db.session.delete(record)
            else:
                record.quantity -= item.quantity

    order.stock_deducted_at = mark_time

    if order.fulfillment_status == 'stock_issue':
        order.fulfillment_status = 'unfulfilled'

    return True


def apply_verified_payment_status(order, payment, result):
    state = verified_order_status(order, result)
    transaction_id = result.get('transaction_id')

    if payment.midtrans_transaction_id and transaction_id and payment.midtrans_transaction_id != transaction_id:
        raise ValueError('ID transaksi Midtrans tidak sesuai dengan transaksi tersimpan.')

    if order.payment_status in ('refunded', 'partial_refund', 'chargeback', 'partial_chargeback') and state not in ('refunded', 'partial_refund', 'chargeback', 'partial_chargeback'):
        return order.payment_status

    if order.payment_status == 'paid' and state in ('pending', 'failed', 'expired', 'canceled'):
        return 'paid'

    payment.transaction_status = (result.get('transaction_status') or '')[:30]
    payment.payment_type = (result.get('payment_type') or '')[:50] or None
    payment.fraud_status = (result.get('fraud_status') or '')[:30] or None
    payment.midtrans_transaction_id = str(transaction_id)[:100] if transaction_id else payment.midtrans_transaction_id

    bank_details = result.get('va_numbers') or []
    bank = bank_details[0].get('bank') if isinstance(bank_details, list) and bank_details and isinstance(bank_details[0], dict) else result.get('bank')

    payment.bank = str(bank)[:30] if bank else None
    payment.verified_at = utc_now()
    order.payment_status = state

    if state == 'paid':
        if order.paid_at is None:
            order.paid_at = utc_now()

        if payment.settled_at is None:
            payment.settled_at = utc_now()

        if order.stock_deducted_at is None:
            try:
                synchronize_paid_stock(order)
            except StockUnavailable:
                order.fulfillment_status = 'stock_issue'
                current_app.logger.warning('Paid order %s requires stock reconciliation.', order.order_number)

    db.session.commit()

    return state


def get_verified_midtrans_status(order, server_key):
    encoded_id = quote(order.order_number, safe='')
    result = midtrans_request(f'https://api.sandbox.midtrans.com/v2/{encoded_id}/status', server_key)

    verified_order_status(order, result)

    return result


def payment_credentials():
    server_key = (current_app.config.get('MIDTRANS_SERVER_KEY') or '').strip()
    client_key = (current_app.config.get('MIDTRANS_CLIENT_KEY') or '').strip()

    return server_key, client_key


def midtrans_request(url, server_key, payload=None):
    authorization = base64.b64encode(f'{server_key}:'.encode()).decode()

    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Authorization': f'Basic {authorization}'
    }

    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method='POST' if payload is not None else 'GET')

    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def request_midtrans_snap_token(order_id, gross_amount, items, customer, enabled_payments=None):
    server_key, client_key = payment_credentials()

    if not server_key or not client_key:
        return None, 'Server Key atau Client Key Midtrans Sandbox belum dikonfigurasi.'

    details = [
        {
            'id': str(item['id'])[:50],
            'price': item['price'],
            'quantity': item['quantity'],
            'name': item['name'][:50]
        }
        for item in items
    ]

    if sum(item['price'] * item['quantity'] for item in details) != gross_amount:
        return None, 'Total transaksi tidak sesuai dengan rincian produk.'

    payload = {
        'transaction_details': {
            'order_id': order_id,
            'gross_amount': gross_amount
        },
        'item_details': details,
        'customer_details': {
            'first_name': customer['first_name'],
            'last_name': customer['last_name'],
            'email': customer['email'],
            'phone': customer['phone'],
            'shipping_address': {
                'first_name': customer['first_name'],
                'phone': customer['phone'],
                'address': customer['address'],
                'city': customer['city'],
                'country_code': 'IDN'
            }
        },
        'enabled_payments': enabled_payments or PAYMENT_METHODS
    }

    try:
        result = midtrans_request('https://app.sandbox.midtrans.com/snap/v1/transactions', server_key, payload)
        token = result.get('token')

        return (token, None) if token else (None, 'Midtrans tidak mengembalikan Snap Token.')

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')

        try:
            response = json.loads(raw)
        except ValueError:
            response = {}

        messages = response.get('error_messages') if isinstance(response, dict) else None

        if isinstance(messages, list):
            reason = ' | '.join(str(message) for message in messages if message)
        elif isinstance(messages, str):
            reason = messages
        else:
            reason = response.get('message') or response.get('error') or '' if isinstance(response, dict) else ''

        if not reason:
            reason = {
                400: 'Parameter transaksi tidak valid.',
                401: 'Server Key salah atau tidak sesuai dengan Sandbox.',
                402: 'Metode pembayaran tidak diizinkan untuk merchant.',
                406: 'Order ID sudah digunakan.',
                500: 'Terjadi gangguan internal Midtrans.',
                503: 'Layanan Midtrans sementara tidak tersedia.'
            }.get(exc.code, 'Permintaan transaksi ditolak Midtrans.')

        current_app.logger.error('Midtrans Snap HTTP %s: %s', exc.code, reason)

        return None, f'Midtrans HTTP {exc.code}: {reason}'

    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        current_app.logger.error('Midtrans Snap connection error: %s', type(exc).__name__)

        return None, 'Tidak dapat terhubung ke Midtrans. Periksa koneksi internet.'


@cart_bp.route('/', methods=['GET'])
def view_cart():
    items = []

    if current_user.is_authenticated and current_user.role == 'customer':
        wishlist_ids = {wish.product_id for wish in WishlistItem.query.filter_by(user_id=current_user.id).all()}
        records = CartItem.query.filter_by(user_id=current_user.id).order_by(CartItem.id.desc()).all()

        for record in records:
            product = record.product
            variant = find_product_variant(product, record.color_name, record.size)
            stock = max(0, int(variant.stock or 0)) if variant else 0
            available = bool(product and product.status == 'published' and variant and stock >= record.quantity and stock > 0)
            price = int(product.price or 0) if product else 0
            path = (product.images or [None])[0] if product else None
            image = path if path and path.startswith(('https://', 'http://')) else url_for('static', filename=path) if path else ''

            items.append({
                'id': record.id,
                'product_id': product.id if product else None,
                'name': product.name if product else 'Produk tidak tersedia',
                'category': (product.subcategory or '') if product else '',
                'image': image,
                'price': price,
                'price_text': rupiah(price),
                'quantity': record.quantity,
                'line_total': price * record.quantity,
                'line_total_text': rupiah(price * record.quantity),
                'color': record.color_name,
                'size': record.size,
                'stock': stock,
                'available': available,
                'collection': (product.collection_label or '') if product else '',
                'wishlisted': bool(product and product.id in wishlist_ids)
            })

    return render_template('cart/cart.html', items=items, total_pieces=sum(item['quantity'] for item in items), rupiah=rupiah)


@cart_bp.route('/add', methods=['POST'])
def add_item():
    if error := customer_required():
        return error

    try:
        product_id = int(request.form.get('product_id', '0'))
        quantity = int(request.form.get('quantity', '1'))
    except (TypeError, ValueError):
        product_id, quantity = 0, 0

    product = db.session.get(Product, product_id)

    if quantity < 1 or quantity > 100 or not product or product.status != 'published':
        flash('Produk atau jumlah tidak valid.', 'warning')
        return redirect(url_for('shop.catalog_all'))

    variant = find_product_variant(product, request.form.get('color_name'), request.form.get('size'))

    if not variant:
        flash('Pilih warna dan ukuran yang tersedia.', 'warning')
        return redirect(url_for('shop.product_detail', product_id=product_id))

    item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id, color_name=variant.color_name, size=variant.size).first()

    if (item.quantity if item else 0) + quantity > max(0, int(variant.stock or 0)):
        flash('Jumlah produk melebihi stok yang tersedia.', 'warning')
        return redirect(url_for('shop.product_detail', product_id=product_id))

    if item:
        item.quantity += quantity
    else:
        db.session.add(CartItem(user_id=current_user.id, product_id=product_id, color_name=variant.color_name, size=variant.size, quantity=quantity))

    db.session.commit()
    flash('Produk berhasil ditambahkan ke keranjang.', 'success')

    return redirect(url_for('cart.view_cart'))


@cart_bp.route('/item/<int:item_id>/quantity', methods=['POST'])
def update_quantity(item_id):
    if error := customer_required():
        return error

    item = get_own_cart_item(item_id)

    try:
        delta = int(request.form.get('delta', '0'))
    except ValueError:
        delta = 0

    variant = find_product_variant(item.product, item.color_name, item.size)
    next_quantity = item.quantity + delta

    if delta not in (-1, 1) or not item.product or item.product.status != 'published' or not variant or next_quantity < 1 or next_quantity > int(variant.stock or 0):
        flash('Jumlah produk tidak valid atau stok tidak cukup.', 'warning')
        return redirect(url_for('cart.view_cart'))

    item.quantity = next_quantity
    db.session.commit()

    return redirect(url_for('cart.view_cart'))


@cart_bp.route('/item/<int:item_id>/remove', methods=['POST'])
def remove_item(item_id):
    if error := customer_required():
        return error

    db.session.delete(get_own_cart_item(item_id))
    db.session.commit()
    flash('Produk dihapus dari keranjang.', 'success')

    return redirect(url_for('cart.view_cart'))


@cart_bp.route('/remove-selected', methods=['POST'])
def remove_selected():
    if error := customer_required():
        return error

    ids = [int(value) for value in request.form.getlist('item_ids')[:100] if value.isdigit()]

    if not ids:
        flash('Pilih produk yang ingin dihapus.', 'warning')
        return redirect(url_for('cart.view_cart'))

    for item in CartItem.query.filter(CartItem.user_id == current_user.id, CartItem.id.in_(ids)).all():
        db.session.delete(item)

    db.session.commit()
    flash('Produk pilihan dihapus dari keranjang.', 'success')

    return redirect(url_for('cart.view_cart'))


@cart_bp.route('/item/<int:item_id>/move-to-wishlist', methods=['POST'])
def move_to_wishlist(item_id):
    if error := customer_required():
        return error

    item = get_own_cart_item(item_id)

    if not item.product or item.product.status != 'published':
        flash('Produk sudah tidak tersedia.', 'warning')
        return redirect(url_for('cart.view_cart'))

    if db.session.get(WishlistItem, (current_user.id, item.product_id)) is None:
        db.session.add(WishlistItem(user_id=current_user.id, product_id=item.product_id))
        db.session.commit()
        flash('Produk ditambahkan ke Wishlist tanpa dihapus dari keranjang.', 'success')
    else:
        flash('Produk sudah ada di Wishlist.', 'info')

    return redirect(url_for('cart.view_cart'))


@cart_bp.route('/checkout', methods=['GET'])
def checkout():
    if error := customer_required():
        return error

    try:
        ids = selected_cart_ids(request.args.get('items'))
        items = checkout_items(ids)
    except ValueError as exc:
        flash(str(exc), 'warning')
        return redirect(url_for('cart.view_cart'))

    subtotal = sum(item['line_total'] for item in items)

    return render_template(
        'cart/checkout.html',
        items=items,
        subtotal=subtotal,
        shipping_cost=0,
        grand_total=subtotal,
        address=current_address(),
        selected_item_ids=','.join(str(item['id']) for item in items),
        midtrans_client_key=payment_credentials()[1],
        rupiah=rupiah
    )


@cart_bp.route('/save-address', methods=['POST'])
def save_address():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return jsonify({'status': 'error', 'message': 'Silakan login sebagai customer.'}), 401

    data = request.get_json(silent=True)

    if data is None:
        data = request.form

    try:
        address_data = validate_address(data)
    except (ValueError, AttributeError) as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    saved = CustomerAddress.query.filter_by(user_id=current_user.id).first()

    if saved is None:
        saved = CustomerAddress(user_id=current_user.id, **address_data)
        db.session.add(saved)
    else:
        for key, value in address_data.items():
            setattr(saved, key, value)

    profile = db.session.get(CustomerProfile, current_user.id)

    if profile is None:
        profile = CustomerProfile(user_id=current_user.id)
        db.session.add(profile)

    if not read_customer_private(profile, 'phone'):
        profile.phone = address_data['phone']

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal menyimpan alamat checkout')

        return jsonify({'status': 'error', 'message': 'Alamat gagal disimpan ke database.'}), 500

    session.pop('shipping_address', None)
    session.pop('shipping_address_user_id', None)
    session.pop('pending_snap', None)

    return jsonify({
        'status': 'success',
        'message': 'Alamat tersimpan di Profil dan Checkout.',
        'address': current_address()
    })


@cart_bp.route('/payment-snap', methods=['GET'])
def payment_snap():
    if error := customer_required():
        return error

    try:
        ids = selected_cart_ids(request.args.get('items'), required=True)
        items = checkout_items(ids)
    except ValueError as exc:
        flash(str(exc), 'warning')
        return redirect(url_for('cart.view_cart'))

    if not current_address():
        flash('Lengkapi alamat pengiriman sebelum membayar.', 'warning')
        return redirect(url_for('cart.checkout', items=','.join(map(str, ids))))

    total = sum(item['line_total'] for item in items)
    fingerprint = pending_fingerprint(items, total, current_address())
    matching_order = latest_matching_order(fingerprint)
    last_order_id = matching_order.order_number if matching_order and matching_order.payment_status in ('initiating', 'pending', 'paid') else ''

    return render_template(
        'cart/payment_snap.html',
        items=items,
        grand_total=total,
        selected_item_ids=','.join(map(str, ids)),
        midtrans_client_key=payment_credentials()[1],
        last_order_id=last_order_id,
        rupiah=rupiah
    )


@cart_bp.route('/create-snap-token', methods=['POST'])
def create_snap_token():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return jsonify({'status': 'error', 'message': 'Silakan login sebagai customer.'}), 401

    data = request.get_json(silent=True) or {}

    try:
        ids = selected_cart_ids(data.get('items'), required=True)
        items = checkout_items(ids)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    address = current_address()

    if not address:
        return jsonify({'status': 'error', 'message': 'Alamat pengiriman belum lengkap.'}), 400

    total = sum(item['line_total'] for item in items)
    fingerprint = pending_fingerprint(items, total, address)
    existing_order = latest_matching_order(fingerprint)

    if existing_order and existing_order.payment_status == 'paid':
        return jsonify({
            'status': 'error',
            'message': f'Pesanan {existing_order.order_number} sudah dibayar. Jangan membayar ulang pesanan yang sama.'
        }), 409

    if existing_order and existing_order.payment_status in ('pending', 'initiating'):
        existing_payment = latest_order_payment(existing_order)

        if existing_payment and existing_payment.snap_token:
            remember_pending_order(existing_order, existing_payment)

            return jsonify({
                'status': 'success',
                'snap_token': existing_payment.snap_token,
                'order_id': existing_order.order_number,
                'gross_amount': existing_order.total
            })

        if order_age_seconds(existing_order) < 120:
            return jsonify({
                'status': 'error',
                'message': 'Pembuatan pembayaran masih diproses. Tunggu sebentar lalu coba kembali.'
            }), 409

        existing_order.payment_status = 'token_error'

        if existing_payment:
            existing_payment.transaction_status = 'token_error'

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception('Gagal memperbarui transaksi tertunda')

            return jsonify({'status': 'error', 'message': 'Database sedang mengalami kendala.'}), 500

    try:
        order, payment = build_order(items, address, total, fingerprint)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal menyimpan Order sebelum meminta Snap Token')

        return jsonify({'status': 'error', 'message': 'Pesanan tidak dapat disimpan ke database.'}), 500

    names = address['recipient_name'].split(' ', 1)

    customer = {
        'first_name': names[0],
        'last_name': names[1] if len(names) > 1 else '',
        'email': current_user.email,
        'phone': address['phone'],
        'address': address['full_address'],
        'city': address['city']
    }

    details = [
        {
            'id': f'PRD-{item["product_id"]}-{item["id"]}',
            'name': f'{item["name"]} ({item["color"]}, {item["size"]})',
            'price': item['price'],
            'quantity': item['quantity']
        }
        for item in items
    ]

    token, error = request_midtrans_snap_token(order.order_number, total, details, customer)

    try:
        if not token:
            order.payment_status = 'token_error'
            payment.transaction_status = 'token_error'
            db.session.commit()

            return jsonify({'status': 'error', 'message': error or 'Gagal menghubungi Midtrans.'}), 502

        order.payment_status = 'pending'
        payment.snap_token = token
        payment.transaction_status = 'pending'
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal menyimpan status Snap Token')

        return jsonify({
            'status': 'error',
            'message': 'Token Midtrans berhasil dibuat, tetapi gagal disimpan. Jangan membayar ulang dulu; periksa log transaksi.'
        }), 500

    remember_pending_order(order, payment)

    return jsonify({
        'status': 'success',
        'snap_token': token,
        'order_id': order.order_number,
        'gross_amount': total
    })


@cart_bp.route('/payment-status', methods=['GET'])
def payment_status():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return jsonify({'message': 'Silakan login sebagai customer.'}), 401

    order_id = (request.args.get('order_id') or '').strip()

    if not order_id or len(order_id) > 64:
        return jsonify({'message': 'Nomor pesanan tidak valid.'}), 400

    order = Order.query.filter_by(order_number=order_id, user_id=current_user.id).first()

    if order is None:
        return jsonify({'message': 'Pesanan tidak ditemukan.'}), 404

    payment = latest_order_payment(order)

    if payment is None:
        return jsonify({'message': 'Data pembayaran belum tersedia.'}), 404

    server_key, _ = payment_credentials()

    if not server_key:
        return jsonify({'message': 'Konfigurasi Midtrans belum lengkap.'}), 503

    try:
        result = get_verified_midtrans_status(order, server_key)

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return jsonify({'status': 'pending', 'transaction_status': 'not_found'})

        current_app.logger.warning('Midtrans status HTTP %s', exc.code)
        return jsonify({'message': 'Status Midtrans belum dapat diperiksa.'}), 502

    except (urllib.error.URLError, TimeoutError):
        return jsonify({'message': 'Koneksi ke Midtrans gagal.'}), 502

    except ValueError:
        return jsonify({'message': 'Nomor pesanan atau nominal Midtrans tidak sesuai.'}), 502

    try:
        state = apply_verified_payment_status(order, payment, result)

    except ValueError:
        return jsonify({'message': 'Status Midtrans belum didukung. Silakan periksa kembali.'}), 502

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal memperbarui status pembayaran %s', order.order_number)

        return jsonify({'message': 'Gagal menyimpan status pembayaran ke database.'}), 500

    frontend_state = 'paid' if state == 'paid' else 'pending' if state == 'pending' else 'failed'

    return jsonify({
        'status': frontend_state,
        'transaction_status': result.get('transaction_status', ''),
        'payment_type': result.get('payment_type', ''),
        'stock_synced': order.stock_deducted_at is not None,
        'fulfillment_status': order.fulfillment_status
    })


@cart_bp.route('/midtrans/notification', methods=['POST'])
@csrf.exempt
def midtrans_notification():
    if request.content_length is not None and request.content_length > 16384:
        return jsonify({'message': 'Payload terlalu besar.'}), 413

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({'message': 'JSON tidak valid.'}), 400

    order_id = payload.get('order_id')
    status_code = payload.get('status_code')
    gross_amount = payload.get('gross_amount')
    signature = payload.get('signature_key')
    server_key, _ = payment_credentials()

    if not server_key:
        return jsonify({'message': 'Konfigurasi Midtrans belum lengkap.'}), 503

    if any(not isinstance(value, str) or not value for value in (order_id, status_code, gross_amount, signature)):
        return jsonify({'message': 'Notifikasi tidak lengkap.'}), 400

    if len(order_id) > 64 or len(signature) != 128:
        return jsonify({'message': 'Notifikasi tidak valid.'}), 400

    expected = hashlib.sha512(f'{order_id}{status_code}{gross_amount}{server_key}'.encode()).hexdigest()

    if not hmac.compare_digest(expected, signature.lower()):
        return jsonify({'message': 'Signature Midtrans tidak valid.'}), 403

    order = Order.query.filter_by(order_number=order_id).first()

    if order is None:
        return jsonify({'message': 'Pesanan tidak ditemukan.'}), 404

    payment = latest_order_payment(order)

    if payment is None:
        return jsonify({'message': 'Pembayaran tidak ditemukan.'}), 404

    try:
        incoming_amount = Decimal(gross_amount)

        if not incoming_amount.is_finite() or incoming_amount != Decimal(order.total):
            return jsonify({'message': 'Nominal pembayaran tidak sesuai.'}), 400

        result = get_verified_midtrans_status(order, server_key)
        state = apply_verified_payment_status(order, payment, result)

    except urllib.error.HTTPError as exc:
        current_app.logger.warning('Midtrans notification status HTTP %s', exc.code)

        return jsonify({'message': 'Gagal memverifikasi Midtrans.'}), 503

    except (urllib.error.URLError, TimeoutError):
        return jsonify({'message': 'Koneksi Midtrans belum tersedia.'}), 503

    except (InvalidOperation, ValueError):
        return jsonify({'message': 'Data transaksi tidak cocok.'}), 400

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal memproses Midtrans notification untuk order %s', order_id)

        return jsonify({'message': 'Gagal menyimpan perubahan transaksi.'}), 500

    return jsonify({
        'status': 'ok',
        'payment_status': state,
        'stock_synced': order.stock_deducted_at is not None
    }), 200