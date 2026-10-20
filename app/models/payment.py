from datetime import datetime, timezone
from app.extensions import db

def utc_now():
    return datetime.now(timezone.utc)

class PaymentTransaction(db.Model):
    __tablename__ = 'payment_transactions'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False, index=True)
    midtrans_transaction_id = db.Column(db.String(100), unique=True, index=True)
    snap_token = db.Column(db.String(255))
    selected_channel = db.Column(db.String(30))
    payment_type = db.Column(db.String(50))
    bank = db.Column(db.String(30))
    transaction_status = db.Column(db.String(30), nullable=False, default='pending', index=True)
    fraud_status = db.Column(db.String(30))
    gross_amount = db.Column(db.Integer, nullable=False)
    verified_at = db.Column(db.DateTime(timezone=True))
    settled_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    order = db.relationship('Order', back_populates='payments')

    __table_args__ = (db.CheckConstraint('gross_amount >= 0', name='ck_payment_gross_nonnegative'),)