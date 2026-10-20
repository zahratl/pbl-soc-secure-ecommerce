import os
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from flask import request

audit_logger = logging.getLogger("WazuhSecurityAudit")

def setup_audit_logger(app):
    audit_logger.setLevel(logging.INFO)
    log_dir = os.path.join(app.root_path, '..', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'security_audit.json')

    handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    audit_logger.addHandler(handler)

def log_security_event(action, status, user_id=None, username=None, details=""):
    """Format Log JSON Terstruktur untuk Wazuh SIEM"""
    client_ip = "127.0.0.1"
    endpoint = "N/A"
    method = "N/A"
    user_agent = "N/A"

    try:
        if request:
            client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if client_ip and ',' in client_ip:
                client_ip = client_ip.split(',')[0].strip()
            endpoint = request.path
            method = request.method
            user_agent = request.user_agent.string
    except RuntimeError:
        pass

    log_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "app_name": "pbl_ecommerce",
        "event_type": "security_audit",
        "action": action,
        "status": status,
        "client_ip": client_ip,
        "user_id": user_id,
        "username": username or "anonymous",
        "endpoint": endpoint,
        "http_method": method,
        "user_agent": user_agent,
        "details": details
    }
    audit_logger.info(json.dumps(log_payload))