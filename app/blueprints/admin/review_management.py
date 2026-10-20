from datetime import datetime, timezone

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.product_review import ProductReview
from app.models.review_interaction import ProductReviewReply

from .routes import admin_bp, admin_only


REVIEW_CATEGORIES = [
    ('tshirt', 'T-Shirt'),
    ('outer', 'Outer'),
    ('pants', 'Pants'),
    ('shirt', 'Shirt'),
    ('skirt', 'Skirt')
]

VALID_CATEGORIES = {'all', *(key for key, _ in REVIEW_CATEGORIES)}


def review_category(review):
    product = review.product

    if product is None:
        return 'other'

    name = ''.join(
        char for char in (product.subcategory or '').lower()
        if char.isalpha()
    )

    aliases = {
        'tshirt': 'tshirt',
        'tshirts': 'tshirt',
        'tee': 'tshirt',
        'tees': 'tshirt',
        'outer': 'outer',
        'outerwear': 'outer',
        'jacket': 'outer',
        'jackets': 'outer',
        'pants': 'pants',
        'pant': 'pants',
        'trousers': 'pants',
        'shirt': 'shirt',
        'shirts': 'shirt',
        'skirt': 'skirt',
        'skirts': 'skirt'
    }

    return aliases.get(name, 'other')


def reply_target(review_id, category):
    if category not in VALID_CATEGORIES:
        category = 'all'

    return url_for(
        'admin.manage_product_reviews',
        category=category
    ) + f'#admin-review-{review_id}'


@admin_bp.route('/reviews', methods=['GET'])
@admin_only
def manage_product_reviews():
    category = request.args.get('category', 'all').strip().lower()

    if category not in VALID_CATEGORIES:
        abort(400)

    all_reviews = ProductReview.query.options(
        selectinload(ProductReview.product)
    ).filter_by(
        status='published'
    ).order_by(
        ProductReview.created_at.desc(),
        ProductReview.id.desc()
    ).all()

    counts = {key: 0 for key, _ in REVIEW_CATEGORIES}
    counts['all'] = len(all_reviews)

    for review in all_reviews:
        key = review_category(review)

        if key in counts:
            counts[key] += 1

    if category == 'all':
        reviews = all_reviews
    else:
        reviews = [
            review for review in all_reviews
            if review_category(review) == category
        ]

    review_ids = [review.id for review in reviews]

    replies = {}

    if review_ids:
        replies = {
            reply.review_id: reply
            for reply in ProductReviewReply.query.filter(
                ProductReviewReply.review_id.in_(review_ids)
            ).all()
        }

    unanswered_count = sum(
        review.id not in replies
        for review in reviews
    )

    return render_template(
        'admin/reviews.html',
        reviews=reviews,
        replies=replies,
        categories=REVIEW_CATEGORIES,
        counts=counts,
        selected_category=category,
        unanswered_count=unanswered_count
    )


@admin_bp.route('/reviews/<int:review_id>/reply', methods=['POST'])
@admin_only
def reply_product_review(review_id):
    review = ProductReview.query.filter_by(
        id=review_id,
        status='published'
    ).first_or_404()

    category = (request.form.get('category') or 'all').strip().lower()
    target = reply_target(review.id, category)
    message = (request.form.get('message') or '').strip()

    if not 5 <= len(message) <= 1000:
        flash('Balasan harus terdiri dari 5 sampai 1000 karakter.', 'warning')
        return redirect(target)

    try:
        reply = db.session.get(ProductReviewReply, review.id)

        if reply is None:
            reply = ProductReviewReply(
                review_id=review.id,
                admin_id=current_user.id,
                message=message
            )
            db.session.add(reply)
        else:
            reply.admin_id = current_user.id
            reply.message = message
            reply.updated_at = datetime.now(timezone.utc)

        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception(
            'Gagal menyimpan balasan untuk review %s',
            review_id
        )
        flash('Balasan belum berhasil disimpan.', 'danger')
        return redirect(target)

    flash('Balasan Admin berhasil disimpan.', 'success')
    return redirect(target)