from datetime import datetime, timezone
from app.extensions import db

class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    sku = db.Column(db.String(64), unique=True, nullable=False, index=True)

    category = db.Column(db.String(15), nullable=False)
    subcategory = db.Column(db.String(70), nullable=False)
    collection_label = db.Column(db.String(80), default='')

    price = db.Column(db.Integer, nullable=False, default=0)
    cost = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, default='')
    care_title = db.Column(db.String(150), default='')
    care_text = db.Column(db.Text, default='')

    images = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(
        db.String(12), nullable=False, default='draft'
    )
    featured = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc)
    )

    variants = db.relationship(
        'ProductVariant',
        back_populates='product',
        cascade='all, delete-orphan',
        lazy='selectin'
    )

    @property
    def total_stock(self):
        return sum(v.stock for v in self.variants)

    @property
    def low_stock_variants(self):
        return [
            v for v in self.variants
            if v.stock <= v.minimum_stock
        ]

    @property
    def main_image(self):
        return (self.images or [''])[0]


class ProductVariant(db.Model):
    __tablename__ = 'product_variants'

    id = db.Column(db.Integer, primary_key=True)

    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id'),
        nullable=False
    )

    color_name = db.Column(db.String(60), nullable=False)
    color_hex = db.Column(
        db.String(7), nullable=False, default='#333333'
    )
    size = db.Column(db.String(5), nullable=False)
    stock = db.Column(db.Integer, nullable=False, default=0)
    minimum_stock = db.Column(
        db.Integer, nullable=False, default=5
    )

    product = db.relationship(
        'Product', back_populates='variants'
    )

    __table_args__ = (
        db.UniqueConstraint(
            'product_id', 'color_name', 'size',
            name='uq_product_color_size'
        ),
    )
