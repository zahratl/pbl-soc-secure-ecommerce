from datetime import datetime, timezone
from app.extensions import db

class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        primary_key=True
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        primary_key=True
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "wishlist_items",
            cascade="all, delete-orphan"
        )
    )

    product = db.relationship(
        "Product",
        backref=db.backref(
            "wishlist_items",
            cascade="all, delete-orphan"
        )
    )