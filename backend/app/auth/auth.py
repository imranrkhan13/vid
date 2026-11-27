"""Authentication and per-user authorization.

Sessions: random 256-bit token, stored only as SHA-256, delivered as an HttpOnly SameSite=Lax cookie (or Bearer).
Passwords: scrypt. Mutating requests must carry the X-VGOH header (CSRF defense on top of SameSite) and, when an
Origin header is present, it must match the Host. Login/registration are rate limited per IP and per email."""
from __future__ import annotations
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, new_token, sha256, verify_password
from app.database.models import User, UserSession, utcnow
from app.database.session import new_session

COOKIE = "vgoh_session"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, window: float):
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "too many attempts, slow down")
    q.append(now)


def reset_rate_limits():
    _hits.clear()


def get_db():
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def client_ip(req: Request) -> str:
    return (req.headers.get("x-forwarded-for", "").split(",")[0].strip() or (req.client.host if req.client else "?"))


def csrf_guard(req: Request):
    if req.method in ("POST", "PUT", "PATCH", "DELETE"):
        if req.headers.get("x-vgoh") is None:
            raise HTTPException(403, "missing X-VGOH header")
        origin = req.headers.get("origin")
        if origin and settings.cors_origins == [] and origin.split("://", 1)[-1] != req.headers.get("host"):
            raise HTTPException(403, "cross-origin request blocked")


def current_user(req: Request, db: Session = Depends(get_db)) -> User:
    csrf_guard(req)
    tok = req.cookies.get(COOKIE)
    if not tok:
        auth = req.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    if not tok:
        raise HTTPException(401, "not signed in")
    s = db.get(UserSession, sha256(tok))
    if not s or s.expires_at.replace(tzinfo=s.expires_at.tzinfo or utcnow().tzinfo) < utcnow():
        raise HTTPException(401, "session expired")
    u = db.get(User, s.user_id)
    if not u:
        raise HTTPException(401, "unknown user")
    return u


def create_session(db: Session, user: User) -> str:
    tok = new_token()
    db.add(UserSession(token_hash=sha256(tok), user_id=user.id, expires_at=utcnow() + timedelta(days=14)))
    db.commit()
    return tok


def register_user(db: Session, email: str, password: str, name: str | None) -> User:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "invalid email")
    if len(password) < 10:
        raise HTTPException(422, "password must be at least 10 characters")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "an account with this email already exists")
    salt, h = hash_password(password)
    u = User(email=email, name=(name or email.split("@")[0])[:100], pw_salt=salt, pw_hash=h)
    db.add(u)
    db.commit()
    return u


def login_user(db: Session, email: str, password: str) -> User:
    u = db.query(User).filter(User.email == email.strip().lower()).first()
    if not u:
        hash_password(password)  # equalize timing
        raise HTTPException(401, "wrong email or password")
    if not verify_password(password, u.pw_salt, u.pw_hash):
        raise HTTPException(401, "wrong email or password")
    return u


def owned(db: Session, model, obj_id: str, user: User, field: str = "user_id"):
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, field) != user.id:
        raise HTTPException(404, "not found")  # 404, not 403: do not reveal other users' ids
    return obj
