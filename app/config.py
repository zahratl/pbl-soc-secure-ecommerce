import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

# Membaca file .env yang baru saja kamu isi
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce_local.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Koneksi aman ke MySQL VM
    SQLALCHEMY_ENGINE_OPTIONS = {}

    if SQLALCHEMY_DATABASE_URI.startswith("mysql+pymysql://"):
        MYSQL_CA = (
            Path.home()
            / ".kurowear"
            / "certs"
            / "mysql-ca.pem"
        )

        if not MYSQL_CA.is_file():
            raise RuntimeError(
                f"Sertifikat CA MySQL tidak ditemukan: {MYSQL_CA}"
            )

        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "connect_args": {
                "ssl_ca": str(MYSQL_CA),
                "ssl_verify_cert": True,
                "ssl_verify_identity": True,
                "connect_timeout": 30
            }
        }

    
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
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False