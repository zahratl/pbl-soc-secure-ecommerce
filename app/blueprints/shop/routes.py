
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models.product import Product
from app.models.product_review import ProductReview
from app.models.wishlist import WishlistItem


shop_bp = Blueprint('shop', __name__)

SIZE_ORDER = ('XS', 'S', 'M', 'L', 'XL', 'XXL')

CATEGORY_IMAGES = {
    'Outer': ('Outer', 'Jackets & Coats', 'img/dashboard_outer.png'),
    'Skirts': ('Skirts', 'Midi & Pleated', 'img/dashboard_skirt.png'),
    'T-Shirts': ('T-Shirts', 'Cotton Essentials', 'img/dashboard_t-shirt.png'),
    'Pants': ('Pants', 'Trousers & Chino', 'img/dashboard_pants.png'),
    'Shirts': ('Shirts', 'Linen & Cotton', 'img/dashboard_shirt.png'),
    'Knit': ('Knit', 'Knitwear', 'img/dashboard_t-shirt.png')
}


@shop_bp.app_context_processor
def inject_navbar_wishlist_count():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return {'wishlist_count': 0}

    count = db.session.query(func.count(WishlistItem.product_id)).join(
        Product, WishlistItem.product_id == Product.id
    ).filter(WishlistItem.user_id == current_user.id, Product.status == 'published').scalar()

    return {'wishlist_count': int(count or 0)}


def published_products():
    return Product.query.filter_by(status='published').order_by(Product.id.desc()).all()


def current_favorite_ids():
    if not current_user.is_authenticated or current_user.role != 'customer':
        return set()

    rows = db.session.query(WishlistItem.product_id).filter_by(user_id=current_user.id).all()
    return {row[0] for row in rows}


def review_summaries(product_ids):
    if not product_ids:
        return {}

    rows = db.session.query(
        ProductReview.product_id,
        func.count(ProductReview.id),
        func.avg(ProductReview.rating)
    ).filter(
        ProductReview.product_id.in_(product_ids),
        ProductReview.status == 'published'
    ).group_by(ProductReview.product_id).all()

    return {
        product_id: (int(count), round(float(average), 1))
        for product_id, count, average in rows
    }


def product_to_dict(product, favorite_ids=None, rating_summary=None):
    favorite_ids = favorite_ids if favorite_ids is not None else set()

    if rating_summary is None:
        rating_summary = review_summaries([product.id]).get(product.id, (0, 0.0))

    review_count, average_rating = rating_summary
    images = [url_for('static', filename=path) for path in (product.images or []) if path]

    colors = []
    seen = set()

    for variant in product.variants:
        key = ((variant.color_name or '').casefold(), (variant.color_hex or '').lower())

        if key not in seen:
            seen.add(key)
            colors.append({
                'name': variant.color_name,
                'hex': variant.color_hex,
                'active': not colors
            })

    sizes = [
        size for size in SIZE_ORDER
        if any(variant.size == size for variant in product.variants)
    ]

    gender_badge = {
        'women': 'WOMEN',
        'men': 'MAN'
    }.get(product.category, 'UNISEX')

    stock_qty = product.total_stock

    return {
        'id': product.id,
        'name': product.name,
        'sku': product.sku,
        'category': product.subcategory,
        'category_badge': (product.subcategory or '').upper(),
        'gender': product.category,
        'gender_badge': gender_badge,
        'desc': product.description or '',
        'price': product.price,
        'price_formatted': f'Rp {int(product.price or 0):,}'.replace(',', '.'),
        'image': images[0] if images else '',
        'images': images,
        'gallery': images[1:],
        'colors': colors,
        'primary_color_name': colors[0]['name'] if colors else '',
        'color_count_text': f'{len(colors)} Warna',
        'sizes': sizes,
        'variants': [
            {
                'color_name': variant.color_name,
                'color_hex': variant.color_hex,
                'size': variant.size,
                'stock': variant.stock
            }
            for variant in product.variants
        ],
        'stock': 'READY STOCK' if stock_qty > 0 else 'STOK HABIS',
        'stock_qty': stock_qty,
        'series': product.collection_label or '',
        'collection_label': product.collection_label or '',
        'care_title': product.care_title or '',
        'care_text': product.care_text or '',
        'featured': bool(product.featured),
        'sub_badge': 'FEATURED' if product.featured else None,
        'rating': average_rating,
        'reviews': review_count,
        'sold': 0,
        'wishlisted': product.id in favorite_ids
    }


def convert_products(products):
    if not products:
        return []

    favorite_ids = current_favorite_ids()
    summaries = review_summaries([product.id for product in products])

    return [
        product_to_dict(product, favorite_ids, summaries.get(product.id, (0, 0.0)))
        for product in products
    ]


def category_silhouettes(categories):
    result = []

    for category in categories:
        details = CATEGORY_IMAGES.get(category)

        if details:
            title, subtitle, image = details

            result.append({
                'title': title,
                'subtitle': subtitle,
                'category': category,
                'image': url_for('static', filename=image)
            })

    return result


@shop_bp.route('/')
def index():
    products = Product.query.filter_by(status='published').order_by(
        Product.featured.desc(),
        Product.id.desc()
    ).limit(4).all()

    return render_template('shop/index.html', products=convert_products(products))


