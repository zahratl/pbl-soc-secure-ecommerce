from datetime import datetime, timezone
from app.extensions import db

class CartItem(db.Model):
    __tablename__ = "cart_items"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
        index=True
    )

    color_name = db.Column(
        db.String(100),
        nullable=False
    )

    size = db.Column(
        db.String(20),
        nullable=False
    )

    quantity = db.Column(
        db.Integer,
        nullable=False,
        default=1
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "cart_items",
            cascade="all, delete-orphan"
        )
    )

    product = db.relationship(
        "Product",
        backref=db.backref(
            "cart_items",
            cascade="all, delete-orphan"
        )
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "product_id",
            "color_name",
            "size",
            name="uq_customer_cart_variant"
        ),
        db.CheckConstraint(
            "quantity > 0",
            name="ck_cart_positive_quantity"
        ),
    )