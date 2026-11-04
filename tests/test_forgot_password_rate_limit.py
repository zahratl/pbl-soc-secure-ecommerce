from unittest.mock import patch

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('KURO_SKIP_CREATE_ALL', '1')
    monkeypatch.setattr(TestingConfig, 'RATELIMIT_ENABLED', True, raising=False)
    monkeypatch.setattr(TestingConfig, 'RATELIMIT_STORAGE_URI', 'memory://', raising=False)

    application = create_app('testing')
    application.config['SECRET_KEY'] = 'test-rate-limit-secret'

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


def test_forgot_password_dibatasi_5_per_jam(app):
    client = app.test_client()
    ip = {'REMOTE_ADDR': '198.51.100.50'}

    with patch('app.blueprints.auth.routes.send_email') as send_email:
        statuses = [
            client.post('/auth/forgot-password', data={'email': 'unknown@example.com'},
                        environ_overrides=ip).status_code
            for _ in range(6)
        ]
        send_email.assert_not_called()

    assert statuses == [302, 302, 302, 302, 302, 429]


def test_ip_lain_dan_get_tetap_bisa(app):
    client = app.test_client()
    ip = {'REMOTE_ADDR': '198.51.100.60'}

    for _ in range(6):
        client.post('/auth/forgot-password', data={'email': 'unknown@example.com'},
                    environ_overrides=ip)

    response_get = client.get('/auth/forgot-password', environ_overrides=ip)
    response_ip_lain = client.post('/auth/forgot-password', data={'email': 'unknown@example.com'},
                                    environ_overrides={'REMOTE_ADDR': '198.51.100.61'})
    response_login = client.get('/auth/login', environ_overrides=ip)

    assert response_get.status_code == 200
    assert response_ip_lain.status_code == 302
    assert response_login.status_code == 200


def test_reset_password_dibatasi_10_per_jam(app):
    client = app.test_client()
    ip = {'REMOTE_ADDR': '198.51.100.70'}

    statuses = [
        client.post('/auth/reset-password/token-palsu', environ_overrides=ip).status_code
        for _ in range(11)
    ]

    assert statuses == [302] * 10 + [429]