import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


class Config:
    # Konfigurasi utama
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce_local.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    SQLALCHEMY_ENGINE_OPTIONS = {}

    # MySQL dengan TLS
    if SQLALCHEMY_DATABASE_URI.startswith('mysql+pymysql://'):
        MYSQL_CA = Path.home() / '.kurowear' / 'certs' / 'mysql-ca.pem'

        if not MYSQL_CA.is_file():
            raise RuntimeError(f'Sertifikat CA MySQL tidak ditemukan: {MYSQL_CA}')

        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'pool_recycle': 1800,
            'connect_args': {
                'ssl_ca': str(MYSQL_CA),
                'ssl_verify_cert': True,
                'ssl_verify_identity': True,
                'connect_timeout': 30
            }
        }

    # Keamanan sesi
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=7)

    # Upload
    MAX_CONTENT_LENGTH = 22 * 1024 * 1024
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')

    # Midtrans
    MIDTRANS_SERVER_KEY = os.environ.get('MIDTRANS_SERVER_KEY', '')
    MIDTRANS_CLIENT_KEY = os.environ.get('MIDTRANS_CLIENT_KEY', '')
    MIDTRANS_IS_PRODUCTION = os.environ.get('MIDTRANS_IS_PRODUCTION', 'False').lower() == 'true'

    # Gmail SMTP
    SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME', '')
    SMTP_APP_PASSWORD = os.environ.get('SMTP_APP_PASSWORD', '').replace(' ', '')
    SMTP_SENDER = os.environ.get('SMTP_SENDER') or SMTP_USERNAME

    # URL tautan reset password
    RESET_PASSWORD_BASE_URL = os.environ.get('RESET_PASSWORD_BASE_URL') or 'http://127.0.0.1:5000'


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    RESET_PASSWORD_BASE_URL = os.environ.get('RESET_PASSWORD_BASE_URL', '')

    if not Config.SECRET_KEY:
        raise RuntimeError('SECRET_KEY wajib diatur pada environment production.')


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False