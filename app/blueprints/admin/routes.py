import csv
import io
import json
import uuid
from datetime import date, timedelta, timezone
from functools import wraps
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.customer_profile import CustomerProfile
from app.models.order import Order
from app.models.payment import PaymentTransaction
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.services.audit_logger import log_security_event
from app.services.customer_encryption_reader import read_customer_private
from .forms import ProductForm, SIZES, parse_variants
from app.models.customer_address import CustomerAddress

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.role != 'admin':
        log_security_event(action='UNAUTHORIZED_ADMIN_DASHBOARD_ACCESS', status='FAILURE', user_id=current_user.id, username=current_user.email, details='Customer attempted to view admin dashboard directly.')
        flash('Akses ditolak. Anda tidak memiliki hak akses administrator.', 'danger')
        return redirect(url_for('shop.index'))

    customer_count = User.query.filter_by(role='customer').count()
    published_products = Product.query.filter_by(status='published').all()
    catalog_count = len(published_products)
    catalog_safe = sum(bool(product.variants) and not product.low_stock_variants for product in published_products)
    catalog_low = sum(bool(product.low_stock_variants) for product in published_products)

    return render_template('admin/dashboard.html', customer_count=customer_count, catalog_count=catalog_count, catalog_safe=catalog_safe, catalog_low=catalog_low)


def admin_only(view):
    @wraps(view)
    @login_required
    def secured(*args, **kwargs):
        if current_user.role != 'admin':
            abort(403)
        return view(*args, **kwargs)

    return secured


@admin_bp.route('/store-preview')
@admin_only
def store_preview():
    page = request.args.get('page', 'home')

    if page not in ('home', 'all'):
        abort(400)

    endpoint = 'shop.catalog_all' if page == 'all' else 'shop.index'
    return redirect(url_for(endpoint))


def _rupiah(amount):
    return 'Rp ' + f'{int(amount or 0):,}'.replace(',', '.')


def _order_date(value):
    if value is None:
        return '-'

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M WIB')


def _order_payment_view(order):
    states = {
        'initiating': ('Membuat Pembayaran', 'pending'),
        'pending': ('Menunggu Pembayaran', 'pending'),
        'paid': ('Lunas', 'success'),
        'token_error': ('Gagal Membuat Token', 'failed'),
        'expired': ('Kedaluwarsa', 'failed'),
        'canceled': ('Dibatalkan', 'failed'),
        'cancelled': ('Dibatalkan', 'failed'),
        'failed': ('Gagal', 'failed'),
        'refunded': ('Refund', 'neutral'),
        'partial_refund': ('Refund Sebagian', 'neutral')
    }

    return states.get(order.payment_status, (order.payment_status or 'Tidak Diketahui', 'neutral'))


def _order_fulfillment_view(order):
    if order.fulfillment_status == 'processing':
        return 'Sedang Dikemas', 'progress'

    if order.fulfillment_status == 'shipped':
        return 'Dikirim', 'progress'

    if order.fulfillment_status == 'completed':
        return 'Selesai', 'success'

    if order.fulfillment_status == 'stock_issue':
        return 'Perlu Cek Stok', 'failed'

    if order.payment_status == 'paid':
        if order.stock_deducted_at is not None:
            return 'Perlu Diproses', 'progress'

        return 'Menunggu Sinkronisasi Stok', 'pending'

    return 'Belum Diproses', 'neutral'


def _payment_method_view(payment):
    names = {
        'bank_transfer': 'Bank Virtual Account',
        'gopay': 'GoPay',
        'qris': 'QRIS',
        'other_qris': 'QRIS',
        'bca_va': 'BCA Virtual Account',
        'bni_va': 'BNI Virtual Account',
        'bri_va': 'BRI Virtual Account'
    }

    method = names.get(payment.payment_type, payment.payment_type or names.get(payment.selected_channel, payment.selected_channel or 'Belum Dipilih'))

    return f'{method} ({payment.bank.upper()})' if payment.bank else method


