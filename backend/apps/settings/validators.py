"""系统设置自定义密码验证器.

从 SystemSetting 读取密码策略配置（最小长度、大写/小写/数字/特殊字符要求），
供 Django AUTH_PASSWORD_VALIDATORS 使用。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from django.core.exceptions import ValidationError


class ConfigurablePasswordValidator:
    """可配置密码验证器.

    从 SystemSetting 读取以下配置项：
    - ``password.min_length``：密码最小长度（默认 8）
    - ``password.require_uppercase``：须包含大写字母（默认 false）
    - ``password.require_lowercase``：须包含小写字母（默认 false）
    - ``password.require_digit``：须包含数字（默认 false）
    - ``password.require_special``：须包含特殊字符（默认 false）
    """

    def __init__(self) -> None:
        """初始化（Django 会自动实例化，无需参数）."""

    def validate(self, password: str, user: Any = None) -> None:  # noqa: ARG002
        """校验密码，不通过时抛 ValidationError."""
        from apps.settings.models import get_setting_bool, get_setting_int

        min_length = get_setting_int("password.min_length", 8)
        if len(password) < min_length:
            raise ValidationError(
                f"密码至少 {min_length} 位",
                code="password_too_short",
            )
        if get_setting_bool("password.require_uppercase", False) and not re.search(r"[A-Z]", password):
            raise ValidationError(
                "密码须包含至少一个大写字母",
                code="password_no_uppercase",
            )
        if get_setting_bool("password.require_lowercase", False) and not re.search(r"[a-z]", password):
            raise ValidationError(
                "密码须包含至少一个小写字母",
                code="password_no_lowercase",
            )
        if get_setting_bool("password.require_digit", False) and not re.search(r"[0-9]", password):
            raise ValidationError(
                "密码须包含至少一个数字",
                code="password_no_digit",
            )
        if get_setting_bool("password.require_special", False) and not re.search(r"[^A-Za-z0-9]", password):
            raise ValidationError(
                "密码须包含至少一个特殊字符",
                code="password_no_special",
            )

    def get_help_text(self) -> str:  # noqa: ARG002
        """返回帮助文本."""
        from apps.settings.models import get_setting_bool, get_setting_int

        min_length = get_setting_int("password.min_length", 8)
        requirements: list[str] = [f"至少 {min_length} 位"]
        if get_setting_bool("password.require_uppercase", False):
            requirements.append("包含大写字母")
        if get_setting_bool("password.require_lowercase", False):
            requirements.append("包含小写字母")
        if get_setting_bool("password.require_digit", False):
            requirements.append("包含数字")
        if get_setting_bool("password.require_special", False):
            requirements.append("包含特殊字符")
        return "；".join(requirements)


class PasswordHistoryValidator:
    """密码历史检查验证器.

    防止用户重复使用最近 N 次密码。从 SystemSetting 读取 ``password.history_count``，
    0 表示不检查。历史密码通过 hashlib.sha256 存储在 PasswordHistory 模型中。
    """

    def __init__(self) -> None:
        """初始化."""

    def validate(self, password: str, user: Any = None) -> None:
        """校验密码是否在历史中."""
        from apps.settings.models import get_setting_int

        count = get_setting_int("password.history_count", 0)
        if count <= 0 or user is None:
            return
        from apps.accounts.models import PasswordHistory
        from apps.accounts.models import User as UserModel

        if not isinstance(user, UserModel):
            return
        # 获取最近 N 条历史密码哈希
        history = PasswordHistory.objects.filter(user=user).order_by("-created_at")[:count]
        if not history:
            return
        password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        recent_hashes = {h.password_hash for h in history}
        if password_hash in recent_hashes:
            raise ValidationError(
                f"密码与最近 {count} 次历史密码重复",
                code="password_reused",
            )

    def validate_password(self, password: str, user: Any = None) -> None:  # noqa: D401
        """更新密码时将旧密码加入历史（由 django.contrib.auth 密码重置流程调用）."""
        from apps.accounts.models import PasswordHistory
        from apps.accounts.models import User as UserModel

        if user is None or not isinstance(user, UserModel):
            return
        password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        PasswordHistory.objects.create(user=user, password_hash=password_hash)


__all__ = ["ConfigurablePasswordValidator", "PasswordHistoryValidator"]
