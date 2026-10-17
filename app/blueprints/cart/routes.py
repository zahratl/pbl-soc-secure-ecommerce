import base64
import json
import urllib.request
import urllib.error
import uuid
from datetime import datetime
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    current_app
)
from flask_login import current_user
from app.extensions import db
from app.models.product import Product
from app.models.cart import CartItem
from app.models.wishlist import WishlistItem
from app.models.customer_profile import CustomerProfile

cart_bp = Blueprint("cart", __name__)

# ==========================================
# HELPER
# ==========================================

def rupiah(amount):
    value = int(amount or 0)
    return "Rp " + f"{value:,}".replace(",", ".")


def find_product_variant(product, color_name, size):
    """
    Mencocokkan warna + ukuran dengan varian
    yang benar-benar dibuat oleh Admin.
    """

    if product is None:
        return None

    wanted_color = (color_name or "").strip().casefold()
    wanted_size = (size or "").strip().upper()

    for variant in product.variants:
        color = (variant.color_name or "").strip().casefold()
        variant_size = (variant.size or "").strip().upper()

        if color == wanted_color and variant_size == wanted_size:
            return variant

    return None


def customer_required():
    """
    Mengembalikan respons jika belum login
    atau akun bukan customer.
    """

    if not current_user.is_authenticated:
        flash(
            "Silakan login terlebih dahulu untuk menggunakan keranjang.",
            "warning"
        )
        return redirect(url_for("auth.login"))

    if current_user.role != "customer":
        flash(
            "Keranjang belanja hanya tersedia untuk pelanggan.",
            "warning"
        )
        return redirect(url_for("shop.index"))

    return None


def get_own_cart_item(item_id):
    """
    Wajib filter user_id agar seorang customer
    tidak bisa mengubah keranjang orang lain.
    """

    return CartItem.query.filter_by(
        id=item_id,
        user_id=current_user.id
    ).first_or_404()


# ==========================================
# HALAMAN KERANJANG
# ==========================================

@cart_bp.route("/", methods=["GET"])
def view_cart():

    items = []

    if (
        current_user.is_authenticated
        and current_user.role == "customer"
    ):

        records = (
            CartItem.query
            .filter_by(user_id=current_user.id)
            .order_by(CartItem.id.desc())
            .all()
        )

        for item in records:
            product = item.product

            if product is None:
                items.append({
                    "id": item.id,
                    "name": "Produk sudah tidak tersedia",
                    "category": "",
                    "image": "",
                    "price": 0,
                    "price_text": rupiah(0),
                    "quantity": item.quantity,
                    "line_total": 0,
                    "line_total_text": rupiah(0),
                    "color": item.color_name,
                    "size": item.size,
                    "stock": 0,
                    "available": False,
                    "product_id": None,
                    "collection": ""
                })
                continue

            variant = find_product_variant(
                product,
                item.color_name,
                item.size
            )

            stock = (
                max(0, int(variant.stock or 0))
                if variant else 0
            )

            available = (
                product.status == "published"
                and variant is not None
                and stock >= item.quantity
                and stock > 0
            )

            paths = product.images or []
            image = (
                url_for("static", filename=paths[0])
                if paths else ""
            )

            price = int(product.price or 0)

            items.append({
                "id": item.id,
                "product_id": product.id,
                "name": product.name,
                "category": product.subcategory or "",
                "image": image,
                "price": price,
                "price_text": rupiah(price),
                "quantity": item.quantity,
                "line_total": price * item.quantity,
                "line_total_text": rupiah(price * item.quantity),
                "color": item.color_name,
                "size": item.size,
                "stock": stock,
                "available": available,
                "collection": product.collection_label or ""
            })

    total_pieces = sum(
        item["quantity"] for item in items
    )

    return render_template(
        "cart/cart.html",
        items=items,
        total_pieces=total_pieces,
        rupiah=rupiah
    )


# ==========================================
# TAMBAH PRODUK KE KERANJANG
# ==========================================