def _payment_transaction_view(payment):
    if payment.transaction_status == 'capture' and payment.fraud_status not in (None, '', 'accept'):
        return 'Capture (Perlu Verifikasi)', 'pending'

    statuses = {
        'initiating': ('Membuat Pembayaran', 'pending'),
        'pending': ('Pending', 'pending'),
        'settlement': ('Settlement', 'success'),
        'capture': ('Capture', 'success'),
        'authorize': ('Authorize', 'pending'),
        'token_error': ('Token Error', 'failed'),
        'deny': ('Ditolak', 'failed'),
        'failure': ('Gagal', 'failed'),
        'expire': ('Kedaluwarsa', 'failed'),
        'cancel': ('Dibatalkan', 'failed'),
        'refund': ('Refund', 'neutral'),
        'partial_refund': ('Refund Sebagian', 'neutral')
    }

    return statuses.get(payment.transaction_status, (payment.transaction_status or 'Tidak Diketahui', 'neutral'))


@admin_bp.route('/orders', methods=['GET'])
@admin_only
def orders_index():
    view = request.args.get('view', 'orders')
    search = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', '')

    filters = {
        'orders': [
            ('', 'Semua Status'),
            ('pending_payment', 'Menunggu Pembayaran'),
            ('processing', 'Perlu Diproses'),
            ('shipped', 'Dikirim'),
            ('completed', 'Selesai'),
            ('cancelled', 'Dibatalkan'),
            ('stock_issue', 'Perlu Cek Stok')
        ],
        'transactions': [
            ('', 'Semua Status'),
            ('initiating', 'Membuat Pembayaran'),
            ('pending', 'Pending'),
            ('settlement', 'Settlement'),
            ('capture', 'Capture'),
            ('token_error', 'Token Error'),
            ('deny', 'Ditolak'),
            ('failure', 'Gagal'),
            ('expire', 'Kedaluwarsa'),
            ('cancel', 'Dibatalkan'),
            ('refund', 'Refund'),
            ('partial_refund', 'Refund Sebagian')
        ]
    }

    if view not in filters or status not in {value for value, _ in filters[view]}:
        abort(400)

    all_orders = Order.query.order_by(Order.id.desc()).all()
    all_transactions = PaymentTransaction.query.order_by(PaymentTransaction.id.desc()).all()

    metrics = [
        {
            'label': 'Total Pesanan',
            'value': len(all_orders),
            'icon': 'bi-bag',
            'description': 'Seluruh pesanan pelanggan',
            'important': False
        },
        {
            'label': 'Menunggu Pembayaran',
            'value': sum(order.payment_status in ('initiating', 'pending') for order in all_orders),
            'icon': 'bi-clock-history',
            'description': 'Pembayaran belum dikonfirmasi',
            'important': True
        },
        {
            'label': 'Perlu Diproses',
            'value': sum(order.payment_status == 'paid' and order.stock_deducted_at is not None and order.fulfillment_status == 'unfulfilled' for order in all_orders),
            'icon': 'bi-box-seam',
            'description': 'Pesanan siap disiapkan',
            'important': False
        },
        {
            'label': 'Pembayaran Berhasil',
            'value': sum(order.payment_status == 'paid' for order in all_orders),
            'icon': 'bi-check-circle',
            'description': 'Pesanan lunas terverifikasi',
            'important': False
        }
    ]

    orders = []

    for order in all_orders:
        if status == 'pending_payment' and order.payment_status not in ('initiating', 'pending'):
            continue

        if status == 'processing' and not (order.payment_status == 'paid' and order.stock_deducted_at is not None and order.fulfillment_status == 'unfulfilled'):
            continue

        if status in ('shipped', 'completed') and order.fulfillment_status != status:
            continue

        if status == 'cancelled' and order.payment_status not in ('canceled', 'cancelled', 'expired', 'failed'):
            continue

        if status == 'stock_issue' and order.fulfillment_status != 'stock_issue' and not (order.payment_status == 'paid' and order.stock_deducted_at is None):
            continue

        order_name = read_customer_private(order, 'customer_name') or ''
        order_email = read_customer_private(order, 'customer_email') or ''
        if search.casefold() not in f'{order.order_number} {order_name} {order_email}'.casefold():
            continue

        payment_label, payment_style = _order_payment_view(order)
        fulfillment_label, fulfillment_style = _order_fulfillment_view(order)

        orders.append({
            'number': order.order_number,
            'created_at_text': _order_date(order.created_at),
            'customer_name': order_name,
            'customer_email': order_email,
            'item_count': sum(item.quantity for item in order.items),
            'total_text': _rupiah(order.total),
            'payment_label': payment_label,
            'payment_style': payment_style,
            'fulfillment_label': fulfillment_label,
            'fulfillment_style': fulfillment_style
        })

    transactions = []

    for payment in all_transactions:
        order = payment.order

        if status and payment.transaction_status != status:
            continue

        order_email = read_customer_private(order, 'customer_email') or ''
        if search.casefold() not in f'{payment.midtrans_transaction_id or ""} {order.order_number} {order_email}'.casefold():
            continue

        status_label, status_style = _payment_transaction_view(payment)

        transactions.append({
            'transaction_id': payment.midtrans_transaction_id or f'LOCAL-{payment.id}',
            'order_number': order.order_number,
            'created_at_text': _order_date(payment.created_at),
            'method': _payment_method_view(payment),
            'amount_text': _rupiah(payment.gross_amount),
            'status_label': status_label,
            'status_style': status_style
        })

    return render_template(
        'admin/order_list.html',
        view=view,
        search=search,
        status=status,
        status_options=filters[view],
        orders=orders if view == 'orders' else [],
        transactions=transactions if view == 'transactions' else [],
        order_count=len(all_orders),
        transaction_count=len(all_transactions),
        metrics=metrics
    )


