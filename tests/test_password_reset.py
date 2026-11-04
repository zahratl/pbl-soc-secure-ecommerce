
import time
from unittest.mock import patch

import pytest

from app import create_app
from app.blueprints.auth.routes import find_reset_user, make_reset_token
from app.config import TestingConfig
from app.extensions import db
from app.models.user import User
from app.services.email_service import EmailDeliveryError


EMAIL = 'reset-test@example.com'
OLD_PASSWORD = 'OldPass1!'
NEW_PASSWORD = 'NewPass2@'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('KURO_SKIP_CREATE_ALL', '1')
    monkeypatch.setattr(TestingConfig, 'RATELIMIT_ENABLED', False, raising=False)

    application = create_app('testing')
    application.config.update(
        SECRET_KEY='test-only-password-reset-secret',
        RESET_PASSWORD_BASE_URL='http://127.0.0.1:5000'
    )

    assert application.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///:memory:'

    with application.app_context():
        db.create_all()
        user = User(name='Test Customer', email=EMAIL, role='customer', is_active=True)
        user.set_password(OLD_PASSWORD)
        db.session.add(user)
        db.session.commit()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def get_token(app):
    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        return make_reset_token(user)


def test_email_tidak_terdaftar(client):
    with patch('app.blueprints.auth.routes.send_email') as send:
        response = client.post('/auth/forgot-password', data={'email': 'unknown@example.com'}, follow_redirects=True)

    assert response.status_code == 200
    assert b'Jika email terdaftar dan aktif' in response.data
    send.assert_not_called()


def test_email_terdaftar(client):
    with patch('app.blueprints.auth.routes.send_email') as send:
        response = client.post('/auth/forgot-password', data={'email': EMAIL}, follow_redirects=True)

    assert response.status_code == 200
    assert b'Jika email terdaftar dan aktif' in response.data
    send.assert_called_once()

    args = send.call_args.args
    assert args[0] == EMAIL
    assert 'Reset Password KURO WEAR' in args[1]
    assert 'http://127.0.0.1:5000/auth/reset-password/' in args[2]


def test_token_palsu_ditolak(client, app):
    assert client.get('/auth/reset-password/token-palsu').status_code == 302

    token = get_token(app)
    assert client.get(f'/auth/reset-password/{token}.invalid').status_code == 302


def test_token_kedaluwarsa(app):
    token = get_token(app)
    future = int(time.time()) + 1900

    with app.app_context():
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=future):
            assert find_reset_user(token) is None


def test_token_sekali_pakai_dan_password_baru(client, app):
    token = get_token(app)
    response = client.post(
        f'/auth/reset-password/{token}',
        data={'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
    )

    assert response.status_code == 302

    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        assert user.check_password(NEW_PASSWORD)
        assert not user.check_password(OLD_PASSWORD)
        assert find_reset_user(token) is None

    assert client.get(f'/auth/reset-password/{token}').status_code == 302

    old_login = client.post('/auth/login', data={'email': EMAIL, 'password': OLD_PASSWORD})
    assert old_login.status_code == 200
    assert b'Email atau password tidak sesuai.' in old_login.data

    new_login = client.post('/auth/login', data={'email': EMAIL, 'password': NEW_PASSWORD})
    assert new_login.status_code == 302


def test_password_lemah_ditolak(client, app):
    token = get_token(app)
    response = client.post(
        f'/auth/reset-password/{token}',
        data={'password': '12345678', 'confirm_password': '12345678'}
    )

    assert response.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        assert user.check_password(OLD_PASSWORD)
        assert find_reset_user(token) is not None


def test_konfirmasi_password_berbeda(client, app):
    token = get_token(app)
    response = client.post(
        f'/auth/reset-password/{token}',
        data={'password': NEW_PASSWORD, 'confirm_password': 'Different2@'}
    )

    assert response.status_code == 200
    assert b'Password konfirmasi tidak cocok' in response.data

    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        assert user.check_password(OLD_PASSWORD)


def test_akun_nonaktif_tidak_bisa_reset(client, app):
    token = get_token(app)

    with app.app_context():
        user = User.query.filter_by(email=EMAIL).first()
        user.is_active = False
        db.session.commit()
        assert find_reset_user(token) is None

    with patch('app.blueprints.auth.routes.send_email') as send:
        response = client.post('/auth/forgot-password', data={'email': EMAIL})

    assert response.status_code == 302
    send.assert_not_called()


def test_csrf_wajib(client, app):
    app.config['WTF_CSRF_ENABLED'] = True

    response = client.post('/auth/forgot-password', data={'email': EMAIL})
    assert response.status_code == 400

    token = get_token(app)
    response = client.post(
        f'/auth/reset-password/{token}',
        data={'password': NEW_PASSWORD, 'confirm_password': NEW_PASSWORD}
    )
    assert response.status_code == 400


def test_kegagalan_smtp_tidak_bocor(client):
    error = EmailDeliveryError('SMTP internal test failure')

    with patch('app.blueprints.auth.routes.send_email', side_effect=error):
        response = client.post('/auth/forgot-password', data={'email': EMAIL}, follow_redirects=True)

    assert response.status_code == 200
    assert b'Jika email terdaftar dan aktif' in response.data
    assert b'SMTP internal test failure' not in response.data


def test_host_header_tidak_mengubah_reset_link(client):
    with patch('app.blueprints.auth.routes.send_email') as send:
        response = client.post(
            '/auth/forgot-password',
            data={'email': EMAIL},
            headers={'Host': 'attacker.example'}
        )

    assert response.status_code == 302
    send.assert_called_once()

    body = send.call_args.args[2]
    assert 'http://127.0.0.1:5000/auth/reset-password/' in body
    assert 'attacker.example' not in body