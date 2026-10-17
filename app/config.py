import os
from datetime import timedelta
from dotenv import load_dotenv

# Membaca file .env yang baru saja kamu isi
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce_local.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Keamanan Cookie & Sesi
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    
    # Maksimal total request 22 MB
    # Maksimal 5 foto, masing-masing 4 MB
    MAX_CONTENT_LENGTH = 22 * 1024 * 1024

    UPLOAD_FOLDER = os.path.join(
        os.path.abspath(os.path.dirname(__file__)),
        'static',
        'uploads'
    )
    
    # Midtrans Sandbox Configuration
    MIDTRANS_SERVER_KEY = os.environ.get('MIDTRANS_SERVER_KEY', '')
    MIDTRANS_CLIENT_KEY = os.environ.get('MIDTRANS_CLIENT_KEY', '')
    MIDTRANS_IS_PRODUCTION = os.environ.get('MIDTRANS_IS_PRODUCTION', 'False').lower() == 'true'

class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True

    if not Config.SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY wajib diatur pada environment production."
        )
class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False