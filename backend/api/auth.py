"""
Accounts and bearer-token auth.

Passwords are stored as bcrypt hashes. A successful register or login returns
a signed JWT (HS256) carrying the user id and an expiry; every authenticated
route verifies the signature and expiry, then loads the user.
"""

import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import config
from backend.core.ratelimit import TokenBucket, limiter
from backend.db.models import User
from backend.db.session import get_db, session_scope

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 10 attempts, refilled one every 30s, per client. Slows password guessing.
auth_bucket = TokenBucket(capacity=10, rate=1 / 30)

# Compared against when the email is unknown, so a miss costs the same bcrypt
# work as a hit and response time does not reveal which emails exist.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v) or len(v) > 320 or "\x00" in v:  # Postgres rejects NUL in text
            raise ValueError("invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _valid_password(cls, v: str) -> str:
        # bcrypt only reads the first 72 bytes; refuse longer rather than truncate silently
        if not 8 <= len(v.encode()) <= 72:
            raise ValueError("password must be 8 to 72 bytes")
        if "\x00" in v:  # bcrypt refuses NUL bytes
            raise ValueError("password must not contain NUL bytes")
        return v


def issue_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + timedelta(hours=config.JWT_TTL_HOURS)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def _user_from_token(token: str) -> User | None:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    # A session of its own, closed before this returns. The request-scoped
    # session would keep a pooled connection (idle in transaction on Postgres)
    # for as long as the handler runs, which for an itinerary is the whole
    # pipeline. The User comes back detached: its columns are loaded and
    # readable, and nothing else is needed from it.
    with session_scope() as db:
        return db.get(User, str(payload.get("sub", "")))


def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User | None:
    """The signed-in user, or None for anonymous requests. A bad token is a 401."""
    if creds is None:
        return None
    user = _user_from_token(creds.credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


@router.post("/register", status_code=201, dependencies=[Depends(limiter(auth_bucket))])
def register(creds: Credentials, db: Session = Depends(get_db)):
    user = User(email=creds.email,
                password_hash=bcrypt.hashpw(creds.password.encode(), bcrypt.gensalt()).decode())
    db.add(user)
    try:
        # Committed here, not in get_db's teardown: teardown runs after the
        # response has been sent, so the client could use its new token before
        # the account existed, and a failed commit would still have answered 201.
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    return {"token": issue_token(user.id), "user": {"id": user.id, "email": user.email}}


@router.post("/login", dependencies=[Depends(limiter(auth_bucket))])
def login(creds: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == creds.email))
    stored = user.password_hash.encode() if user else _DUMMY_HASH
    if not bcrypt.checkpw(creds.password.encode(), stored) or user is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"token": issue_token(user.id), "user": {"id": user.id, "email": user.email}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email}
