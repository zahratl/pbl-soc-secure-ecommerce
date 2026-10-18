#setiap ulasan mempunyai hubungan langsung dengan produk, customer, dan item pesanan yang sudah dibeli. Foto ulasan disimpan sebagai path di database, sementara file gambarnya disimpan dalam direktori static
from datetime import datetime, timezone

from app.extensions import db

class ProductReview(db.Model):
    __tablename__ = 'product_reviews'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False, index=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey('order_items.id'), nullable=False)
    author_name = db.Column(db.String(100), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    image_paths = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(15), nullable=False, default='published', index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    product = db.relationship('Product')
    order_item = db.relationship('OrderItem')

    __table_args__ = (
        db.CheckConstraint('rating >= 1 AND rating <= 5', name='ck_product_review_rating'),
        db.UniqueConstraint('order_item_id', name='uq_product_review_order_item'),
    )