
from datetime import datetime, timezone
from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class ProductReviewLike(db.Model):
    __tablename__ = 'product_review_likes'

    review_id = db.Column(
        db.Integer,
        db.ForeignKey('product_reviews.id', ondelete='CASCADE'),
        primary_key=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id', ondelete='CASCADE'),
        primary_key=True
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )


class ProductReviewReply(db.Model):
    __tablename__ = 'product_review_replies'

    review_id = db.Column(
        db.Integer,
        db.ForeignKey('product_reviews.id', ondelete='CASCADE'),
        primary_key=True
    )
    admin_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=False
    )
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now
    )


class ProductReviewEdit(db.Model):
    __tablename__ = 'product_review_edits'

    review_id = db.Column(
        db.Integer,
        db.ForeignKey('product_reviews.id', ondelete='CASCADE'),
        primary_key=True
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )