#Modul membaca data langsung dari tabel orders, products, product_variants, dan users. Tidak memakai angka contoh dan tidak membuat transaksi baru.
from datetime import datetime, timedelta, timezone

from flask import request
from sqlalchemy import func

from app.extensions import db
from app.models.order import Order
from app.models.product import Product, ProductVariant
from app.models.user import User

from .routes import admin_bp


WIB = timezone(timedelta(hours=7))


def rupiah(amount):
    return 'Rp ' + f'{int(amount or 0):,}'.replace(',', '.')


def format_date(value):
    if value is None:
        return '-'

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(WIB).strftime('%d/%m/%Y %H:%M WIB')


def current_month_bounds():
    now = datetime.now(WIB)
    start = datetime(now.year, now.month, 1, tzinfo=WIB)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)

    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        next_month.astimezone(timezone.utc).replace(tzinfo=None)
    )


def order_status(order):
    if order.payment_status != 'paid':
        return {
            'initiating': 'Menyiapkan Pembayaran',
            'pending': 'Menunggu Pembayaran',
            'expired': 'Kedaluwarsa',
            'failed': 'Gagal',
            'canceled': 'Dibatalkan',
            'token_error': 'Gagal Membuat Pembayaran',
            'refunded': 'Dana Dikembalikan'
        }.get(order.payment_status, 'Periksa Pembayaran'), 'warning'

    if order.fulfillment_status == 'stock_issue' or not order.stock_deducted_at:
        return 'Perlu Cek Stok', 'warning'

    return {
        'unfulfilled': ('Perlu Diproses', 'pending'),
        'processing': ('Sedang Dikemas', 'progress'),
        'shipped': ('Dikirim', 'progress'),
        'completed': ('Selesai', 'success')
    }.get(order.fulfillment_status, ('Dalam Proses', 'progress'))


@admin_bp.context_processor
def inject_dashboard_data():
    if request.endpoint != 'admin.dashboard':
        return {}

    month_start, month_end = current_month_bounds()

    monthly_revenue = db.session.query(
        func.coalesce(func.sum(Order.total), 0)
    ).filter(
        Order.payment_status == 'paid',
        Order.paid_at >= month_start,
        Order.paid_at < month_end
    ).scalar()

    order_count = Order.query.count()

    paid_order_count = Order.query.filter_by(
        payment_status='paid'
    ).count()

    pending_payment_count = Order.query.filter(
        Order.payment_status.in_(['pending', 'initiating'])
    ).count()

    ready_to_process_count = Order.query.filter(
        Order.payment_status == 'paid',
        Order.fulfillment_status == 'unfulfilled',
        Order.stock_deducted_at.is_not(None)
    ).count()

    customer_count = User.query.filter_by(
        role='customer'
    ).count()

    products = Product.query.filter_by(
        status='published'
    ).all()

    catalog_count = len(products)
    catalog_safe = sum(
        bool(product.variants) and not product.low_stock_variants
        for product in products
    )
    catalog_low = sum(
        bool(product.low_stock_variants)
        for product in products
    )

    low_stock_query = db.session.query(
        ProductVariant, Product
    ).join(
        Product,
        ProductVariant.product_id == Product.id
    ).filter(
        Product.status == 'published',
        ProductVariant.stock <= ProductVariant.minimum_stock
    )

    stock_alert_count = low_stock_query.count()

    stock_alerts = low_stock_query.order_by(
        ProductVariant.stock.asc(),
        Product.name.asc()
    ).limit(6).all()

    recent_orders = Order.query.order_by(
        Order.created_at.desc(),
        Order.id.desc()
    ).limit(5).all()

    return {
        'monthly_revenue': int(monthly_revenue or 0),
        'order_count_dashboard': order_count,
        'paid_order_count_dashboard': paid_order_count,
        'pending_payment_count_dashboard': pending_payment_count,
        'ready_to_process_count': ready_to_process_count,
        'customer_count': customer_count,
        'catalog_count': catalog_count,
        'catalog_safe': catalog_safe,
        'catalog_low': catalog_low,
        'stock_alert_count': stock_alert_count,
        'stock_alerts': stock_alerts,
        'recent_orders': recent_orders,
        'dashboard_rupiah': rupiah,
        'dashboard_date': format_date,
        'dashboard_order_status': order_status
    }