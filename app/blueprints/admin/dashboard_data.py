from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from flask import abort, render_template, request, send_file
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.order import Order
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.services.customer_encryption_reader import read_customer_private

from .routes import admin_bp, admin_only


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


# ==========================================================
# FR-010 - LAPORAN PENJUALAN
# ==========================================================

def _report_dates():
    today = datetime.now(WIB).date()

    try:
        start = date.fromisoformat(
            request.args.get('start') or today.replace(day=1).isoformat()
        )
        end = date.fromisoformat(
            request.args.get('end') or today.isoformat()
        )
    except ValueError:
        abort(400, description='Format tanggal tidak valid.')

    if start > end or (end - start).days > 366:
        abort(400, description='Periode laporan maksimal 367 hari.')

    start_at = datetime.combine(
        start, datetime.min.time(), tzinfo=WIB
    )
    end_at = datetime.combine(
        end + timedelta(days=1), datetime.min.time(), tzinfo=WIB
    )

    return (
        start,
        end,
        start_at.astimezone(timezone.utc).replace(tzinfo=None),
        end_at.astimezone(timezone.utc).replace(tzinfo=None)
    )


def _sales_data():
    start, end, start_at, end_at = _report_dates()

    orders = (
        Order.query
        .options(selectinload(Order.items))
        .filter(
            Order.payment_status == 'paid',
            Order.paid_at >= start_at,
            Order.paid_at < end_at
        )
        .order_by(Order.paid_at.desc(), Order.id.desc())
        .all()
    )

    rows = [
        {
            'number': order.order_number,
            'date': format_date(order.paid_at),
            'customer': (
                read_customer_private(order, 'customer_name')
                or f'Customer #{order.user_id}'
            ),
            'items': sum(int(item.quantity or 0) for item in order.items),
            'total': int(order.total or 0)
        }
        for order in orders
    ]

    revenue = sum(row['total'] for row in rows)

    summary = {
        'revenue': revenue,
        'orders': len(rows),
        'items': sum(row['items'] for row in rows),
        'average': round(revenue / len(rows)) if rows else 0
    }

    return start, end, rows, summary


@admin_bp.route('/reports/sales')
@admin_only
def sales_report():
    start, end, rows, summary = _sales_data()

    return render_template(
        'admin/sales_report.html',
        start=start.isoformat(),
        end=end.isoformat(),
        rows=rows,
        summary=summary,
        rupiah=rupiah
    )


def _safe_excel(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


@admin_bp.route('/reports/sales/export/<file_type>')
@admin_only
def sales_report_export(file_type):
    if file_type not in ('xlsx', 'pdf'):
        abort(404)

    start, end, rows, summary = _sales_data()
    buffer = BytesIO()
    filename = f'laporan_penjualan_{start}_{end}.{file_type}'

    if file_type == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Laporan Penjualan'

        sheet.merge_cells('A1:E1')
        sheet['A1'] = 'LAPORAN PENJUALAN KURO WEAR'
        sheet['A1'].font = Font(size=15, bold=True)

        sheet.merge_cells('A2:E2')
        sheet['A2'] = f'Periode: {start} s.d. {end} (WIB)'

        headers = [
            'Tanggal Bayar', 'Nomor Pesanan',
            'Pelanggan', 'Produk Terjual', 'Total (Rp)'
        ]

        sheet.append([])
        sheet.append(headers)

        for cell in sheet[4]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='242424')
            cell.alignment = Alignment(horizontal='center')

        for row in rows:
            sheet.append([
                row['date'],
                _safe_excel(row['number']),
                _safe_excel(row['customer']),
                row['items'],
                row['total']
            ])
            sheet.cell(sheet.max_row, 5).number_format = '#,##0'

        sheet.append([])
        sheet.append([
            'TOTAL', f"{summary['orders']} pesanan",
            '', summary['items'], summary['revenue']
        ])

        for cell in sheet[sheet.max_row]:
            cell.font = Font(bold=True)

        sheet.cell(sheet.max_row, 5).number_format = '#,##0'

        for column, width in {
            'A': 23, 'B': 25, 'C': 32, 'D': 19, 'E': 20
        }.items():
            sheet.column_dimensions[column].width = width

        sheet.freeze_panes = 'A5'
        sheet.auto_filter.ref = f'A4:E{max(4, len(rows) + 4)}'

        workbook.save(buffer)
        mime = (
            'application/vnd.openxmlformats-officedocument.'
            'spreadsheetml.sheet'
        )

    else:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            LongTable, Paragraph, SimpleDocTemplate,
            Spacer, TableStyle
        )

        styles = getSampleStyleSheet()
        document = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=28,
            rightMargin=28,
            topMargin=30,
            bottomMargin=30
        )

        content = [
            Paragraph('LAPORAN PENJUALAN KURO WEAR', styles['Title']),
            Paragraph(
                f'Periode: {start} sampai {end} (WIB)',
                styles['Normal']
            ),
            Spacer(1, 15),
            Paragraph(
                f"Total Pendapatan: {rupiah(summary['revenue'])} | "
                f"Pesanan Lunas: {summary['orders']} | "
                f"Produk Terjual: {summary['items']} | "
                f"Rata-rata: {rupiah(summary['average'])}",
                styles['Normal']
            ),
            Spacer(1, 18)
        ]

        data = [[
            'Tanggal Bayar', 'Nomor Pesanan',
            'Pelanggan', 'Produk', 'Total'
        ]]

        for row in rows:
            data.append([
                row['date'],
                Paragraph(escape(str(row['number'])), styles['Normal']),
                Paragraph(escape(str(row['customer'])), styles['Normal']),
                str(row['items']),
                rupiah(row['total'])
            ])

        if not rows:
            data.append(['-', '-', 'Tidak ada transaksi lunas', '0', '-'])

        table = LongTable(
            data,
            colWidths=[125, 130, 230, 60, 135],
            repeatRows=1
        )
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#242424')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 9),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
                colors.white, colors.HexColor('#F5F5F5')
            ])
        ]))
        content.append(table)
        document.build(content)
        mime = 'application/pdf'

    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=mime
    )