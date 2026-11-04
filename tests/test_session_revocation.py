import pytest

from app import create_app
from app.blueprints.auth.routes import make_reset_token
from app.config import TestingConfig
from app.extensions import db
from app.models.user import User


EMAIL = 'session-test@example.com'
OLD_PASSWORD = 'OldPass1!'
NEW_PASSWORD = 'NewPass2@'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('KURO_SKIP_CREATE_ALL', '1')
    monkeypatch.setattr(TestingConfig, 'RATELIMIT_ENABLED', False, raising=False)

    application = create_app('testing')
    application.config['SECRET_KEY'] = 'test-session-revocation-key'

    with application.app_context():
        db.create_all()
        user = User(name='Session Test', email=EMAIL, role='customer', is_active=True)
        user.set_password(OLD_PASSWORD)
        db.session.add(user)
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_reset_password_mencabut_sesi_lama(app):
    browser_a = app.test_client()
    browser_b = app.test_client()
    browser_reset = app.test_client()

    # Login pada dua browser berbeda
    for browser in (browser_a, browser_b):
        response = browser.post('/auth/login', data={'email': EMAIL, 'password': OLD_PASSWORD})
        assert response.status_code == 302
        assert browser.get('/auth/login').status_code == 302

    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        token = make_reset_token(user)

    # Reset password dari browser ketiga
    response = browser_reset.post(
        f'/auth/reset-password/{token}',
        data={'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
    )
    assert response.status_code == 302

    # Kedua sesi lama tidak lagi dikenali
    assert browser_a.get('/auth/login').status_code == 200
    assert browser_b.get('/auth/login').status_code == 200

    # Password lama ditolak
    response = browser_a.post('/auth/login', data={'email': EMAIL, 'password': OLD_PASSWORD})
    assert response.status_code == 200

    # Password baru diterima
    response = browser_b.post('/auth/login', data={'email': EMAIL, 'password': NEW_PASSWORD})
    assert response.status_code == 302


def test_remember_me_lama_tidak_bisa_login_ulang(app):
    browser = app.test_client()

    # Login menggunakan Remember Me
    with app.app_context():
        response = browser.post('/auth/login', data={
            'email': EMAIL,
            'password': OLD_PASSWORD,
            'remember_me': 'y'
        })
    assert response.status_code == 302

    cookie = browser.get_cookie('remember_token')
    assert cookie is not None, 'Cookie Remember Me tidak dibuat'

    # Cookie harus berfungsi sebelum password diganti
    browser_valid = app.test_client()
    browser_valid.set_cookie('remember_token', cookie.value)

    with app.app_context():
        response = browser_valid.get('/auth/login')
    assert response.status_code == 302

    # Ganti password dan periksa identitas sesi lama
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        old_id = user.get_id()
        user.set_password(NEW_PASSWORD)
        db.session.commit()

        assert user.get_id() != old_id
        assert app.login_manager.user_callback(old_id) is None

    # Browser baru hanya membawa cookie Remember Me lama
    browser_lama = app.test_client()
    browser_lama.set_cookie('remember_token', cookie.value)

    with app.app_context():
        response = browser_lama.get('/auth/login')

    assert response.status_code == 200, (
        f'Cookie lama masih diarahkan ke: {response.headers.get("Location")}'
    )


def test_sesi_dengan_password_belum_berubah_tetap_valid(app):
    browser = app.test_client()
    response = browser.post('/auth/login', data={'email': EMAIL, 'password': OLD_PASSWORD})

    assert response.status_code == 302
    assert browser.get('/auth/login').status_code == 302