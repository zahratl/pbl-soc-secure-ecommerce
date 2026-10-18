from datetime import datetime, timezone

from app.extensions import db
from app.services.customer_private_field import CustomerPrivateField

def utc_now():
    return datetime.now(timezone.utc)
class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    customer_name = CustomerPrivateField("customer_name", required=True)
    customer_email = CustomerPrivateField("customer_email", required=True)
    recipient_name = CustomerPrivateField("recipient_name", required=True)
    phone = CustomerPrivateField("phone", required=True)
    city = db.Column(db.String(100), nullable=False)
    full_address = CustomerPrivateField("full_address", required=True)

    customer_name_enc = db.Column(db.Text, nullable=True)
    customer_email_enc = db.Column(db.Text, nullable=True)
    recipient_name_enc = db.Column(db.Text, nullable=True)
    phone_enc = db.Column(db.Text, nullable=True)
    full_address_enc = db.Column(db.Text, nullable=True)

    subtotal = db.Column(db.Integer, nullable=False)
    shipping_cost = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False)

    payment_status = db.Column(db.String(24), nullable=False, default='pending', index=True)
    fulfillment_status = db.Column(db.String(24), nullable=False, default='unfulfilled', index=True)
    checkout_fingerprint = db.Column(db.String(64), index=True)

    paid_at = db.Column(db.DateTime(timezone=True))
    stock_deducted_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    items = db.relationship(
        'OrderItem',
        back_populates='order',
        cascade='all, delete-orphan',
        lazy='selectin'
    )

    payments = db.relationship(
        'PaymentTransaction',
        back_populates='order',
        cascade='all, delete-orphan',
        lazy='selectin'
    )

    __table_args__ = (
        db.CheckConstraint('subtotal >= 0', name='ck_order_subtotal_nonnegative'),
        db.CheckConstraint('shipping_cost >= 0', name='ck_order_shipping_nonnegative'),
        db.CheckConstraint('total >= 0', name='ck_order_total_nonnegative')
    )


class OrderItem(db.Model):
    __tablename__ = 'order_items'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, index=True)
    source_cart_item_id = db.Column(db.Integer)
    product_id = db.Column(db.Integer)
    variant_id = db.Column(db.Integer)

    sku = db.Column(db.String(64), nullable=False)
    product_name = db.Column(db.String(200), nullable=False)
    image_path = db.Column(db.String(500))
    color_name = db.Column(db.String(100), nullable=False)
    size = db.Column(db.String(20), nullable=False)

    unit_price = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    line_total = db.Column(db.Integer, nullable=False)

    order = db.relationship('Order', back_populates='items')

    __table_args__ = (
        db.CheckConstraint('unit_price >= 0', name='ck_order_item_price_nonnegative'),
        db.CheckConstraint('quantity > 0', name='ck_order_item_quantity_positive'),
        db.CheckConstraint('line_total >= 0', name='ck_order_item_total_nonnegative')
    )