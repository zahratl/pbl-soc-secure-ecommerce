from datetime import datetime, timezone
from app.extensions import db

class CustomerProfile(db.Model):
    __tablename__ = "customer_profiles"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        primary_key=True
    )

    display_name = db.Column(db.String(120))
    phone = db.Column(db.String(25))
    birth_date = db.Column(db.Date)
    gender = db.Column(db.String(12))
    avatar_filename = db.Column(db.String(90))

    updated_at = db.Column(
        db.DateTime,
        onupdate=lambda: datetime.now(timezone.utc)
    )

    user = db.relationship(
        "User",
        backref=db.backref(
            "customer_profile",
            uselist=False
        )
    )