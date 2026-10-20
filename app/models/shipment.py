#File baru ini menyimpan kurir, resi, catatan, dan waktu setiap proses pengiriman.
from datetime import datetime, timezone

from app.extensions import db

def utc_now():
    return datetime.now(timezone.utc)

class OrderShipment(db.Model):
    __tablename__ = 'order_shipments'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), unique=True, nullable=False)
    courier = db.Column(db.String(80))
    tracking_number = db.Column(db.String(100))
    notes = db.Column(db.String(500))
    processing_at = db.Column(db.DateTime(timezone=True))
    shipped_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    order = db.relationship('Order')