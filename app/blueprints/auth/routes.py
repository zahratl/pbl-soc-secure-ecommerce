import hashlib
import hmac
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.blueprints.auth.forms import (
    AdminLoginForm,
    ForgotPasswordForm,
    LoginForm,
    RegisterForm,
    ResetPasswordForm
)
from app.extensions import db, limiter
from app.models.customer_profile import CustomerProfile
from app.models.user import User
from app.services.audit_logger import log_security_event
from app.services.email_service import EmailDeliveryError, send_email


auth_bp = Blueprint('auth', __name__)
RESET_TOKEN_TTL = 30 * 60


# Helpers

def clean_email(value):
    return str(value or '').strip().lower().replace('\r', '').replace('\n', '')


def find_user_by_email(email):
    return User.query.filter(db.func.lower(User.email) == email).first()


def safe_redirect_target(default_endpoint):
    destination = (request.args.get('next') or '').strip()

    if not destination:
        return url_for(default_endpoint)

    parsed = urlsplit(destination)

    if parsed.scheme or parsed.netloc or not destination.startswith('/') or destination.startswith('//') or destination.startswith('/\\'):
        return url_for(default_endpoint)

    if default_endpoint == 'shop.index' and (
        parsed.path == '/admin'
        or parsed.path.startswith('/admin/')
        or parsed.path == '/auth/admin-login'
    ):
        return url_for(default_endpoint)

    return destination


# Password Reset Token

def password_fingerprint(user):
    return hashlib.sha256(user.password_hash.encode('utf-8')).hexdigest()


def reset_serializer():
    return URLSafeTimedSerializer(
        current_app.config['SECRET_KEY'],
        salt='kurowear-password-reset-v1'
    )


def make_reset_token(user):
    return reset_serializer().dumps({
        'user_id': user.id,
        'fingerprint': password_fingerprint(user)
    })


def find_reset_user(token):
    if len(token) > 512:
        return None

    try:
        data = reset_serializer().loads(token, max_age=RESET_TOKEN_TTL)
    except BadSignature:
        return None

    if not isinstance(data, dict):
        return None

    if type(data.get('user_id')) is not int or not isinstance(data.get('fingerprint'), str):
        return None

    user = db.session.get(User, data['user_id'])

    if not user or not user.is_active:
        return None

    valid = hmac.compare_digest(data['fingerprint'], password_fingerprint(user))
    return user if valid else None


def reset_link(token):
    base_url = (current_app.config.get('RESET_PASSWORD_BASE_URL') or '').rstrip('/')
    parsed = urlsplit(base_url)

    invalid = (
        not base_url
        or parsed.scheme not in ('http', 'https')
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in ('', '/')
        or parsed.query
        or parsed.fragment
    )

    if invalid:
        raise ValueError('RESET_PASSWORD_BASE_URL belum valid')

    if current_app.config.get('DEBUG') is False and current_app.config.get('SESSION_COOKIE_SECURE') and parsed.scheme != 'https':
        raise ValueError('RESET_PASSWORD_BASE_URL harus menggunakan HTTPS di production')

    return f"{base_url}{url_for('auth.reset_password', token=token)}"


