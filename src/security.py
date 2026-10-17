from datetime import datetime, timedelta, timezone
from src.config import settings
import jwt
from pwdlib import PasswordHash
from src.config import settings
import hashlib
import secrets

password_hash = PasswordHash.recommended()  # argon2

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)

def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": subject, "exp": expire},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)

def hash_token(token: str) -> str:
    # SHA-256 is fine here: the token is high-entropy random, not a password
    return hashlib.sha256(token.encode()).hexdigest()

# Used to keep login timing similar when the email does not exist
SALTED_HASH = hash_password(settings.salted_hash + "salt")