@cart_bp.route("/add", methods=["POST"])
def add_item():

    access_error = customer_required()
    if access_error:
        return access_error

    try:
        product_id = int(request.form.get("product_id", "0"))
        quantity = int(request.form.get("quantity", "1"))
    except (ValueError, TypeError):
        flash("Data produk tidak valid.", "danger")
        return redirect(url_for("shop.catalog_all"))

    if quantity < 1 or quantity > 100:
        flash("Jumlah produk tidak valid.", "danger")
        return redirect(url_for("shop.catalog_all"))

    product = db.session.get(Product, product_id)

    if product is None or product.status != "published":
        flash("Produk tidak tersedia.", "warning")
        return redirect(url_for("shop.catalog_all"))

    color = request.form.get("color_name", "").strip()
    size = request.form.get("size", "").strip()

    variant = find_product_variant(
        product,
        color,
        size
    )

    if variant is None:
        flash(
            "Silakan pilih kombinasi warna dan ukuran yang tersedia.",
            "warning"
        )
        return redirect(
            url_for("shop.product_detail", product_id=product.id)
        )

    stock = max(0, int(variant.stock or 0))

    existing_item = CartItem.query.filter_by(
        user_id=current_user.id,
        product_id=product.id,
        color_name=variant.color_name,
        size=variant.size
    ).first()

    quantity_already_added = (
        existing_item.quantity if existing_item else 0
    )

    if quantity_already_added + quantity > stock:
        flash(
            "Jumlah yang diminta melebihi stok varian yang tersedia.",
            "warning"
        )
        return redirect(
            url_for("shop.product_detail", product_id=product.id)
        )

    if existing_item:
        existing_item.quantity += quantity
    else:
        db.session.add(
            CartItem(
                user_id=current_user.id,
                product_id=product.id,
                color_name=variant.color_name,
                size=variant.size,
                quantity=quantity
            )
        )

    db.session.commit()

    flash(
        "Produk berhasil ditambahkan ke keranjang.",
        "success"
    )

    return redirect(url_for("cart.view_cart"))


# ==========================================
# TAMBAH / KURANGI KUANTITAS
# ==========================================

@cart_bp.route(
    "/item/<int:item_id>/quantity",
    methods=["POST"]
)
def update_quantity(item_id):

    access_error = customer_required()
    if access_error:
        return access_error

    item = get_own_cart_item(item_id)

    try:
        delta = int(request.form.get("delta", "0"))
    except ValueError:
        delta = 0

    if delta not in (-1, 1):
        flash("Perubahan jumlah tidak valid.", "warning")
        return redirect(url_for("cart.view_cart"))

    new_quantity = item.quantity + delta

    if new_quantity < 1:
        return redirect(url_for("cart.view_cart"))

    variant = find_product_variant(
        item.product,
        item.color_name,
        item.size
    )

    available_stock = (
        max(0, int(variant.stock or 0))
        if variant else 0
    )

    if (
        item.product is None
        or item.product.status != "published"
        or new_quantity > available_stock
    ):
        flash(
            "Jumlah barang melebihi stok yang tersedia.",
            "warning"
        )
        return redirect(url_for("cart.view_cart"))

    item.quantity = new_quantity
    db.session.commit()

    return redirect(url_for("cart.view_cart"))


# ==========================================
# HAPUS SATU BARANG
# ==========================================

@cart_bp.route(
    "/item/<int:item_id>/remove",
    methods=["POST"]
)
def remove_item(item_id):

    access_error = customer_required()
    if access_error:
        return access_error

    item = get_own_cart_item(item_id)

    db.session.delete(item)
    db.session.commit()

    flash(
        "Barang berhasil dihapus dari keranjang.",
        "success"
    )

    return redirect(url_for("cart.view_cart"))


# ==========================================
# HAPUS BARANG YANG DIPILIH
# ==========================================