@shop_bp.route('/all')
@shop_bp.route('/catalog')
def catalog_all():
    return render_template(
        'shop/catalog.html',
        products=convert_products(published_products()),
        silhouettes=category_silhouettes([
            'Outer', 'Skirts', 'T-Shirts', 'Pants', 'Shirts', 'Knit'
        ])
    )


@shop_bp.route('/women')
def women():
    products = Product.query.filter(
        Product.status == 'published',
        Product.category.in_(['women', 'unisex'])
    ).order_by(Product.id.desc()).all()

    return render_template(
        'shop/women.html',
        products=convert_products(products),
        silhouettes=category_silhouettes([
            'Skirts', 'Pants', 'Outer', 'Shirts', 'T-Shirts', 'Knit'
        ])
    )


@shop_bp.route('/man')
@shop_bp.route('/men')
def man():
    products = Product.query.filter(
        Product.status == 'published',
        Product.category.in_(['men', 'unisex'])
    ).order_by(Product.id.desc()).all()

    return render_template(
        'shop/man.html',
        products=convert_products(products),
        silhouettes=category_silhouettes([
            'Outer', 'T-Shirts', 'Shirts', 'Pants', 'Knit'
        ])
    )


@shop_bp.route('/product/<int:product_id>')
def product_detail(product_id):
    product = db.get_or_404(Product, product_id)

    if product.status != 'published':
        abort(404)

    return render_template(
        'shop/product_detail.html',
        product=product_to_dict(product, current_favorite_ids())
    )


@shop_bp.route('/wishlist')
@login_required
def wishlist():
    if current_user.role != 'customer' or not current_user.is_active:
        abort(403)

    saved_items = WishlistItem.query.filter_by(
        user_id=current_user.id
    ).order_by(WishlistItem.created_at.desc()).all()

    products = [
        item.product for item in saved_items
        if item.product and item.product.status == 'published'
    ]

    return render_template(
        'shop/wishlist.html',
        products=convert_products(products)
    )


@shop_bp.route('/wishlist/<int:product_id>/toggle', methods=['POST'])
def wishlist_toggle(product_id):
    if not current_user.is_authenticated:
        return jsonify({
            'success': False,
            'message': 'Login untuk menyimpan Wishlist.'
        }), 401

    if current_user.role != 'customer' or not current_user.is_active:
        return jsonify({
            'success': False,
            'message': 'Wishlist hanya untuk customer.'
        }), 403

    product = db.session.get(Product, product_id)

    if not product or product.status != 'published':
        return jsonify({
            'success': False,
            'message': 'Produk tidak tersedia.'
        }), 404

    item = db.session.get(WishlistItem, (current_user.id, product_id))
    is_saved = item is None

    try:
        if item:
            db.session.delete(item)
        else:
            db.session.add(WishlistItem(
                user_id=current_user.id,
                product_id=product_id
            ))

        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()

        return jsonify({
            'success': False,
            'message': 'Wishlist belum dapat diperbarui.'
        }), 409

    count = db.session.query(func.count(WishlistItem.product_id)).join(
        Product, WishlistItem.product_id == Product.id
    ).filter(
        WishlistItem.user_id == current_user.id,
        Product.status == 'published'
    ).scalar()

    return jsonify({
        'success': True,
        'wishlisted': is_saved,
        'count': int(count or 0),
        'message': (
            'Produk ditambahkan ke Wishlist.'
            if is_saved else 'Produk dihapus dari Wishlist.'
        )
    })


def find_products_by_keyword(keyword, limit=None):
    keyword = (keyword or '').strip()[:80]

    if not keyword:
        return []

    search_pattern = f'%{keyword}%'

    query = Product.query.filter(
        Product.status == 'published',
        or_(
            Product.name.ilike(search_pattern),
            Product.subcategory.ilike(search_pattern),
            Product.description.ilike(search_pattern)
        )
    ).order_by(Product.id.desc())

    return query.limit(limit).all() if limit is not None else query.all()


@shop_bp.route('/search', methods=['GET'])
def search():
    keyword = request.args.get('q', '', type=str).strip()[:80]

    if not keyword:
        return redirect(url_for('shop.catalog_all'))

    return render_template(
        'shop/catalog.html',
        products=convert_products(find_products_by_keyword(keyword)),
        silhouettes=category_silhouettes([
            'Skirts', 'Pants', 'Outer', 'Shirts', 'T-Shirts'
        ]),
        search_query=keyword
    )


@shop_bp.route('/search/suggest', methods=['GET'])
def search_suggest():
    keyword = request.args.get('q', '', type=str).strip()[:80]

    if len(keyword) < 2:
        return jsonify({'items': []})

    suggestions = []

    for product in find_products_by_keyword(keyword, limit=6):
        images = [
            url_for('static', filename=path)
            for path in (product.images or [])
            if path
        ]

        suggestions.append({
            'id': product.id,
            'name': product.name,
            'price': f'Rp {int(product.price or 0):,}'.replace(',', '.'),
            'image': images[0] if images else '',
            'category': (product.subcategory or '').upper(),
            'url': url_for('shop.product_detail', product_id=product.id)
        })

    return jsonify({'items': suggestions})


from . import reviews