"""Identity persistence using Werkzeug hashes and revocable opaque login tokens."""

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing

from email_validator import EmailNotValidError, validate_email
from flask import has_request_context
from werkzeug.security import check_password_hash, generate_password_hash

from database.database import get_db_connection
from services.validation import visible_text

# OWASP's supported scrypt work factor; Werkzeug supplies salt and encoding.
HASH_METHOD = "scrypt:32768:8:3"
DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32), method=HASH_METHOD)
SESSION_LIFETIME = 8 * 60 * 60


def normalize_email(value):
    if not isinstance(value, str) or len(value.strip()) > 254:
        raise ValueError("Enter a valid email address.")
    try:
        result = validate_email(value.strip(), check_deliverability=False, allow_smtputf8=False)
        return result.ascii_email.casefold()
    except EmailNotValidError:
        raise ValueError("Enter a valid email address.") from None


def register_user(email, password, confirmation, store_name=""):
    email = normalize_email(email)
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValueError("Use a password or passphrase of 15–128 characters.")
    if password != confirmation:
        raise ValueError("Passwords do not match.")
    if not isinstance(store_name, str) or len(store_name.strip()) > 100:
        raise ValueError("Store name must be 100 characters or fewer.")
    store_name = visible_text(store_name, "Store name", 100, optional=True) or "My store"
    password_hash = generate_password_hash(password, method=HASH_METHOD)
    with closing(get_db_connection()) as conn:
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                user_id = conn.execute(
                    "INSERT INTO users(email,password_hash) VALUES (?,?)", (email, password_hash)
                ).lastrowid
                store_id = conn.execute(
                    "INSERT INTO stores(name,kind) VALUES (?,'private')", (store_name,)
                ).lastrowid
                conn.execute(
                    "INSERT INTO store_memberships(user_id,store_id,role) VALUES (?,?,'owner')",
                    (user_id, store_id),
                )
        except sqlite3.IntegrityError:
            raise ValueError(
                "Unable to create this account. Try signing in or use a different email."
            ) from None
    return user_id, store_id


def authenticate(email, password):
    try:
        email = normalize_email(email)
    except ValueError:
        email = ""
    with closing(get_db_connection()) as conn:
        row = conn.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
    candidate = password if isinstance(password, str) and len(password) <= 128 else ""
    valid = check_password_hash(row["password_hash"] if row else DUMMY_HASH, candidate)
    return row["id"] if row is not None and valid else None


def digest_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_session(token):
    if isinstance(token, str) and len(token) <= 128:
        with closing(get_db_connection()) as conn:
            with conn:
                conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (digest_token(token),))


def create_login_session(user_id):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with closing(get_db_connection()) as conn:
        with conn:
            membership = conn.execute(
                "SELECT m.store_id FROM store_memberships m JOIN stores s ON s.id=m.store_id WHERE m.user_id=? AND s.kind!='demo' ORDER BY m.store_id LIMIT 1",
                (user_id,),
            ).fetchone()
            if membership is None:
                raise ValueError("No authorized store is available.")
            conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
            conn.execute(
                "INSERT INTO auth_sessions(token_hash,user_id,store_id,expires_at) VALUES (?,?,?,?)",
                (digest_token(token), user_id, membership["store_id"], now + SESSION_LIFETIME),
            )
    return token


def load_identity(token):
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        return None
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            """SELECT u.id AS user_id,u.email,s.id,s.name,s.kind
            FROM auth_sessions a JOIN users u ON u.id=a.user_id
            JOIN store_memberships m ON m.user_id=a.user_id AND m.store_id=a.store_id
            JOIN stores s ON s.id=m.store_id
            WHERE a.token_hash=? AND a.expires_at>? AND s.kind!='demo'""",
            (digest_token(token), int(time.time())),
        ).fetchone()
    return dict(row) if row else None


def consume_rate_limit(secret, action, address, email=""):
    """SQLite-backed fixed windows, shared across workers on one persistent disk.

    Reserve attempts before password verification to make concurrent requests
    count. Neither raw emails nor network addresses are persisted in buckets.
    Never trust X-Forwarded-For without an explicitly configured trusted proxy.
    """
    now = int(time.time())
    windows = [(f"{action}:ip:{address}", 30 if action == "login" else 10)]
    if action == "login":
        try:
            normalized = normalize_email(email)
        except ValueError:
            normalized = str(email).strip().casefold()[:254]
        windows.append((f"login:account:{normalized}", 10))
    allowed = True
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM auth_limits WHERE expires_at<=?", (now,))
            for value, limit in windows:
                bucket = hmac.new(
                    secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                conn.execute(
                    "INSERT INTO auth_limits(bucket,attempts,expires_at) VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1",
                    (bucket, now + 900),
                )
                count = conn.execute(
                    "SELECT attempts FROM auth_limits WHERE bucket=?", (bucket,)
                ).fetchone()["attempts"]
                allowed = allowed and count <= limit
    return allowed


def assign_legacy_owner(email):
    """Explicit local administrator action; never called by registration/login."""
    if has_request_context():
        raise PermissionError("Legacy ownership can only be assigned by a local administrator.")
    email = normalize_email(email)
    with closing(get_db_connection()) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if user is None:
                raise ValueError("Register the intended owner first.")
            if conn.execute("SELECT 1 FROM store_memberships WHERE store_id=1").fetchone():
                raise ValueError("The legacy store already has an owner.")
            conn.execute(
                "INSERT INTO store_memberships(user_id,store_id,role) VALUES (?,1,'owner')",
                (user["id"],),
            )
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user["id"],))
