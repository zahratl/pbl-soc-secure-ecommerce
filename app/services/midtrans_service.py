import base64
import hashlib
import json
import urllib.error
import urllib.request
from flask import current_app

# Default payment channels sesuai kebutuhan user: GoPay, QRIS, BRI VA, BNI VA, BCA VA
DEFAULT_ENABLED_PAYMENTS = ["gopay", "qris", "bri_va", "bni_va", "bca_va"]

def get_midtrans_config():
    """Mengambil konfigurasi Midtrans dari environment/config."""
    server_key = current_app.config.get("MIDTRANS_SERVER_KEY", "").strip()
    client_key = current_app.config.get("MIDTRANS_CLIENT_KEY", "").strip()
    is_production = current_app.config.get("MIDTRANS_IS_PRODUCTION", False)
    snap_endpoint = (
        "https://app.midtrans.com/snap/v1/transactions"
        if is_production
        else "https://app.sandbox.midtrans.com/snap/v1/transactions"
    )
    api_endpoint = (
        "https://api.midtrans.com/v2"
        if is_production
        else "https://api.sandbox.midtrans.com/v2"
    )
    return {
        "server_key": server_key,
        "client_key": client_key,
        "is_production": is_production,
        "snap_endpoint": snap_endpoint,
        "api_endpoint": api_endpoint
    }


def request_snap_token(order_id, gross_amount, items, customer, enabled_payments=None):
    """
    Meminta Snap Token resmi ke Midtrans Sandbox/Production API.
    Mendukung channel: GoPay, QRIS, BRI VA, BNI VA, BCA VA.
    """
    config = get_midtrans_config()
    server_key = config["server_key"]
    if not server_key:
        return None, "MIDTRANS_SERVER_KEY belum dikonfigurasi di file .env."
    if enabled_payments is None:
        enabled_payments = DEFAULT_ENABLED_PAYMENTS
    item_details = []
    for item in items:
        item_details.append({
            "id": str(item.get("id") or "KW-ITEM")[:50],
            "price": int(item.get("price") or 0),
            "quantity": int(item.get("quantity") or 1),
            "name": (item.get("name") or "Produk Kuro Wear")[:50]
        })

    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": int(gross_amount)
        },
        "item_details": item_details,
        "customer_details": {
            "first_name": customer.get("first_name", "Pelanggan"),
            "last_name": customer.get("last_name", ""),
            "email": customer.get("email", "customer@kurowear.local"),
            "phone": customer.get("phone", "081234567890"),
            "shipping_address": {
                "first_name": customer.get("first_name", "Pelanggan"),
                "phone": customer.get("phone", "081234567890"),
                "address": customer.get("address", "")
            }
        },
        "enabled_payments": enabled_payments
    }

    auth_str = base64.b64encode(f"{server_key}:".encode("utf-8")).decode("utf-8")
    req = urllib.request.Request(
        config["snap_endpoint"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth_str}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data.get("token"), None
    except urllib.error.HTTPError as error:
        err_body = error.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_msgs = err_json.get("error_messages", [str(error)])
            err_text = ", ".join(err_msgs) if isinstance(err_msgs, list) else str(err_msgs)
        except Exception:
            err_text = err_body or str(error)
        return None, f"Midtrans API Error: {err_text}"
    except Exception as error:
        return None, f"Koneksi Midtrans: {error}"


def verify_signature(order_id, status_code, gross_amount, received_signature):
    """
    Verifikasi HMAC/SHA-512 Signature Key untuk Webhook Midtrans (PBL Keamanan).
    Formula: SHA512(order_id + status_code + gross_amount + ServerKey)
    """
    config = get_midtrans_config()
    server_key = config["server_key"]
    if not server_key or not received_signature:
        return False

    raw_str = f"{order_id}{status_code}{gross_amount}{server_key}"
    calculated = hashlib.sha512(raw_str.encode("utf-8")).hexdigest()
    return calculated.lower() == received_signature.lower()

def check_midtrans_status(order_id):
    """
    Memeriksa status transaksi langsung ke API Midtrans GET /v2/{order_id}/status.
    """
    config = get_midtrans_config()
    server_key = config["server_key"]
    if not server_key:
        return None, "MIDTRANS_SERVER_KEY belum diisi."

    auth_str = base64.b64encode(f"{server_key}:".encode("utf-8")).decode("utf-8")
    url = f"{config['api_endpoint']}/{order_id}/status"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {auth_str}"
        },
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")), None
    except Exception as e:
        return None, str(e)
