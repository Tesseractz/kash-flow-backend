import base64
import hashlib
import os

from cryptography.fernet import Fernet
from fastapi import HTTPException


def _get_encryption_key() -> bytes:
    """Get or generate encryption key for password storage."""
    key_env = os.getenv("PASSWORD_ENCRYPTION_KEY")
    if key_env:
        try:
            return base64.urlsafe_b64decode(key_env.encode())
        except Exception:
            key_hash = hashlib.sha256(key_env.encode()).digest()
            return base64.urlsafe_b64encode(key_hash[:32])
    else:
        seed = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "default-secret-key")
        key_hash = hashlib.sha256(seed.encode()).digest()
        return base64.urlsafe_b64encode(key_hash[:32])


def _encrypt_password(password: str) -> str:
    """Encrypt password for storage."""
    try:
        key = _get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(password.encode())
        return base64.urlsafe_b64encode(encrypted).decode()
    except Exception:
        return base64.urlsafe_b64encode(password.encode()).decode()


def _decrypt_password(encrypted_password: str) -> str:
    """Decrypt stored password."""
    try:
        key = _get_encryption_key()
        f = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_password.encode())
        decrypted = f.decrypt(encrypted_bytes)
        return decrypted.decode()
    except Exception:
        try:
            return base64.urlsafe_b64decode(encrypted_password.encode()).decode()
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to decrypt password")
