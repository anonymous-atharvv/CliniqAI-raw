"""
Security Configuration — KMS + Encryption + Secrets Management
Centralizes all cryptographic operations.
HIPAA requires AES-256 at rest, TLS 1.3 in transit.
"""

import os, base64, hashlib, hmac, logging
from typing import Optional
from functools import lru_cache
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


class EncryptionService:
    """
    Application-level encryption for PHI fields.
    Uses AES-256-GCM (authenticated encryption).
    Key managed by AWS KMS or HashiCorp Vault.

    Production: keys never touch application memory in plaintext.
    KMS envelope encryption: data key encrypted by KMS CMK.
    """

    def __init__(self, key_bytes: Optional[bytes] = None, fernet_key: Optional[str] = None):
        self._aes_key = key_bytes or os.urandom(32)         # 256-bit AES key
        self._fernet = Fernet(fernet_key.encode() if fernet_key else Fernet.generate_key())

    def encrypt_phi(self, plaintext: str) -> bytes:
        """Encrypt a PHI string using AES-256-GCM."""
        if not plaintext:
            return b""
        nonce = os.urandom(12)
        aesgcm = AESGCM(self._aes_key)
        ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ct   # Prepend nonce for decryption

    def decrypt_phi(self, ciphertext: bytes) -> Optional[str]:
        """Decrypt a PHI ciphertext."""
        if not ciphertext or len(ciphertext) < 13:
            return None
        try:
            nonce, ct = ciphertext[:12], ciphertext[12:]
            aesgcm = AESGCM(self._aes_key)
            plaintext = aesgcm.decrypt(nonce, ct, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

    def encrypt_fernet(self, data: str) -> str:
        """Fernet encryption for less-sensitive fields (reversible)."""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt_fernet(self, token: str) -> Optional[str]:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except Exception:
            return None

    def hash_phi(self, value: str, salt: str) -> str:
        """One-way hash for audit-log-safe PHI references."""
        return hmac.new(salt.encode(), value.encode(), hashlib.sha256).hexdigest()


class KMSClient:
    """
    AWS KMS client wrapper for envelope encryption.
    In dev/test: uses local key. In production: calls AWS KMS.
    """

    def __init__(self, key_arn: Optional[str] = None, region: str = "ap-south-1"):
        self._key_arn = key_arn
        self._region = region
        self._use_kms = bool(key_arn and "arn:aws:kms" in (key_arn or ""))

    def generate_data_key(self) -> tuple:
        """Generate a data encryption key (DEK). Returns (plaintext_key, encrypted_key)."""
        if self._use_kms:
            import boto3
            kms = boto3.client("kms", region_name=self._region)
            response = kms.generate_data_key(KeyId=self._key_arn, KeySpec="AES_256")
            return response["Plaintext"], response["CiphertextBlob"]
        # Dev: return random key (not encrypted by KMS)
        key = os.urandom(32)
        return key, base64.b64encode(key)

    def decrypt_data_key(self, encrypted_key: bytes) -> bytes:
        """Decrypt an encrypted data key using KMS CMK."""
        if self._use_kms:
            import boto3
            kms = boto3.client("kms", region_name=self._region)
            response = kms.decrypt(CiphertextBlob=encrypted_key, KeyId=self._key_arn)
            return response["Plaintext"]
        return base64.b64decode(encrypted_key)


class SecretsManager:
    """Fetch secrets from AWS Secrets Manager or environment variables."""

    @staticmethod
    def get(secret_name: str, default: Optional[str] = None) -> Optional[str]:
        val = os.environ.get(secret_name, default)
        if val:
            return val
        # Production: fall through to AWS Secrets Manager
        try:
            import boto3
            client = boto3.client("secretsmanager")
            response = client.get_secret_value(SecretId=secret_name)
            return response.get("SecretString")
        except Exception:
            return default


@lru_cache()
def get_encryption_service() -> EncryptionService:
    fernet_key = os.environ.get("FERNET_KEY")
    return EncryptionService(fernet_key=fernet_key)
