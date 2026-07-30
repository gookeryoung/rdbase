"""accounts 模块的 Pydantic Schema."""

from __future__ import annotations

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
