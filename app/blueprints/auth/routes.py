from urllib.parse import urlsplit
from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    session
)
from flask_login import (
    login_user,
    logout_user,
    login_required,
    current_user
)
from app.extensions import db, limiter
from app.models.user import User
from app.models.customer_profile import CustomerProfile
from app.blueprints.auth.forms import (
    LoginForm,
    RegisterForm,
    AdminLoginForm
)
from app.services.audit_logger import log_security_event

auth_bp = Blueprint("auth", __name__)

# ==========================================
# HELPERS
# ==========================================

def clean_email(value):

    return (
        str(value or "")
        .strip()
        .lower()
        .replace("\r", "")
        .replace("\n", "")
    )


def find_user_by_email(email):

    return User.query.filter(
        db.func.lower(User.email) == email
    ).first()


def safe_redirect_target(default_endpoint):

    destination = (
        request.args.get("next") or ""
    ).strip()

    if not destination:
        return url_for(default_endpoint)

    parsed = urlsplit(destination)

    # Hanya izinkan path relatif internal.
    if (
        parsed.scheme
        or parsed.netloc
        or not destination.startswith("/")
        or destination.startswith("//")
        or destination.startswith("/\\")
    ):
        return url_for(default_endpoint)

    # Customer tidak boleh diarahkan ke area admin
    if (
        default_endpoint == "shop.index"
        and (
            parsed.path == "/admin"
            or parsed.path.startswith("/admin/")
            or parsed.path == "/auth/admin-login"
        )
    ):
        return url_for(default_endpoint)

    return destination


# ==========================================
# LOGIN CUSTOMER
# ==========================================

@auth_bp.route(
    "/login",
    methods=["GET", "POST"]
)
@limiter.limit("5 per minute")
def login():

    if current_user.is_authenticated:

        if current_user.role == "admin":
            return redirect(
                url_for("admin.dashboard")
            )

        return redirect(
            url_for("shop.index")
        )

    form = LoginForm()

    if form.validate_on_submit():

        email = clean_email(form.email.data)

        user = find_user_by_email(email)

        # ----------------------------------
        # AKUN TIDAK DITEMUKAN
        # ----------------------------------

        if not user:

            log_security_event(
                action="AUTH_LOGIN_FAILED",
                status="FAILURE",
                username=email,
                details="Account not found"
            )

            flash(
                "Email atau password tidak sesuai.",
                "danger"
            )

            return render_template(
                "auth/login.html",
                form=form
            )

        # ----------------------------------
        # PASSWORD SALAH
        # ----------------------------------

        if not user.check_password(
            form.password.data
        ):

            log_security_event(
                action="AUTH_LOGIN_FAILED",
                status="FAILURE",
                user_id=user.id,
                username=user.email,
                details="Incorrect password"
            )

            flash(
                "Email atau password tidak sesuai.",
                "danger"
            )

            return render_template(
                "auth/login.html",
                form=form
            )

        # ----------------------------------
        # ROLE BUKAN CUSTOMER
        # ----------------------------------

        if user.role != "customer":

            log_security_event(
                action="AUTH_LOGIN_FAILED",
                status="FAILURE",
                user_id=user.id,
                username=user.email,
                details=(
                    "Non-customer account attempted "
                    "customer portal login"
                )
            )

            flash(
                "Akun administrator harus login "
                "melalui Portal Admin.",
                "warning"
            )

            return redirect(
                url_for("auth.admin_login")
            )

        # ----------------------------------
        # AKUN NONAKTIF
        # ----------------------------------

        if not user.is_active:

            log_security_event(
                action="AUTH_LOGIN_FAILED",
                status="FAILURE",
                user_id=user.id,
                username=user.email,
                details="Inactive customer account"
            )

            flash(
                "Akun dinonaktifkan oleh administrator.",
                "danger"
            )

            return render_template(
                "auth/login.html",
                form=form
            )

        # ----------------------------------
        # LOGIN BERHASIL
        # ----------------------------------

        session.clear()

        login_user(
            user,
            remember=bool(form.remember_me.data)
        )

        log_security_event(
            action="AUTH_LOGIN_SUCCESS",
            status="SUCCESS",
            user_id=user.id,
            username=user.email,
            details="Customer login successful"
        )

        flash(
            f"Selamat datang kembali, {user.name}!",
            "success"
        )

        return redirect(
            safe_redirect_target("shop.index")
        )

    # Catat form login yang gagal validasi
    if request.method == "POST":

        log_security_event(
            action="AUTH_LOGIN_FAILED",
            status="FAILURE",
            username=clean_email(
                request.form.get("email")
            ),
            details="Invalid customer login form"
        )

    return render_template(
        "auth/login.html",
        form=form
    )

# ==========================================
# REGISTER CUSTOMER
# ==========================================