@auth_bp.after_request
def reset_security_headers(response):
    if request.endpoint in ('auth.forgot_password', 'auth.reset_password'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'

    return response


# Login Customer

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('5 per minute')
def login():
    if current_user.is_authenticated:
        endpoint = 'admin.dashboard' if current_user.role == 'admin' else 'shop.index'
        return redirect(url_for(endpoint))

    form = LoginForm()

    if form.validate_on_submit():
        email = clean_email(form.email.data)
        user = find_user_by_email(email)

        if not user:
            log_security_event(
                action='AUTH_LOGIN_FAILED',
                status='FAILURE',
                username=email,
                details='Account not found'
            )
            flash('Email atau password tidak sesuai.', 'danger')
            return render_template('auth/login.html', form=form)

        if not user.check_password(form.password.data):
            log_security_event(
                action='AUTH_LOGIN_FAILED',
                status='FAILURE',
                user_id=user.id,
                username=user.email,
                details='Incorrect password'
            )
            flash('Email atau password tidak sesuai.', 'danger')
            return render_template('auth/login.html', form=form)

        if user.role != 'customer':
            log_security_event(
                action='AUTH_LOGIN_FAILED',
                status='FAILURE',
                user_id=user.id,
                username=user.email,
                details='Non-customer account attempted customer portal login'
            )
            flash('Akun administrator harus login melalui Portal Admin.', 'warning')
            return redirect(url_for('auth.admin_login'))

        if not user.is_active:
            log_security_event(
                action='AUTH_LOGIN_FAILED',
                status='FAILURE',
                user_id=user.id,
                username=user.email,
                details='Inactive customer account'
            )
            flash('Akun dinonaktifkan oleh administrator.', 'danger')
            return render_template('auth/login.html', form=form)

        session.clear()
        login_user(user, remember=bool(form.remember_me.data))

        log_security_event(
            action='AUTH_LOGIN_SUCCESS',
            status='SUCCESS',
            user_id=user.id,
            username=user.email,
            details='Customer login successful'
        )

        flash(f'Selamat datang kembali, {user.name}!', 'success')
        return redirect(safe_redirect_target('shop.index'))

    if request.method == 'POST':
        log_security_event(
            action='AUTH_LOGIN_FAILED',
            status='FAILURE',
            username=clean_email(request.form.get('email')),
            details='Invalid customer login form'
        )

    return render_template('auth/login.html', form=form)


# Register Customer

@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit('5 per minute')
def register():
    if current_user.is_authenticated:
        endpoint = 'admin.dashboard' if current_user.role == 'admin' else 'shop.index'
        return redirect(url_for(endpoint))

    form = RegisterForm()

    if form.validate_on_submit():
        email = clean_email(form.email.data)

        if find_user_by_email(email):
            log_security_event(
                action='AUTH_REGISTER_ENUMERATION_ATTEMPT',
                status='WARNING',
                details='Registration attempted with an existing account'
            )
            flash('Jika pendaftaran dapat diproses, silakan lanjutkan melalui halaman login.', 'info')
            return redirect(url_for('auth.login'))

        user = User(
            name=str(form.name.data or '').strip(),
            email=email,
            role='customer',
            is_active=True
        )
        user.set_password(form.password.data)

        db.session.add(user)
        db.session.flush()

        gender = str(form.gender.data or '').strip().lower() if hasattr(form, 'gender') else ''
        gender_mapping = {
            'man': 'men',
            'men': 'men',
            'male': 'men',
            'pria': 'men',
            'women': 'women',
            'woman': 'women',
            'female': 'women',
            'wanita': 'women',
            'other': 'other',
            'lainnya': 'other'
        }

        db.session.add(CustomerProfile(
            user_id=user.id,
            gender=gender_mapping.get(gender)
        ))
        db.session.commit()

        log_security_event(
            action='AUTH_REGISTER_SUCCESS',
            status='SUCCESS',
            user_id=user.id,
            username=user.email,
            details='Customer registration successful'
        )

        flash('Pendaftaran akun berhasil! Silakan login.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html', form=form)


# Logout

@auth_bp.route('/logout')
@login_required
def logout():
    role = current_user.role

    log_security_event(
        action='AUTH_LOGOUT',
        status='SUCCESS',
        user_id=current_user.id,
        username=current_user.email,
        details=f'Logout from role: {role}'
    )

    logout_user()
    session.clear()

    flash('Anda telah keluar dari sistem.', 'info')
    endpoint = 'auth.admin_login' if role == 'admin' else 'auth.login'
    return redirect(url_for(endpoint))


# Login Admin

@auth_bp.route('/admin-login', methods=['GET', 'POST'])
@limiter.limit('5 per minute')
def admin_login():
    if current_user.is_authenticated and current_user.role == 'admin':
        return redirect(url_for('admin.dashboard'))

    form = AdminLoginForm()

    if form.validate_on_submit():
        email = clean_email(form.email.data)
        user = find_user_by_email(email)

        if not user or not user.check_password(form.password.data):
            log_security_event(
                action='ADMIN_LOGIN_FAILED',
                status='FAILURE',
                username=email,
                details='Invalid admin credentials'
            )
            flash('Email atau password salah.', 'danger')
            return render_template('auth/admin_login.html', form=form)

        if user.role != 'admin':
            log_security_event(
                action='ADMIN_LOGIN_FAILED',
                status='FAILURE',
                user_id=user.id,
                username=user.email,
                details='Customer attempted administrator login'
            )
            flash('Akses ditolak. Akun ini bukan administrator.', 'danger')
            return render_template('auth/admin_login.html', form=form)

        if not user.is_active:
            log_security_event(
                action='ADMIN_LOGIN_FAILED',
                status='FAILURE',
                user_id=user.id,
                username=user.email,
                details='Inactive admin account'
            )
            flash('Akun administrator dinonaktifkan.', 'danger')
            return render_template('auth/admin_login.html', form=form)

        if current_user.is_authenticated:
            logout_user()

        session.clear()
        login_user(user, remember=False)

        log_security_event(
            action='ADMIN_LOGIN_SUCCESS',
            status='SUCCESS',
            user_id=user.id,
            username=user.email,
            details='Administrator login successful'
        )

        flash('Selamat datang kembali di Portal Admin.', 'success')
        return redirect(url_for('admin.dashboard'))

    if request.method == 'POST':
        log_security_event(
            action='ADMIN_LOGIN_FAILED',
            status='FAILURE',
            username=clean_email(request.form.get('email')),
            details='Invalid admin login form'
        )

    return render_template('auth/admin_login.html', form=form)


# Forgot Password

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def forgot_password():
    form = ForgotPasswordForm()

    if form.validate_on_submit():
        user = find_user_by_email(clean_email(form.email.data))

        if user and user.is_active:
            try:
                link = reset_link(make_reset_token(user))
                body = (
                    f'Halo {user.name},\n\n'
                    'Klik tautan berikut untuk mengatur ulang password KURO WEAR:\n'
                    f'{link}\n\n'
                    'Tautan berlaku 30 menit dan tidak dapat digunakan kembali '
                    'setelah password berhasil diganti.\n\n'
                    'Jika kamu tidak meminta reset password, abaikan email ini.'
                )

                send_email(user.email, 'Reset Password KURO WEAR', body)

            except (EmailDeliveryError, ValueError) as exc:
                current_app.logger.warning(
                    'Password reset delivery failed: %s',
                    type(exc).__name__
                )

        flash(
            'Jika email terdaftar dan aktif, tautan reset password akan dikirim ke email tersebut.',
            'info'
        )
        return redirect(url_for('auth.forgot_password'))

    return render_template('auth/forgot_password.html', form=form)


# Reset Password

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def reset_password(token):
    user = find_reset_user(token)

    if not user:
        flash(
            'Tautan reset password tidak valid atau sudah kedaluwarsa. Silakan minta tautan baru.',
            'warning'
        )
        return redirect(url_for('auth.forgot_password'))

    form = ResetPasswordForm()

    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()

        log_security_event(
            action='AUTH_PASSWORD_RESET_SUCCESS',
            status='SUCCESS',
            user_id=user.id,
            username=user.email,
            details='Password reset via verified email token'
        )

        if current_user.is_authenticated:
            logout_user()

        session.clear()
        flash('Password berhasil diperbarui. Silakan login menggunakan password baru.', 'success')

        endpoint = 'auth.admin_login' if user.role == 'admin' else 'auth.login'
        return redirect(url_for(endpoint))

    return render_template('auth/reset_password.html', form=form)