@cart_bp.route(
    "/remove-selected",
    methods=["POST"]
)
def remove_selected():

    access_error = customer_required()
    if access_error:
        return access_error

    raw_ids = request.form.getlist("item_ids")

    item_ids = [
        int(value)
        for value in raw_ids
        if value.isdigit()
    ][:100]

    if not item_ids:
        flash("Pilih barang yang ingin dihapus.", "warning")
        return redirect(url_for("cart.view_cart"))

    own_items = CartItem.query.filter(
        CartItem.user_id == current_user.id,
        CartItem.id.in_(item_ids)
    ).all()

    for item in own_items:
        db.session.delete(item)

    db.session.commit()

    flash(
        "Barang yang dipilih berhasil dihapus.",
        "success"
    )

    return redirect(url_for("cart.view_cart"))


# ==========================================
# PINDAHKAN KE WISHLIST
# ==========================================

@cart_bp.route(
    "/item/<int:item_id>/move-to-wishlist",
    methods=["POST"]
)
def move_to_wishlist(item_id):

    access_error = customer_required()
    if access_error:
        return access_error

    item = get_own_cart_item(item_id)
    product = item.product

    if product is None or product.status != "published":
        flash(
            "Produk sudah tidak tersedia untuk Wishlist.",
            "warning"
        )
        return redirect(url_for("cart.view_cart"))

    existing_wishlist = db.session.get(
        WishlistItem,
        (current_user.id, product.id)
    )

    if existing_wishlist is None:
        db.session.add(
            WishlistItem(
                user_id=current_user.id,
                product_id=product.id
            )
        )

    db.session.delete(item)
    db.session.commit()

    flash(
        "Produk dipindahkan ke Wishlist.",
        "success"
    )

    return redirect(url_for("cart.view_cart"))


# ==========================================
# MIDTRANS SNAP SANDBOX INTEGRATION
# ==========================================

def request_midtrans_snap_token(order_id, gross_amount, items, customer, enabled_payments=None):
    """
    Meminta Snap Token resmi ke Midtrans Sandbox API.
    Fokus utama channel pembayaran yang diminta user:
    GoPay, QRIS, BRI VA, BNI VA, BCA VA.
    """
    server_key = current_app.config.get("MIDTRANS_SERVER_KEY", "").strip()
    if not server_key:
        return None, "MIDTRANS_SERVER_KEY belum dikonfigurasi di file .env."

    is_production = current_app.config.get("MIDTRANS_IS_PRODUCTION", False)
    endpoint = (
        "https://app.midtrans.com/snap/v1/transactions"
        if is_production
        else "https://app.sandbox.midtrans.com/snap/v1/transactions"
    )

    if enabled_payments is None:
        # Prioritas utama sesuai permintaan: GoPay, QRIS, BRI VA, BNI VA, BCA VA
        enabled_payments = ["gopay", "qris", "bri_va", "bni_va", "bca_va"]

    item_details = []
    for it in items:
        clean_name = (it.get("name") or "Produk Kuro Wear")[:50]
        item_details.append({
            "id": str(it.get("id") or "KW-ITEM")[:50],
            "price": int(it.get("price") or 0),
            "quantity": int(it.get("quantity") or 1),
            "name": clean_name
        })

    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": int(gross_amount)
        },
        "item_details": item_details,
        "customer_details": {
            "first_name": customer.get("first_name", "Pelanggan"),
            "last_name": customer.get("last_name", ""),
            "email": customer.get("email", "customer@kurowear.local"),
            "phone": customer.get("phone", "081234567890"),
            "shipping_address": {
                "first_name": customer.get("first_name", "Pelanggan"),
                "phone": customer.get("phone", "081234567890"),
                "address": customer.get("address", "")
            }
        },
        "enabled_payments": enabled_payments
    }

    auth_str = base64.b64encode(f"{server_key}:".encode("utf-8")).decode("utf-8")
    req_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=req_data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_str}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            return res_json.get("token"), None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_msgs = err_json.get("error_messages", [str(e)])
            if isinstance(err_msgs, list):
                err_text = ", ".join(err_msgs)
            else:
                err_text = str(err_msgs)
        except Exception:
            err_text = err_body or str(e)
        return None, f"Midtrans API Error: {err_text}"
    except Exception as e:
        return None, f"Koneksi Midtrans: {str(e)}"