@auth_bp.route(
    "/register",
    methods=["GET", "POST"]
)
@limiter.limit("5 per minute")
def register():

    if current_user.is_authenticated:

        if current_user.role == "admin":
            return redirect(
                url_for("admin.dashboard")
            )

        return redirect(
            url_for("shop.index")
        )

    form = RegisterForm()

    if form.validate_on_submit():

        email = clean_email(form.email.data)

        existing_user = find_user_by_email(email)

        # Pertahankan penanganan email duplikat
        if existing_user:

            log_security_event(
                action="AUTH_REGISTER_ENUMERATION_ATTEMPT",
                status="WARNING",
                details=(
                    "Registration attempted "
                    "with an existing account"
                )
            )

            flash(
                "Jika pendaftaran dapat diproses, "
                "silakan lanjutkan melalui halaman login.",
                "info"
            )

            return redirect(
                url_for("auth.login")
            )

        # ----------------------------------
        # MEMBUAT USER CUSTOMER
        # ----------------------------------

        name = str(
            form.name.data or ""
        ).strip()

        new_user = User(
            name=name,
            email=email,
            role="customer",
            is_active=True
        )

        new_user.set_password(
            form.password.data
        )

        db.session.add(new_user)

        # Mendapatkan ID sebelum commit
        db.session.flush()

        # ----------------------------------
        # MENYIMPAN GENDER REGISTRASI
        # ----------------------------------

        gender_input = (
            str(form.gender.data or "")
            .strip()
            .lower()
            if hasattr(form, "gender")
            else ""
        )

        gender_mapping = {
            "man": "men",
            "men": "men",
            "male": "men",
            "pria": "men",

            "women": "women",
            "woman": "women",
            "female": "women",
            "wanita": "women",

            "other": "other",
            "lainnya": "other"
        }

        selected_gender = gender_mapping.get(
            gender_input
        )

        new_profile = CustomerProfile(
            user_id=new_user.id,
            gender=selected_gender
        )

        db.session.add(new_profile)

        db.session.commit()

        log_security_event(
            action="AUTH_REGISTER_SUCCESS",
            status="SUCCESS",
            user_id=new_user.id,
            username=new_user.email,
            details="Customer registration successful"
        )

        flash(
            "Pendaftaran akun berhasil! Silakan login.",
            "success"
        )

        return redirect(
            url_for("auth.login")
        )

    return render_template(
        "auth/register.html",
        form=form
    )

# ==========================================
# LOGOUT
# ==========================================

@auth_bp.route("/logout")
@login_required
def logout():

    previous_role = current_user.role

    log_security_event(
        action="AUTH_LOGOUT",
        status="SUCCESS",
        user_id=current_user.id,
        username=current_user.email,
        details=f"Logout from role: {previous_role}"
    )

    logout_user()
    session.clear()

    flash(
        "Anda telah keluar dari sistem.",
        "info"
    )

    if previous_role == "admin":
        return redirect(
            url_for("auth.admin_login")
        )

    return redirect(
        url_for("auth.login")
    )

# ==========================================
# LOGIN KHUSUS ADMIN
# ==========================================

@auth_bp.route(
    "/admin-login",
    methods=["GET", "POST"]
)
@limiter.limit("5 per minute")
def admin_login():

    # Jika sudah login sebagai admin,
    # arahkan ke Dashboard Admin.
    if (
        current_user.is_authenticated
        and current_user.role == "admin"
    ):

        return redirect(
            url_for("admin.dashboard")
        )

    # Customer tetap boleh membuka form Admin.
    # Sesi customer tidak dihapus hanya
    # karena membuka halaman login ini.
    form = AdminLoginForm()

    if form.validate_on_submit():

        email = clean_email(form.email.data)

        user = find_user_by_email(email)

        # ----------------------------------
        # KREDENSIAL TIDAK VALID
        # ----------------------------------

        if (
            not user
            or not user.check_password(
                form.password.data
            )
        ):

            log_security_event(
                action="ADMIN_LOGIN_FAILED",
                status="FAILURE",
                username=email,
                details="Invalid admin credentials"
            )

            flash(
                "Email atau password salah.",
                "danger"
            )

            return render_template(
                "auth/admin_login.html",
                form=form
            )

        # ----------------------------------
        # BUKAN AKUN ADMIN
        # ----------------------------------

        if user.role != "admin":

            log_security_event(
                action="ADMIN_LOGIN_FAILED",
                status="FAILURE",
                user_id=user.id,
                username=user.email,
                details=(
                    "Customer attempted "
                    "administrator login"
                )
            )

            flash(
                "Akses ditolak. Akun ini "
                "bukan administrator.",
                "danger"
            )

            return render_template(
                "auth/admin_login.html",
                form=form
            )

        # ----------------------------------
        # AKUN ADMIN NONAKTIF
        # ----------------------------------

        if not user.is_active:

            log_security_event(
                action="ADMIN_LOGIN_FAILED",
                status="FAILURE",
                user_id=user.id,
                username=user.email,
                details="Inactive admin account"
            )

            flash(
                "Akun administrator dinonaktifkan.",
                "danger"
            )

            return render_template(
                "auth/admin_login.html",
                form=form
            )

        # ----------------------------------
        # LOGIN ADMIN BERHASIL
        # ----------------------------------

        # Bersihkan sesi pada hostname Admin.
        # Cookie Customer di hostname lain
        # tidak ikut dihapus.
        if current_user.is_authenticated:
            logout_user()

        session.clear()

        login_user(
            user,
            remember=False
        )

        log_security_event(
            action="ADMIN_LOGIN_SUCCESS",
            status="SUCCESS",
            user_id=user.id,
            username=user.email,
            details="Administrator login successful"
        )

        flash(
            "Selamat datang kembali di Portal Admin.",
            "success"
        )

        return redirect(
            url_for("admin.dashboard")
        )

    # Catat POST yang gagal validasi
    if request.method == "POST":

        log_security_event(
            action="ADMIN_LOGIN_FAILED",
            status="FAILURE",
            username=clean_email(
                request.form.get("email")
            ),
            details="Invalid admin login form"
        )

    return render_template(
        "auth/admin_login.html",
        form=form
    )