import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .models import Library, LoginSession, User, now

hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("gmv_session")
    session = db.get(LoginSession, token_hash(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, "请先登录")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "账号已停用")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "页面会话已更新，请刷新后重试")
    request.state.login_session = session
    return user


def admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "此操作需要管理员权限")
    return user


def create_session(db, user, hours):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, csrf=csrf,
                        expires_at=now() + timedelta(hours=hours)))
    db.commit()
    return token, csrf


def bootstrap(db, username, password):
    if db.scalar(select(User.id).where(User.role == "admin")):
        return False
    if len(password) < 12 or password.startswith("change-this"):
        raise ValueError("首次启动必须设置至少 12 位的 GMV_ADMIN_PASSWORD，不能使用示例密码")
    db.add(User(username=username, password_hash=hash_password(password), role="admin"))
    if not db.scalar(select(Library.id).limit(1)):
        db.add(Library(name="生成作品", mode="managed"))
    db.commit()
    return True