# ==========================================
# HALAMAN CHECKOUT & VERIFIKASI PEMBAYARAN
# ==========================================

@cart_bp.route("/checkout", methods=["GET"])
def checkout():
    """
    Halaman Checkout & Verifikasi Pembayaran Midtrans Sandbox.
    Memuat produk asli dan harga dari keranjang belanja user.
    """
    access_error = customer_required()
    if access_error:
        return access_error

    items_arg = request.args.get("items", "").strip()
    selected_ids = [int(x) for x in items_arg.split(",") if x.isdigit()]

    query = CartItem.query.filter_by(user_id=current_user.id)
    if selected_ids:
        query = query.filter(CartItem.id.in_(selected_ids))

    cart_records = query.order_by(CartItem.id.desc()).all()
    if not cart_records:
        flash("Keranjang belanja Anda masih kosong atau belum ada produk yang dipilih.", "warning")
        return redirect(url_for("cart.view_cart"))

    checkout_items = []
    for item in cart_records:
        product = item.product
        if product is None:
            continue

        variant = find_product_variant(product, item.color_name, item.size)
        stock = max(0, int(variant.stock or 0)) if variant else 0
        if product.status != "published" or variant is None or stock < 1:
            continue

        price = int(product.price or 0)
        quantity = min(item.quantity, stock)
        line_total = price * quantity

        paths = product.images or []
        image = ""
        if paths:
            first_path = paths[0]
            if first_path.startswith("http://") or first_path.startswith("https://"):
                image = first_path
            else:
                image = url_for("static", filename=first_path)

        checkout_items.append({
            "id": item.id,
            "product_id": product.id,
            "name": product.name,
            "category": product.subcategory or "",
            "image": image,
            "price": price,
            "price_text": rupiah(price),
            "quantity": quantity,
            "line_total": line_total,
            "line_total_text": rupiah(line_total),
            "color": item.color_name,
            "size": item.size,
            "stock": stock
        })

    if not checkout_items:
        flash("Produk yang dipilih sedang tidak tersedia atau stok habis.", "warning")
        return redirect(url_for("cart.view_cart"))

    subtotal = sum(it["line_total"] for it in checkout_items)
    shipping_cost = 0  # Promo Bebas Ongkir Kuro Wear
    grand_total = subtotal + shipping_cost

    # Alamat pengiriman: jika user belum pernah menambahkan alamat, dikosongkan (None)
    address = session.get("shipping_address")

    # Midtrans Client Key untuk Snap JS
    midtrans_client_key = current_app.config.get("MIDTRANS_CLIENT_KEY", "").strip()

    return render_template(
        "cart/checkout.html",
        items=checkout_items,
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        grand_total=grand_total,
        address=address,
        selected_item_ids=",".join(str(it["id"]) for it in checkout_items),
        midtrans_client_key=midtrans_client_key,
        rupiah=rupiah
    )


# ==========================================
# SIMPAN / UPDATE ALAMAT PENGIRIMAN
# ==========================================

@cart_bp.route("/save-address", methods=["POST"])
def save_address():
    """
    Endpoint AJAX untuk menyimpan alamat pengiriman sementara / sesi user.
    """
    access_error = customer_required()
    if access_error:
        return jsonify({"status": "error", "message": "Silakan login terlebih dahulu."}), 401

    data = request.get_json(silent=True) or request.form
    recipient_name = (data.get("recipient_name") or "").strip()
    phone = (data.get("phone") or "").strip()
    city = (data.get("city") or "").strip()
    full_address = (data.get("full_address") or "").strip()

    if not recipient_name or not phone or not full_address:
        return jsonify({
            "status": "error",
            "message": "Nama penerima, nomor telepon/WhatsApp, dan alamat lengkap wajib diisi."
        }), 400

    address_data = {
        "recipient_name": recipient_name,
        "phone": phone,
        "city": city or "Indonesia",
        "full_address": full_address
    }
    session["shipping_address"] = address_data

    # Sinkronisasi nomor HP ke CustomerProfile jika masih kosong
    profile = db.session.get(CustomerProfile, current_user.id)
    if profile and not profile.phone:
        profile.phone = phone
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify({
        "status": "success",
        "message": "Alamat pengiriman berhasil disimpan.",
        "address": address_data
    })


