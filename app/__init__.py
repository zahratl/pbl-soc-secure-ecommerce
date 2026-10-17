import os
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    abort
)
from flask_login import current_user
from app.config import (
    Config,
    ProductionConfig,
    TestingConfig
)
from app.extensions import (
    db,
    login_manager,
    csrf,
    limiter
)
from app.services.audit_logger import (
    setup_audit_logger,
    log_security_event
)
# Import model agar dikenali SQLAlchemy
from app.models.user import User
from app.models.product import Product, ProductVariant
from app.models.customer_profile import CustomerProfile
from app.models.wishlist import WishlistItem
from app.models.cart import CartItem


def create_app(config_mode=None):

    app = Flask(__name__)

    # ==========================================
    # KONFIGURASI APLIKASI
    # ==========================================

    if config_mode == "testing":

        app.config.from_object(TestingConfig)

    elif os.environ.get("FLASK_ENV") == "production":

        app.config.from_object(ProductionConfig)

    else:

        app.config.from_object(Config)

    # ==========================================
    # INISIALISASI EKSTENSI
    # ==========================================

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # ==========================================
    # FLASK LOGIN CONFIGURATION
    # ==========================================

    login_manager.login_view = "auth.login"

    login_manager.login_message = (
        "Silakan login terlebih dahulu "
        "untuk mengakses halaman ini."
    )

    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):

        try:
            return db.session.get(
                User,
                int(user_id)
            )

        except (TypeError, ValueError):
            return None

    # ==========================================
    # AUDIT LOGGER
    # ==========================================

    setup_audit_logger(app)

    # ==========================================
    # REGISTRASI BLUEPRINTS
    # ==========================================

    from app.blueprints.shop.routes import shop_bp
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.cart.routes import cart_bp
    from app.blueprints.customer.routes import customer_bp
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.errors.handlers import errors_bp

    app.register_blueprint(
        shop_bp,
        url_prefix="/"
    )

    app.register_blueprint(
        auth_bp,
        url_prefix="/auth"
    )

    app.register_blueprint(
        cart_bp,
        url_prefix="/cart"
    )

    app.register_blueprint(
        customer_bp,
        url_prefix="/customer"
    )

    app.register_blueprint(
        admin_bp,
        url_prefix="/admin"
    )

    app.register_blueprint(errors_bp)

    # ==========================================
    # MIDDLEWARE KEAMANAN DAN PEMISAHAN PORTAL
    # ==========================================

    @app.before_request
    def protect_application_routes():

        endpoint = request.endpoint or ""
        blueprint = request.blueprint or ""

        # --------------------------------------
        # A. PEMISAHAN COOKIE PADA LOCALHOST
        # --------------------------------------
        # Berlaku hanya untuk pengujian lokal,
        # bukan konfigurasi produksi.
        #
        # Customer: 127.0.0.1
        # Admin:    localhost

        hostname = request.host.split(":")[0].lower()

        local_split_enabled = (
            not app.config.get("TESTING", False)
            and os.environ.get("FLASK_ENV") != "production"
            and hostname in ("127.0.0.1", "localhost")
        )

        if local_split_enabled:

            is_admin_page = (
                blueprint == "admin"
                or endpoint == "auth.admin_login"
            )

            is_customer_page = (
                blueprint in (
                    "shop",
                    "cart",
                    "customer"
                )
                or endpoint in (
                    "auth.login",
                    "auth.register"
                )
            )

            target_hostname = None

            if is_admin_page and hostname == "127.0.0.1":
                target_hostname = "localhost"

            elif is_customer_page and hostname == "localhost":
                target_hostname = "127.0.0.1"

            if target_hostname is not None:

                # Jangan mengalihkan POST, karena
                # data form tidak boleh hilang.
                if request.method not in ("GET", "HEAD"):
                    abort(400)

                # Pertahankan port development
                host_parts = request.host.split(":", 1)

                port = (
                    ":" + host_parts[1]
                    if len(host_parts) == 2
                    else ""
                )

                requested_path = request.full_path

                if requested_path.endswith("?"):
                    requested_path = requested_path[:-1]

                target_url = (
                    f"{request.scheme}://"
                    f"{target_hostname}{port}"
                    f"{requested_path}"
                )

                return redirect(target_url, code=302)

        # --------------------------------------
        # B. PROTEKSI AREA ADMIN
        # --------------------------------------

        if blueprint == "admin":

            # Belum login
            if not current_user.is_authenticated:

                log_security_event(
                    action="ADMIN_ACCESS_DENIED",
                    status="FAILURE",
                    details=(
                        f"Unauthenticated access: "
                        f"{request.method} {request.path}"
                    )
                )

                return redirect(
                    url_for("auth.admin_login")
                )

            # Role bukan admin atau akun nonaktif
            if (
                current_user.role != "admin"
                or not current_user.is_active
            ):

                log_security_event(
                    action="ADMIN_ACCESS_DENIED",
                    status="FAILURE",
                    user_id=current_user.id,
                    username=current_user.email,
                    details=(
                        f"Insufficient privileges: "
                        f"{request.method} {request.path}"
                    )
                )

                abort(403)

            # Role valid: akses diizinkan
            log_security_event(
                action="ADMIN_ACCESS_GRANTED",
                status="SUCCESS",
                user_id=current_user.id,
                username=current_user.email,
                details=(
                    f"Admin route authorized: "
                    f"{request.method} {request.path}"
                )
            )

        # --------------------------------------
        # C. PROTEKSI AREA PRIVAT CUSTOMER
        # --------------------------------------

        if blueprint in ("customer", "cart"):

            if (
                current_user.is_authenticated
                and (
                    current_user.role != "customer"
                    or not current_user.is_active
                )
            ):
                abort(403)

    # ==========================================
    # DATABASE
    # ==========================================

    # Membuat tabel yang belum tersedia.
    # Tidak menghapus isi tabel yang sudah ada.
    with app.app_context():

        db.create_all()

    # ==========================================
    # SECURITY HEADERS
    # ==========================================

    @app.after_request
    def set_security_headers(response):

        response.headers[
            "X-Content-Type-Options"
        ] = "nosniff"

        response.headers[
            "X-Frame-Options"
        ] = "SAMEORIGIN"

        response.headers[
            "X-XSS-Protection"
        ] = "1; mode=block"

        return response

    return app