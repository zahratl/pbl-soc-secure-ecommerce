from flask import Blueprint
errors_bp = Blueprint('errors', __name__)

@errors_bp.app_errorhandler(404)
def not_found(e):
    return "<h1>404 - Halaman Tidak Ditemukan</h1>", 404