# ==========================================
# GENERATE MIDTRANS SNAP TOKEN
# ==========================================

@cart_bp.route("/create-snap-token", methods=["POST"])
def create_snap_token():
    """
    Membuat Snap Token Midtrans Sandbox resmi untuk checkout saat ini.
    Mendukung channel GoPay, QRIS, BRI VA, BNI VA, dan BCA VA.
    """
    access_error = customer_required()
    if access_error:
        return jsonify({"status": "error", "message": "Silakan login terlebih dahulu."}), 401

    data = request.get_json(silent=True) or {}
    items_str = (data.get("items") or "").strip() or request.args.get("items", "").strip()
    selected_ids = [int(x) for x in items_str.split(",") if x.isdigit()]

    query = CartItem.query.filter_by(user_id=current_user.id)
    if selected_ids:
        query = query.filter(CartItem.id.in_(selected_ids))

    cart_records = query.all()
    if not cart_records:
        return jsonify({"status": "error", "message": "Keranjang belanja kosong."}), 400

    checkout_items = []
    for item in cart_records:
        product = item.product
        if not product or product.status != "published":
            continue
        variant = find_product_variant(product, item.color_name, item.size)
        if not variant or (variant.stock or 0) < 1:
            continue
        price = int(product.price or 0)
        qty = item.quantity
        name_label = f"{product.name} ({item.color_name}, {item.size})"
        checkout_items.append({
            "id": f"PRD-{product.id}-{item.id}",
            "name": name_label[:50],
            "price": price,
            "quantity": qty,
            "line_total": price * qty
        })

    if not checkout_items:
        return jsonify({"status": "error", "message": "Produk tidak tersedia untuk checkout."}), 400

    gross_amount = sum(it["line_total"] for it in checkout_items)

    address_info = session.get("shipping_address") or {}
    recipient_name = address_info.get("recipient_name") or current_user.name or "Pelanggan"
    phone = address_info.get("phone") or "081234567890"
    full_addr = address_info.get("full_address") or ""

    name_parts = recipient_name.split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    order_id = f"KW-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"

    customer_dict = {
        "first_name": first_name,
        "last_name": last_name,
        "email": current_user.email,
        "phone": phone,
        "address": full_addr
    }

    # Prioritas channels sesuai permintaan user: GoPay, QRIS, BRI VA, BNI VA, BCA VA
    enabled_payments = ["gopay", "qris", "bri_va", "bni_va", "bca_va"]

    token, err_msg = request_midtrans_snap_token(
        order_id=order_id,
        gross_amount=gross_amount,
        items=checkout_items,
        customer=customer_dict,
        enabled_payments=enabled_payments
    )

    client_key = current_app.config.get("MIDTRANS_CLIENT_KEY", "").strip()

    if token:
        return jsonify({
            "status": "success",
            "snap_token": token,
            "order_id": order_id,
            "gross_amount": gross_amount,
            "gross_amount_text": rupiah(gross_amount),
            "client_key": client_key
        })
    else:
        # Fallback mode jika Midtrans Sandbox API tidak merespons (misal masalah DNS atau key)
        return jsonify({
            "status": "fallback",
            "order_id": order_id,
            "gross_amount": gross_amount,
            "gross_amount_text": rupiah(gross_amount),
            "error_detail": err_msg,
            "message": "Menggunakan simulasi pembayaran Sandbox langsung."
        })