from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class SessionMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("booking.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_role = db.Column(db.String(30), nullable=False, default="student")
    message_text = db.Column(db.Text, default="")
    file_url = db.Column(db.String(255), default="")
    file_name = db.Column(db.String(255), default="")
    is_blocked = db.Column(db.Boolean, default=False)
    blocked_reason = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)