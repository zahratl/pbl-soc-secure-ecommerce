from flask import (
    Blueprint,
    render_template,
    url_for,
    jsonify,
    abort
)
from flask_login import current_user, login_required
from app.extensions import db
from app.models.product import Product
from app.models.wishlist import WishlistItem
from flask import request, redirect
from sqlalchemy import or_

shop_bp = Blueprint("shop", __name__)

# ==========================================
# WISHLIST COUNT GLOBAL
# Untuk seluruh halaman KURO WEAR
# ==========================================

@shop_bp.app_context_processor
def inject_navbar_wishlist_count():

    wishlist_count = 0

    if (
        current_user.is_authenticated
        and current_user.role == "customer"
    ):

        wishlist_count = (
            db.session.query(WishlistItem)
            .join(
                Product,
                WishlistItem.product_id == Product.id
            )
            .filter(
                WishlistItem.user_id == current_user.id,
                Product.status == "published"
            )
            .count()
        )

    return {
        "wishlist_count": wishlist_count
    }

# ==========================================
# DATA PRODUK DARI DATABASE
# ==========================================

def published_products():
    return (
        Product.query
        .filter_by(status="published")
        .order_by(Product.id.desc())
        .all()
    )


def current_favorite_ids():
    if not current_user.is_authenticated:
        return set()

    records = (
        db.session.query(WishlistItem.product_id)
        .filter_by(user_id=current_user.id)
        .all()
    )

    return {record[0] for record in records}


def product_to_dict(product, favorite_ids=None):
    favorite_ids = favorite_ids or set()

    images = [
        url_for("static", filename=path)
        for path in (product.images or [])
        if path
    ]

    colors = []
    seen = set()

    for variant in product.variants:
        key = (
            variant.color_name.casefold(),
            variant.color_hex.lower()
        )

        if key not in seen:
            seen.add(key)

            colors.append({
                "name": variant.color_name,
                "hex": variant.color_hex,
                "active": len(colors) == 0
            })

    size_order = [
        "XS", "S", "M", "L", "XL", "XXL"
    ]

    sizes = [
        size
        for size in size_order
        if any(
            v.size == size
            for v in product.variants
        )
    ]

    if product.category == "women":
        gender_badge = "WOMEN"
    elif product.category == "men":
        gender_badge = "MAN"
    else:
        gender_badge = "UNISEX"

    stock_qty = product.total_stock

    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,

        "category": product.subcategory,
        "category_badge": product.subcategory.upper(),

        "gender": product.category,
        "gender_badge": gender_badge,

        "desc": product.description or "",
        "price": product.price,
        "price_formatted": (
            f"Rp {product.price:,}".replace(",", ".")
        ),

        "image": images[0] if images else "",
        "images": images,
        "gallery": images[1:],

        "colors": colors,

        "primary_color_name": (
            colors[0]["name"] if colors else ""
        ),

        "color_count_text": (
            f"{len(colors)} Warna"
        ),

        "sizes": sizes,

        "variants": [
            {
                "color_name": v.color_name,
                "color_hex": v.color_hex,
                "size": v.size,
                "stock": v.stock
            }
            for v in product.variants
        ],

        "stock": (
            "READY STOCK"
            if stock_qty > 0
            else "STOK HABIS"
        ),

        "stock_qty": stock_qty,

        "series": product.collection_label or "",
        "collection_label": product.collection_label or "",

        "care_title": product.care_title or "",
        "care_text": product.care_text or "",

        "featured": bool(product.featured),

        "sub_badge": (
            "FEATURED"
            if product.featured
            else None
        ),

        "rating": 0,
        "reviews": 0,
        "sold": 0,

        "wishlisted": (
            product.id in favorite_ids
        )
    }


def convert_products(products):
    favorites = current_favorite_ids()

    return [
        product_to_dict(product, favorites)
        for product in products
    ]


# ==========================================
# KATEGORI VISUAL
# ==========================================

def category_silhouettes(categories):
    data = {
        "Outer": (
            "Outer",
            "Jackets & Coats",
            "img/dashboard_outer.png"
        ),

        "Skirts": (
            "Skirts",
            "Midi & Pleated",
            "img/dashboard_skirt.png"
        ),

        "T-Shirts": (
            "T-Shirts",
            "Cotton Essentials",
            "img/dashboard_t-shirt.png"
        ),

        "Pants": (
            "Pants",
            "Trousers & Chino",
            "img/dashboard_pants.png"
        ),

        "Shirts": (
            "Shirts",
            "Linen & Cotton",
            "img/dashboard_shirt.png"
        ),

        "Knit": (
            "Knit",
            "Knitwear",
            "img/dashboard_t-shirt.png"
        )
    }

    result = []

    for category in categories:
        if category not in data:
            continue

        title, subtitle, image = data[category]

        result.append({
            "title": title,
            "subtitle": subtitle,
            "category": category,
            "image": url_for(
                "static",
                filename=image
            )
        })

    return result


# ==========================================
# HOME
# ==========================================

@shop_bp.route("/")
def index():

    products = (
        Product.query
        .filter_by(status="published")
        .order_by(
            Product.featured.desc(),
            Product.id.desc()
        )
        .limit(4)
        .all()
    )

    return render_template(
        "shop/index.html",
        products=convert_products(products)
    )


# ==========================================
# ALL COLLECTION
# ==========================================

@shop_bp.route("/all")
@shop_bp.route("/catalog")
def catalog_all():

    products = published_products()

    return render_template(
        "shop/catalog.html",
        products=convert_products(products),

        silhouettes=category_silhouettes([
            "Outer",
            "Skirts",
            "T-Shirts",
            "Pants",
            "Shirts",
            "Knit"
        ])
    )


