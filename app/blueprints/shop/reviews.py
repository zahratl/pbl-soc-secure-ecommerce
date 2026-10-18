#menangani pemeriksaan kelayakan customer, validasi foto, penyimpanan ulasan, dan penyediaan data ulasan ke halaman produk.
import io
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db, limiter
from app.models.customer_profile import CustomerProfile
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.product_review import ProductReview
from app.models.review_interaction import ProductReviewEdit, ProductReviewLike, ProductReviewReply
from .routes import shop_bp


MAX_PHOTOS = 3
MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 7 * 1024 * 1024
WIB = timezone(timedelta(hours=7))


def review_url(product_id, review_id=None):
    base = url_for('shop.product_detail', product_id=product_id)
    return base + (f'#review-{review_id}' if review_id else '#kpdReviews')


def format_date(value):
    if value is None:
        return '-'

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(WIB).strftime('%d/%m/%Y')


def photos_folder():
    path = Path(current_app.static_folder) / 'uploads' / 'product_reviews'
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_photos(paths):
    folder = photos_folder()

    for path in paths:
        if not path.startswith('uploads/product_reviews/'):
            continue

        filename = path.removeprefix('uploads/product_reviews/')

        if Path(filename).name == filename and filename.endswith('.webp'):
            (folder / filename).unlink(missing_ok=True)


def verified_order_item(product_id, item_id, user_id):
    return OrderItem.query.join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        OrderItem.id == item_id,
        OrderItem.product_id == product_id,
        Order.user_id == user_id,
        Order.payment_status == 'paid',
        Order.fulfillment_status == 'completed'
    ).first()


def reviewable_items(product_id, user_id):
    items = OrderItem.query.join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        OrderItem.product_id == product_id,
        Order.user_id == user_id,
        Order.payment_status == 'paid',
        Order.fulfillment_status == 'completed'
    ).order_by(OrderItem.id.desc()).all()

    if not items:
        return []

    reviewed = {
        item_id
        for item_id, in db.session.query(
            ProductReview.order_item_id
        ).filter(
            ProductReview.order_item_id.in_([item.id for item in items])
        ).all()
    }

    return [item for item in items if item.id not in reviewed]


def read_rating_and_comment():
    try:
        rating = int(request.form.get('rating', '0'))
    except (TypeError, ValueError):
        rating = 0

    comment = (request.form.get('comment') or '').strip()

    if not 1 <= rating <= 5:
        raise ValueError('Pilih rating 1 sampai 5 bintang.')

    if not 10 <= len(comment) <= 2000:
        raise ValueError('Komentar harus terdiri dari 10 sampai 2000 karakter.')

    return rating, comment


def image_to_webp(upload):
    data = upload.read(MAX_FILE_SIZE + 1)

    if not data or len(data) > MAX_FILE_SIZE:
        raise ValueError('Ukuran setiap foto maksimal 2 MB.')

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('Gunakan foto JPG, PNG, atau WEBP.')

            if min(image.size) < 80 or image.width * image.height > 12_000_000:
                raise ValueError('Resolusi foto tidak valid (minimal 80 px dan maksimal 12 MP).')

            clean = ImageOps.exif_transpose(image).convert('RGBA')
            clean.thumbnail((1200, 1200))

            background = Image.new('RGB', clean.size, (255, 255, 255))
            background.paste(clean, mask=clean.getchannel('A'))

            stream = io.BytesIO()
            background.save(stream, 'WEBP', quality=83, method=5)

            return stream.getvalue()

    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('File foto tidak valid atau rusak.') from exc


def uploaded_images():
    if request.content_length is not None and request.content_length > MAX_UPLOAD_BYTES:
        raise ValueError('Ukuran total upload terlalu besar.')

    uploads = [
        file for file in request.files.getlist('photos')
        if file and file.filename
    ]

    if len(uploads) > MAX_PHOTOS:
        raise ValueError('Maksimal 3 foto setiap ulasan.')

    return [image_to_webp(file) for file in uploads]


def store_images(images):
    names = []

    try:
        folder = photos_folder()

        for image in images:
            filename = f'{uuid.uuid4().hex}.webp'
            (folder / filename).write_bytes(image)
            names.append(f'uploads/product_reviews/{filename}')

    except OSError:
        delete_photos(names)
        raise

    return names


@shop_bp.context_processor
def inject_product_reviews():
    if request.endpoint != 'shop.product_detail':
        return {}

    product_id = (request.view_args or {}).get('product_id')

    if not product_id:
        return {}

    rows = ProductReview.query.filter_by(
        product_id=product_id,
        status='published'
    ).order_by(
        ProductReview.created_at.desc(),
        ProductReview.id.desc()
    ).all()

    review_ids = [row.id for row in rows]

    likes = Counter()
    liked_ids = set()
    replies = {}
    edited_ids = set()
    eligible = []

    if review_ids:
        likes = Counter({
            review_id: count
            for review_id, count in db.session.query(
                ProductReviewLike.review_id,
                db.func.count(ProductReviewLike.user_id)
            ).filter(
                ProductReviewLike.review_id.in_(review_ids)
            ).group_by(ProductReviewLike.review_id).all()
        })

        replies = {
            reply.review_id: reply
            for reply in ProductReviewReply.query.filter(
                ProductReviewReply.review_id.in_(review_ids)
            ).all()
        }

        edited_ids = {
            item.review_id
            for item in ProductReviewEdit.query.filter(
                ProductReviewEdit.review_id.in_(review_ids)
            ).all()
        }

    if current_user.is_authenticated and current_user.role == 'customer' and current_user.is_active:
        eligible = reviewable_items(product_id, current_user.id)

        if review_ids:
            liked_ids = {
                item.review_id
                for item in ProductReviewLike.query.filter(
                    ProductReviewLike.review_id.in_(review_ids),
                    ProductReviewLike.user_id == current_user.id
                ).all()
            }

    result = [
        {
            'id': row.id,
            'author_name': row.author_name,
            'rating': row.rating,
            'comment': row.comment,
            'images': [
                url_for('static', filename=path)
                for path in (row.image_paths or [])
            ],
            'created_at': format_date(row.created_at),
            'own': current_user.is_authenticated and row.user_id == current_user.id,
            'liked': row.id in liked_ids,
            'like_count': likes[row.id],
            'edited': row.id in edited_ids,
            'reply': replies.get(row.id)
        }
        for row in rows
    ]

    return {
        'reviews': result,
        'product_reviews': result,
        'reviewable_order_items': eligible,
        'review_count': len(result),
        'average_rating': sum(row.rating for row in rows) / len(rows) if rows else 0
    }