def _product_colors(product):
    if not product:
        return []

    groups = {}

    for variant in product.variants:
        key = (variant.color_name, variant.color_hex)
        groups.setdefault(key, {'name': variant.color_name, 'hex': variant.color_hex, 'sizes': []})
        groups[key]['sizes'].append({'size': variant.size, 'stock': variant.stock, 'minimum_stock': variant.minimum_stock})

    return list(groups.values())


def _remove_photos(paths):
    folder = (Path(current_app.static_folder) / 'uploads' / 'products').resolve()

    for path in paths:
        if not path.startswith('uploads/products/'):
            continue

        target = (folder / Path(path).name).resolve()

        if target.parent == folder:
            target.unlink(missing_ok=True)


def _store_photos(files):
    folder = Path(current_app.static_folder) / 'uploads' / 'products'
    folder.mkdir(parents=True, exist_ok=True)
    paths = []

    try:
        for file in files:
            if not file or not file.filename:
                continue

            data = file.read(4 * 1024 * 1024 + 1)

            if not data or len(data) > 4 * 1024 * 1024:
                raise ValueError('Setiap foto maksimal 4 MB.')

            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('Hanya foto JPG, PNG atau WebP yang diterima.')

                if source.width * source.height > 20_000_000:
                    raise ValueError('Resolusi foto terlalu besar.')

                image = ImageOps.exif_transpose(source).convert('RGB')
                filename = f'{uuid.uuid4().hex}.webp'
                image.save(folder / filename, 'WEBP', quality=85)
                paths.append(f'uploads/products/{filename}')

    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        _remove_photos(paths)
        raise

    return paths


def _product_form_page(form, product=None, status=200):
    try:
        raw = json.loads(form.variants_payload.data or '[]')

        if not isinstance(raw, list):
            raw = []

    except (ValueError, TypeError):
        raw = []

    if raw:
        groups = {}

        for variant in raw:
            if not isinstance(variant, dict):
                continue

            key = (variant.get('color_name', ''), variant.get('color_hex', '#333333'))
            groups.setdefault(key, {'name': key[0], 'hex': key[1], 'sizes': []})
            groups[key]['sizes'].append({
                'size': variant.get('size'),
                'stock': variant.get('stock', 0),
                'minimum_stock': variant.get('minimum_stock', 5)
            })

        colors = list(groups.values())
    else:
        colors = _product_colors(product)

    return render_template('admin/product_form.html', form=form, product=product, colors=colors, sizes=SIZES), status


