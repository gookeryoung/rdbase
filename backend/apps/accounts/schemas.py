"""accounts 模块的 Pydantic Schema."""

from __future__ import annotations

from datetime import datetime

from ninja import Schema


class RegisterIn(Schema):
    """注册请求."""

    username: str
    password: str
    email: str = ""


class LoginIn(Schema):
    """登录请求."""

    username: str
    password: str


class UserOut(Schema):
    """用户信息响应."""

    id: int
    username: str
    email: str
    role: str
    is_active: bool


class TokenOut(Schema):
    """登录/注册成功响应：access token + 用户信息."""

    access: str
    user: UserOut


class RefreshOut(Schema):
    """刷新 token 响应：新 access token."""

    access: str


class MessageOut(Schema):
    """通用消息响应."""

    detail: str


class PasswordResetIn(Schema):
    """管理员重置密码请求."""

    new_password: str


class PasswordChangeIn(Schema):
    """个人中心修改密码请求."""

    old_password: str
    new_password: str


class RoleUpdateIn(Schema):
    """修改用户角色请求."""

    role: str


# ================================================================
# API Token 相关 Schema
# ================================================================


class ApiTokenCreateIn(Schema):
    """创建 API Token 请求."""

    name: str
    scopes: list[str] = []
    expires_at: datetime | None = None


class ApiTokenOut(Schema):
    """创建/轮换 API Token 响应（含明文，仅此一次返回）."""

    id: int
    name: str
    token: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    is_active: bool
    created_at: datetime


class ApiTokenListItemOut(Schema):
    """API Token 列表项（不含明文，仅展示前缀）."""

    id: int
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    is_active: bool
    created_by_id: int
    created_at: datetime


class ApiTokenListOut(Schema):
    """API Token 列表响应."""

    items: list[ApiTokenListItemOut]
    total: int


class ApiTokenRotateOut(Schema):
    """轮换 API Token 响应（含新明文，仅此一次返回）."""

    id: int
    name: str
    token: str
    prefix: str
    is_active: bool