@shop_bp.route('/product/<int:product_id>/reviews', methods=['POST'])
@login_required
@limiter.limit('8 per hour')
def submit_product_review(product_id):
    product = db.session.get(Product, product_id)

    if product is None or product.status != 'published':
        abort(404)

    if current_user.role != 'customer' or not current_user.is_active:
        abort(403)

    target = review_url(product_id)

    try:
        item_id = int(request.form.get('order_item_id', '0'))
        rating, comment = read_rating_and_comment()

        item = verified_order_item(product_id, item_id, current_user.id)

        if not item:
            raise ValueError('Ulasan hanya bisa dibuat untuk pesanan Lunas dan Selesai milikmu.')

        if ProductReview.query.filter_by(order_item_id=item.id).first():
            raise ValueError('Item pesanan ini sudah pernah diulas. Gunakan tombol Edit Ulasan.')

        images = uploaded_images()

    except (ValueError, TypeError) as exc:
        flash(str(exc), 'warning')
        return redirect(target)

    profile = db.session.get(CustomerProfile, current_user.id)
    author = (
        (profile.display_name if profile else None)
        or current_user.name
        or 'Pelanggan KURO WEAR'
    ).strip()[:100]

    stored = []

    try:
        stored = store_images(images)

        review = ProductReview(
            user_id=current_user.id,
            product_id=product_id,
            order_item_id=item.id,
            author_name=author,
            rating=rating,
            comment=comment,
            image_paths=stored
        )

        db.session.add(review)
        db.session.commit()

    except (SQLAlchemyError, OSError):
        db.session.rollback()
        delete_photos(stored)
        current_app.logger.exception('Gagal menyimpan ulasan produk %s', product_id)
        flash('Gagal menyimpan ulasan.', 'danger')
        return redirect(target)

    flash('Ulasan berhasil dipublikasikan.', 'success')

    return redirect(review_url(product_id, review.id))


@shop_bp.route('/product/<int:product_id>/reviews/<int:review_id>/edit', methods=['POST'])
@login_required
@limiter.limit('12 per hour')
def edit_product_review(product_id, review_id):
    if current_user.role != 'customer' or not current_user.is_active:
        abort(403)

    review = ProductReview.query.filter_by(
        id=review_id,
        product_id=product_id,
        user_id=current_user.id,
        status='published'
    ).first_or_404()

    if not verified_order_item(product_id, review.order_item_id, current_user.id):
        abort(403)

    target = review_url(product_id, review_id)

    try:
        rating, comment = read_rating_and_comment()
        images = uploaded_images()
        clear_photos = request.form.get('clear_photos') == '1'

    except ValueError as exc:
        flash(str(exc), 'warning')
        return redirect(target)

    old_images = list(review.image_paths or [])
    new_images = []

    try:
        if images:
            new_images = store_images(images)
            review.image_paths = new_images

        elif clear_photos:
            review.image_paths = []

        review.rating = rating
        review.comment = comment

        edit = db.session.get(ProductReviewEdit, review.id)

        if edit is None:
            db.session.add(ProductReviewEdit(review_id=review.id))
        else:
            edit.updated_at = datetime.now(timezone.utc)

        db.session.commit()

    except (SQLAlchemyError, OSError):
        db.session.rollback()
        delete_photos(new_images)
        current_app.logger.exception('Gagal mengubah ulasan %s', review_id)
        flash('Gagal mengubah ulasan.', 'danger')
        return redirect(target)

    if images or clear_photos:
        delete_photos(old_images)

    flash('Ulasan berhasil diperbarui.', 'success')

    return redirect(target)


@shop_bp.route('/product/<int:product_id>/reviews/<int:review_id>/like', methods=['POST'])
@login_required
@limiter.limit('60 per hour')
def toggle_review_like(product_id, review_id):
    if current_user.role != 'customer' or not current_user.is_active:
        abort(403)

    review = ProductReview.query.filter_by(
        id=review_id,
        product_id=product_id,
        status='published'
    ).first_or_404()

    if review.user_id == current_user.id:
        flash('Kamu tidak bisa menyukai ulasan milik sendiri.', 'info')
        return redirect(review_url(product_id, review_id))

    existing = db.session.get(
        ProductReviewLike,
        (review_id, current_user.id)
    )

    try:
        if existing:
            db.session.delete(existing)
        else:
            db.session.add(ProductReviewLike(
                review_id=review_id,
                user_id=current_user.id
            ))

        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        flash('Status like berubah. Silakan refresh.', 'warning')

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Gagal mengubah like ulasan %s', review_id)
        flash('Gagal memperbarui like.', 'danger')

    return redirect(review_url(product_id, review_id))