@admin_bp.route('/products')
@admin_only
def products():
    all_products = Product.query.order_by(Product.id.desc()).all()
    q = request.args.get('q', '').strip().casefold()
    category = request.args.get('category', '')
    status = request.args.get('status', '')

    shown = [
        product for product in all_products
        if (not q or q in f'{product.name} {product.sku} {product.subcategory}'.casefold())
        and (not category or product.category == category)
        and (not status or product.status == status)
    ]

    critical = [(product, variant) for product in all_products for variant in product.low_stock_variants]

    return render_template(
        'admin/product_list.html',
        products=shown,
        count=len(all_products),
        safe_count=sum(bool(p.variants) and not p.low_stock_variants for p in all_products),
        low_count=sum(bool(p.low_stock_variants) for p in all_products),
        unit_count=sum(p.total_stock for p in all_products),
        alerts=critical[:3],
        alert_count=len(critical),
        q=q,
        category=category,
        status=status,
        csrf_form=FlaskForm()
    )


@admin_bp.route('/products/create', methods=['GET', 'POST'])
@admin_only
def product_create():
    form = ProductForm()

    if form.validate_on_submit():
        return _save_product(form)

    if request.method == 'POST':
        flash('Periksa kembali data produk dan token keamanan form.', 'danger')

    return _product_form_page(form)


