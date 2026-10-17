import io
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    redirect,
    url_for,
    flash
)
from flask_login import (
    login_required,
    current_user
)
from PIL import (
    Image,
    ImageOps,
    UnidentifiedImageError
)
from app.extensions import db
from app.models.customer_profile import CustomerProfile

# ============================================
# BLUEPRINT CUSTOMER
# ============================================
customer_bp = Blueprint(
    "customer",
    __name__
)
# ============================================
# FOLDER AVATAR
# ============================================

def avatar_folder():

    folder = (
        Path(current_app.static_folder)
        / "uploads"
        / "avatars"
    )

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder
# ============================================
# NAMA TAMPILAN CUSTOMER
# ============================================

def get_display_name(user, profile):

    return (
        (profile.display_name if profile else None)
        or user.name
        or user.email.split("@")[0]
    )
# ============================================
# SIMPAN FOTO PROFIL
# ============================================

def save_avatar(upload):
    max_size = 2 * 1024 * 1024
    data = upload.read(max_size + 1)
    if not data or len(data) > max_size:
        raise ValueError(
            "Ukuran foto maksimal 2 MB."
        )
    filename = f"{uuid.uuid4().hex}.webp"
    destination = avatar_folder() / filename
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in (
                "JPEG",
                "PNG",
                "WEBP"
            ):
                raise ValueError(
                    "Foto harus berformat JPG, PNG, atau WEBP."
                )
            if source.width * source.height > 12000000:
                raise ValueError(
                    "Resolusi gambar terlalu besar."
                )
            image = ImageOps.exif_transpose(
                source
            ).convert("RGB")
            image.thumbnail((640, 640))
            image.save(
                destination,
                "WEBP",
                quality=85
            )
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError
    ) as error:

        destination.unlink(missing_ok=True)

        raise ValueError(
            "File gambar tidak valid atau rusak."
        ) from error

    except ValueError:

        destination.unlink(missing_ok=True)
        raise

    return filename
# ============================================
# HAPUS FILE AVATAR
# ============================================
def delete_avatar_file(filename):

    if not filename:
        return

    if (
        Path(filename).name != filename
        or not filename.endswith(".webp")
    ):
        return

    path = avatar_folder() / filename

    path.unlink(missing_ok=True)
# ============================================
# URL PROFIL LAMA
# ============================================

@customer_bp.route("/profile")
@login_required
def profile():

    return redirect(
        url_for("customer.my_profile")
    )
# ============================================
# PROFIL CUSTOMER
# ============================================

@customer_bp.route(
    "/my-profile",
    methods=["GET", "POST"]
)
@login_required
def my_profile():

    # Admin tidak mengakses profil customer
    if current_user.role == "admin":

        return redirect(
            url_for("admin.dashboard")
        )

    if current_user.role != "customer":

        return redirect(
            url_for("shop.index")
        )

    # Mengambil profil milik akun yang login
    profile = db.session.get(
        CustomerProfile,
        current_user.id
    )

    # ========================================
    # PROSES PERUBAHAN DATA
    # ========================================

    if request.method == "POST":

        action = request.form.get(
            "action",
            "save"
        )

        if action not in (
            "save",
            "upload_avatar",
            "delete_avatar"
        ):

            flash(
                "Permintaan tidak valid.",
                "danger"
            )

            return redirect(
                url_for("customer.my_profile")
            )

        # ====================================
        # SIMPAN INFORMASI PRIBADI
        # ====================================

        if action == "save":

            name = request.form.get(
                "display_name", ""
            ).strip()

            phone = request.form.get(
                "phone", ""
            ).strip()

            birth_text = request.form.get(
                "birth_date", ""
            ).strip()

            gender = request.form.get(
                "gender", ""
            ).strip().lower()

            # Validasi nama sesuai User.name
            if not 2 <= len(name) <= 100:

                flash(
                    "Nama harus terdiri dari 2-100 karakter.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            # Validasi nomor WhatsApp
            if (
                len(phone) > 25
                or any(
                    char not in "0123456789+ -()"
                    for char in phone
                )
            ):

                flash(
                    "Nomor WhatsApp tidak valid.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            # Validasi Gender
            if gender not in (
                "",
                "men",
                "women",
                "other"
            ):

                flash(
                    "Pilihan gender tidak valid.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            # Validasi tanggal lahir
            try:

                birth_date = (
                    date.fromisoformat(birth_text)
                    if birth_text
                    else None
                )

            except ValueError:

                flash(
                    "Format tanggal lahir tidak valid.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            if birth_date and (
                birth_date > date.today()
                or birth_date.year < 1900
            ):

                flash(
                    "Tanggal lahir tidak valid.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            # Buat profil hanya jika belum ada
            if profile is None:

                profile = CustomerProfile(
                    user_id=current_user.id
                )

                db.session.add(profile)

            # Simpan ke CustomerProfile
            profile.display_name = name
            profile.phone = phone or None
            profile.birth_date = birth_date
            profile.gender = gender or None
            profile.updated_at = datetime.now(timezone.utc)

            # Sinkronkan nama ke tabel users
            current_user.name = name

            db.session.commit()

            flash(
                "Profil berhasil diperbarui.",
                "success"
            )

        # ====================================
        # UPLOAD FOTO PROFIL
        # ====================================

        elif action == "upload_avatar":

            upload = request.files.get("avatar")

            if not upload or not upload.filename:

                flash(
                    "Silakan pilih foto profil.",
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            try:

                new_filename = save_avatar(upload)

            except ValueError as error:

                flash(
                    str(error),
                    "danger"
                )

                return redirect(
                    url_for("customer.my_profile")
                )

            if profile is None:

                profile = CustomerProfile(
                    user_id=current_user.id
                )

                db.session.add(profile)

            old_filename = profile.avatar_filename

            profile.avatar_filename = new_filename
            profile.updated_at = datetime.now(timezone.utc)

            # Sinkronkan path untuk navbar jika
            # base.html memakai User.avatar_path
            current_user.avatar_path = (
                f"uploads/avatars/{new_filename}"
            )

            try:

                db.session.commit()

            except Exception:

                db.session.rollback()

                delete_avatar_file(new_filename)

                raise

            delete_avatar_file(old_filename)

            flash(
                "Foto profil berhasil diperbarui.",
                "success"
            )

        # ====================================
        # HAPUS FOTO PROFIL
        # ====================================

        elif action == "delete_avatar":

            if profile and profile.avatar_filename:

                old_filename = profile.avatar_filename

                profile.avatar_filename = None
                profile.updated_at = datetime.now(timezone.utc)

                current_user.avatar_path = None

                db.session.commit()

                delete_avatar_file(old_filename)

                flash(
                    "Foto profil berhasil dihapus.",
                    "success"
                )

            else:

                flash(
                    "Belum ada foto profil yang diunggah.",
                    "warning"
                )

        return redirect(
            url_for("customer.my_profile")
        )

    # ========================================
    # TAMPILKAN PROFIL CUSTOMER
    # ========================================

    return render_template(
        "customer/profile.html",
        profile=profile,
        display_name=get_display_name(
            current_user,
            profile
        )
    )


# ============================================
# URL RIWAYAT PESANAN LAMA
# ============================================

@customer_bp.route("/order-history")
@login_required
def order_history():

    return redirect(
        url_for("customer.my_profile")
        + "#orders-section"
    )