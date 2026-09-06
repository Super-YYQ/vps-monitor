import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"{salt}:{hashed}"


def password_matches(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Vault:
    def __init__(self, directory):
        path = Path(directory) / "secret.key"
        if not path.exists():
            with path.open("xb") as file:
                file.write(Fernet.generate_key())
            os.chmod(path, 0o600)
        self.fernet = Fernet(path.read_bytes())

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode() if value else ""

    def decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode() if value else ""


class InstanceLock:
    """One scheduler owns the database; fail fast on accidental multi-worker startup."""

    def __init__(self, directory):
        self.file = (Path(directory) / "instance.lock").open("a+b")
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("数据目录已被另一实例使用；请使用单个 Uvicorn worker") from None

    def close(self):
        self.file.close()