@admin_bp.route('/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_only
def product_edit(product_id):
    product = db.get_or_404(Product, product_id)
    form = ProductForm(obj=product)

    if form.validate_on_submit():
        return _save_product(form, product)

    if request.method == 'POST':
        flash('Data produk belum valid.', 'danger')

    return _product_form_page(form, product)


def _save_product(form, product=None):
    variants, error = parse_variants(form.variants_payload.data)
    intent = request.form.get('intent', 'draft')

    if intent not in ('draft', 'published'):
        abort(400)

    if error:
        flash(error, 'danger')
        return _product_form_page(form, product, 400)

    if intent == 'published' and not variants:
        flash('Tambahkan minimal satu varian ukuran sebelum publish.', 'danger')
        return _product_form_page(form, product, 400)

    sku = form.sku.data.strip().upper()
    duplicate = Product.query.filter_by(sku=sku).first()

    if duplicate and (product is None or duplicate.id != product.id):
        flash('SKU sudah dipakai produk lain.', 'danger')
        return _product_form_page(form, product, 400)

    old_photos = list(product.images or []) if product else []
    selected_for_delete = set(request.form.getlist('remove_image')) & set(old_photos)
    retained = [photo for photo in old_photos if photo not in selected_for_delete]
    files = [file for file in request.files.getlist('photos') if file and file.filename]

    if len(retained) + len(files) > 5:
        flash('Total foto produk maksimal lima.', 'danger')
        return _product_form_page(form, product, 400)

    new_photos = []

    try:
        new_photos = _store_photos(files)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        flash(f'Gagal mengunggah foto: {exc}', 'danger')
        return _product_form_page(form, product, 400)

    photos = retained + new_photos

    if request.form.get('replace_main') and new_photos:
        photos = new_photos + retained

    if intent == 'published' and (not photos or form.price.data <= 0):
        _remove_photos(new_photos)
        flash('Produk live harus memiliki foto dan harga jual lebih dari nol.', 'danger')
        return _product_form_page(form, product, 400)

    if product is None:
        product = Product()
        db.session.add(product)

    for key in ('name', 'category', 'subcategory', 'collection_label', 'price', 'cost', 'description', 'care_title', 'care_text', 'featured'):
        setattr(product, key, getattr(form, key).data)

    product.sku = sku
    product.status = intent
    product.images = photos
    existing = {(variant.color_name.casefold(), variant.size): variant for variant in product.variants}

    for item in variants:
        key = (item['color_name'].casefold(), item['size'])
        variant = existing.pop(key, None)

        if variant is None:
            product.variants.append(ProductVariant(**item))
        else:
            for field, value in item.items():
                setattr(variant, field, value)

    for obsolete in existing.values():
        product.variants.remove(obsolete)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _remove_photos(new_photos)
        flash('Produk gagal disimpan. Periksa SKU atau varian ganda.', 'danger')
        return _product_form_page(form, product, 400)

    _remove_photos(selected_for_delete)
    flash('Produk berhasil disimpan.', 'success')

    return redirect(url_for('admin.products'))


@admin_bp.route('/products/<int:product_id>/stock', methods=['GET', 'POST'])
@admin_only
def product_stock(product_id):
    product = db.get_or_404(Product, product_id)
    csrf_form = FlaskForm()

    if request.method == 'POST':
        if not csrf_form.validate_on_submit():
            flash('Token keamanan tidak valid.', 'danger')
        else:
            amounts = {}

            for variant in product.variants:
                raw = request.form.get(f'add_{variant.id}', '0')

                if not raw.isdigit() or len(raw) > 6:
                    flash('Jumlah restock harus angka 0 sampai 999999.', 'danger')
                    break

                amounts[variant.id] = int(raw)
            else:
                if sum(amounts.values()) == 0:
                    flash('Isi minimal satu jumlah restock.', 'warning')
                else:
                    for variant in product.variants:
                        variant.stock += amounts[variant.id]

                    db.session.commit()
                    flash('Stok berhasil ditambahkan.', 'success')

                    return redirect(url_for('admin.products'))

    return render_template('admin/product_stock.html', product=product, csrf_form=csrf_form)


@admin_bp.route('/products/<int:product_id>/delete', methods=['POST'])
@admin_only
def product_delete(product_id):
    if not FlaskForm().validate_on_submit():
        abort(400)

    product = db.get_or_404(Product, product_id)
    photos = list(product.images or [])

    db.session.delete(product)
    db.session.commit()
    _remove_photos(photos)

    flash('Produk berhasil dihapus.', 'success')

    return redirect(url_for('admin.products'))


def _account_verified(user):
    return getattr(user, 'email_verified', None)


def _customer_rows():
    users = User.query.filter_by(role='customer').order_by(User.id.desc()).all()
    result = []

    for user in users:
        profile = db.session.get(CustomerProfile, user.id)

        result.append({
            'id': user.id,
            'name': (profile.display_name if profile else None) or user.name or user.email.split('@')[0],
            'email': user.email,
            'phone': read_customer_private(profile, 'phone') if profile else None,
            'gender': profile.gender if profile else None,
            'birth_date': read_customer_private(profile, 'birth_date') if profile else None,
            'avatar': profile.avatar_filename if profile else None,
            'verified': _account_verified(user)
        })

    return result


@admin_bp.route('/users')
@admin_only
def users_index():
    all_users = _customer_rows()
    q = request.args.get('q', '').strip().casefold()
    status = request.args.get('status', '')
    gender = request.args.get('gender', '')
    sort = request.args.get('sort', 'newest')

    if status not in ('', 'verified', 'unverified') or gender not in ('', 'men', 'women', 'other') or sort not in ('newest', 'oldest', 'name'):
        abort(400)

    rows = []

    for item in all_users:
        search_value = ' '.join((item['name'], item['email'], item['phone'] or '')).casefold()

        if q and q not in search_value:
            continue

        if status == 'verified' and item['verified'] is not True:
            continue

        if status == 'unverified' and item['verified'] is not False:
            continue

        if gender and item['gender'] != gender:
            continue

        rows.append(item)

    if sort == 'oldest':
        rows.reverse()
    elif sort == 'name':
        rows.sort(key=lambda item: item['name'].casefold())

    selected_id = request.args.get('selected', type=int)
    selected = next((item for item in rows if item['id'] == selected_id), None)

    if selected is None and rows:
        selected = rows[0]

    return render_template(
        'admin/user_list.html',
        rows=rows,
        selected=selected,
        total_users=len(all_users),
        verified_users=sum(item['verified'] is True for item in all_users),
        q=q,
        status=status,
        gender=gender,
        sort=sort
    )

@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@admin_only
def user_edit(user_id):
    user = db.get_or_404(User, user_id)

    if user.role != 'customer':
        abort(403)

    profile = db.session.get(CustomerProfile, user.id)

    if request.method == 'POST':
        name = request.form.get('display_name', '').strip()
        phone = request.form.get('phone', '').strip()
        gender = request.form.get('gender', '').strip()
        birth_text = request.form.get('birth_date', '').strip()

        if not 2 <= len(name) <= 100:
            flash('Nama harus terdiri dari 2-100 karakter.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user.id))

        if len(phone) > 25 or any(char not in '0123456789+ -()' for char in phone):
            flash('Nomor WhatsApp tidak valid.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user.id))

        if gender not in ('', 'men', 'women', 'other'):
            abort(400)

        try:
            birth_date = date.fromisoformat(birth_text) if birth_text else None
        except ValueError:
            flash('Tanggal lahir tidak valid.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user.id))

        if birth_date and (birth_date > date.today() or birth_date.year < 1900):
            flash('Tanggal lahir tidak valid.', 'danger')
            return redirect(url_for('admin.user_edit', user_id=user.id))

        if profile is None:
            profile = CustomerProfile(user_id=user.id)
            db.session.add(profile)

        profile.display_name = name
        profile.phone = phone or None
        profile.gender = gender or None
        profile.birth_date = birth_date
        user.name = name
        db.session.commit()

        flash('Data customer berhasil diperbarui.', 'success')
        return redirect(url_for('admin.user_edit', user_id=user.id))

    account = next(item for item in _customer_rows() if item['id'] == user.id)

    customer_orders = Order.query.filter_by(user_id=user.id).order_by(Order.id.desc()).all()
    paid_orders = [order for order in customer_orders if order.payment_status == 'paid']

    order_stats = {
        'total_orders': len(customer_orders),
        'paid_orders': len(paid_orders),
        'total_spent': sum(int(order.total or 0) for order in paid_orders),
        'items_bought': sum(int(item.quantity or 0) for order in paid_orders for item in order.items)
    }

    saved = CustomerAddress.query.filter_by(user_id=user.id).first()
    saved_address = None

    if saved:
        saved_address = {
            'recipient_name': read_customer_private(saved, 'recipient_name'),
            'phone': read_customer_private(saved, 'phone'),
            'full_address': read_customer_private(saved, 'full_address'),
            'city': saved.city,
            'postal_code': saved.postal_code or ''
        }

    return render_template(
        'admin/user_edit.html',
        account=account,
        customer_orders=customer_orders,
        order_stats=order_stats,
        saved_address=saved_address,
        rupiah=_rupiah,
        format_order_date=_order_date
    )


def _csv_safe(value):
    text = str(value or '')

    if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text

    return text


@admin_bp.route('/users/export')
@admin_only
def users_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Nama', 'Email', 'WhatsApp', 'Gender', 'Status Email'])

    for item in _customer_rows():
        if item['verified'] is True:
            email_status = 'Terverifikasi'
        elif item['verified'] is False:
            email_status = 'Belum terverifikasi'
        else:
            email_status = 'Verifikasi belum tersedia'

        writer.writerow([
            item['id'],
            _csv_safe(item['name']),
            _csv_safe(item['email']),
            _csv_safe(item['phone']),
            _csv_safe(item['gender']),
            email_status
        ])

    response = make_response('\ufeff' + output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=customer_kuro.csv'

    return response


from . import order_management
from . import review_management
from . import dashboard_data
#Import ini harus berada setelah seluruh definisi route lama, bukan di bagian atas. Ini memungkinkan modul pengelolaan pesanan mendaftarkan route baru pada blueprint Admin sebelum Flask menjalankan app.register_blueprint()