# ==========================================
# WOMEN COLLECTION
# ==========================================

@shop_bp.route("/women")
def women():

    products = (
        Product.query
        .filter(
            Product.status == "published",
            Product.category.in_([
                "women", "unisex"
            ])
        )
        .order_by(Product.id.desc())
        .all()
    )

    return render_template(
        "shop/women.html",
        products=convert_products(products),

        silhouettes=category_silhouettes([
            "Skirts",
            "Pants",
            "Outer",
            "Shirts",
            "T-Shirts",
            "Knit"
        ])
    )


# ==========================================
# MAN COLLECTION
# ==========================================

@shop_bp.route("/man")
@shop_bp.route("/men")
def man():

    products = (
        Product.query
        .filter(
            Product.status == "published",
            Product.category.in_([
                "men", "unisex"
            ])
        )
        .order_by(Product.id.desc())
        .all()
    )

    return render_template(
        "shop/man.html",
        products=convert_products(products),

        silhouettes=category_silhouettes([
            "Outer",
            "T-Shirts",
            "Shirts",
            "Pants",
            "Knit"
        ])
    )


# ==========================================
# DETAIL PRODUK
# ==========================================

@shop_bp.route("/product/<int:product_id>")
def product_detail(product_id):

    product = db.get_or_404(
        Product,
        product_id
    )

    if product.status != "published":
        abort(404)

    return render_template(
        "shop/product_detail.html",

        product=product_to_dict(
            product,
            current_favorite_ids()
        )
    )


# ==========================================
# WISHLIST CUSTOMER
# ==========================================

@shop_bp.route("/wishlist")
@login_required
def wishlist():

    saved_items = (
        WishlistItem.query
        .filter_by(user_id=current_user.id)
        .order_by(
            WishlistItem.created_at.desc()
        )
        .all()
    )

    products = [
        item.product
        for item in saved_items
        if (
            item.product is not None
            and item.product.status == "published"
        )
    ]

    return render_template(
        "shop/wishlist.html",
        products=convert_products(products)
    )


# ==========================================
# TOGGLE WISHLIST
# ==========================================

@shop_bp.route(
    "/wishlist/<int:product_id>/toggle",
    methods=["POST"]
)
def wishlist_toggle(product_id):

    if not current_user.is_authenticated:
        return jsonify({
            "success": False,
            "message": "Login untuk menyimpan Wishlist."
        }), 401

    if current_user.role != "customer":
        return jsonify({
            "success": False,
            "message": "Wishlist hanya untuk customer."
        }), 403

    product = db.session.get(
        Product,
        product_id
    )

    if not product or product.status != "published":
        return jsonify({
            "success": False,
            "message": "Produk tidak tersedia."
        }), 404

    item = db.session.get(
        WishlistItem,
        (current_user.id, product_id)
    )

    if item:
        db.session.delete(item)
        is_saved = False

    else:
        db.session.add(
            WishlistItem(
                user_id=current_user.id,
                product_id=product_id
            )
        )
        is_saved = True

    db.session.commit()

    wishlist_count = (
        WishlistItem.query
        .filter_by(user_id=current_user.id)
        .count()
    )

    return jsonify({
        "success": True,
        "wishlisted": is_saved,
        "count": wishlist_count,

        "message": (
            "Produk ditambahkan ke Wishlist."
            if is_saved
            else "Produk dihapus dari Wishlist."
        )
    })

    
# ==========================================
# PENCARIAN PRODUK KURO WEAR
# ==========================================

def find_products_by_keyword(keyword, limit=None):
    """
    Mencari produk published berdasarkan:
    - Nama produk
    - Subkategori
    - Deskripsi produk

    Produk draft tidak ditampilkan.
    """

    keyword = (keyword or "").strip()[:80]

    if not keyword:
        return []

    search_pattern = f"%{keyword}%"

    query = (
        Product.query
        .filter(
            Product.status == "published",
            or_(
                Product.name.ilike(search_pattern),
                Product.subcategory.ilike(search_pattern),
                Product.description.ilike(search_pattern)
            )
        )
        .order_by(Product.id.desc())
    )

    if limit is not None:
        query = query.limit(limit)

    return query.all()


# ==========================================
# HALAMAN HASIL PENCARIAN
# ==========================================

@shop_bp.route("/search", methods=["GET"])
def search():

    keyword = request.args.get(
        "q", "",
        type=str
    ).strip()[:80]

    # Jika input kosong, kembali ke katalog
    if not keyword:
        return redirect(
            url_for("shop.catalog_all")
        )

    products = find_products_by_keyword(keyword)

    return render_template(
        "shop/catalog.html",

        products=convert_products(products),

        silhouettes=category_silhouettes([
            "Skirts",
            "Pants",
            "Outer",
            "Shirts",
            "T-Shirts"
        ]),

        search_query=keyword
    )


# ==========================================
# SARAN PRODUK SECARA REAL-TIME
# ==========================================

@shop_bp.route("/search/suggest", methods=["GET"])
def search_suggest():

    keyword = request.args.get(
        "q", "",
        type=str
    ).strip()[:80]

    # Minimal 2 karakter untuk menampilkan saran
    if len(keyword) < 2:
        return jsonify({
            "items": []
        })

    products = find_products_by_keyword(
        keyword,
        limit=6
    )

    suggestions = []

    for product in products:

        data = product_to_dict(product)

        suggestions.append({
            "id": product.id,
            "name": product.name,
            "price": data["price_formatted"],
            "image": data["image"],
            "category": data["category_badge"],

            "url": url_for(
                "shop.product_detail",
                product_id=product.id
            )
        })

    return jsonify({
        "items": suggestions
    })