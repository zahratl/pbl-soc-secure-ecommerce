import os
from hmac import compare_digest

from flask import Flask, abort, redirect, request, session, url_for
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config, ProductionConfig, TestingConfig
from app.extensions import csrf, db, limiter, login_manager
from app.models.cart import CartItem
from app.models.customer_address import CustomerAddress
from app.models.customer_profile import CustomerProfile
from app.models.order import Order, OrderItem
from app.models.payment import PaymentTransaction
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.models.wishlist import WishlistItem
from app.services.audit_logger import log_security_event, setup_audit_logger
from app.services.customer_encryption_reader import read_customer_private


def create_app(config_mode=None):
    app = Flask(__name__)

    if config_mode == 'testing':
        app.config.from_object(TestingConfig)
    elif os.environ.get('FLASK_ENV') == 'production':
        app.config.from_object(ProductionConfig)
    else:
        app.config.from_object(Config)

    # Reverse proxy
    try:
        proxy_count = int(os.environ.get('TRUSTED_PROXY_COUNT', '0') or 0)
    except ValueError:
        proxy_count = 0

    if proxy_count > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count, x_proto=proxy_count)

    # Extensions
    db.init_app(app)
    app.add_template_global(read_customer_private, name='private_field')
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Silakan login terlebih dahulu untuk mengakses halaman ini.'
    login_manager.login_message_category = 'warning'

    # Validasi sesi login terhadap password terkini
    @login_manager.user_loader
    def load_user(user_id):
        if not isinstance(user_id, str) or ':' not in user_id:
            return None

        try:
            raw_id, _ = user_id.split(':', 1)
            user = db.session.get(User, int(raw_id))
        except (TypeError, ValueError):
            return None

        if not user or not user.is_active:
            return None

        return user if compare_digest(user.get_id(), user_id) else None

    setup_audit_logger(app)

    # Blueprints
    from app.blueprints.shop.routes import shop_bp
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.cart.routes import cart_bp
    from app.blueprints.customer.routes import customer_bp
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.errors.handlers import errors_bp

    app.register_blueprint(shop_bp, url_prefix='/')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(cart_bp, url_prefix='/cart')
    app.register_blueprint(customer_bp, url_prefix='/customer')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(errors_bp)

    @app.before_request
    def protect_application_routes():
        session.pop('shipping_address', None)
        session.pop('shipping_address_user_id', None)

        endpoint = request.endpoint or ''
        blueprint = request.blueprint or ''
        hostname = request.host.split(':')[0].lower()

        # Pisahkan hostname Customer dan Admin saat development
        local_split_enabled = (
            not app.config.get('TESTING', False)
            and os.environ.get('FLASK_ENV') != 'production'
            and hostname in ('127.0.0.1', 'localhost')
        )

        if local_split_enabled:
            is_admin_page = blueprint == 'admin' or endpoint == 'auth.admin_login'
            is_customer_page = (
                blueprint in ('shop', 'cart', 'customer')
                or endpoint in ('auth.login', 'auth.register')
            )

            target_hostname = None

            if is_admin_page and hostname == '127.0.0.1':
                target_hostname = 'localhost'
            elif is_customer_page and hostname == 'localhost':
                target_hostname = '127.0.0.1'

            if target_hostname:
                if request.method not in ('GET', 'HEAD'):
                    abort(400)

                host_parts = request.host.split(':', 1)
                port = f':{host_parts[1]}' if len(host_parts) == 2 else ''
                requested_path = request.full_path.removesuffix('?')

                return redirect(
                    f'{request.scheme}://{target_hostname}{port}{requested_path}',
                    code=302
                )

        # Perlindungan halaman Admin
        if blueprint == 'admin':
            if not current_user.is_authenticated:
                log_security_event(
                    action='ADMIN_ACCESS_DENIED',
                    status='FAILURE',
                    details=f'Unauthenticated access: {request.method} {request.path}'
                )
                return redirect(url_for('auth.admin_login'))

            if current_user.role != 'admin' or not current_user.is_active:
                log_security_event(
                    action='ADMIN_ACCESS_DENIED',
                    status='FAILURE',
                    user_id=current_user.id,
                    username=current_user.email,
                    details=f'Insufficient privileges: {request.method} {request.path}'
                )
                abort(403)

            log_security_event(
                action='ADMIN_ACCESS_GRANTED',
                status='SUCCESS',
                user_id=current_user.id,
                username=current_user.email,
                details=f'Admin route authorized: {request.method} {request.path}'
            )

        # Perlindungan halaman Customer
        if (
            blueprint in ('customer', 'cart')
            and current_user.is_authenticated
            and (current_user.role != 'customer' or not current_user.is_active)
        ):
            abort(403)

    # Hindari membuat tabel otomatis pada deployment
    with app.app_context():
        if os.environ.get('KURO_SKIP_CREATE_ALL') != '1':
            db.create_all()

    @app.after_request
    def set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        return response

    return app