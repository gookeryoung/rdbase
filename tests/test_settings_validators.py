"""密码策略验证器测试.

验证 ConfigurablePasswordValidator 和 PasswordHistoryValidator。
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest
from apps.settings.validators import (
    ConfigurablePasswordValidator,
    PasswordHistoryValidator,
)

# 正确的 patch 路径：函数在 validators.py 中通过
# ``from apps.settings.models import get_setting_int`` 引入，
# 因此 patch 目标应为 ``apps.settings.models.get_setting_int``。
_MODEL_GET_INT = "apps.settings.models.get_setting_int"
_MODEL_GET_BOOL = "apps.settings.models.get_setting_bool"


class TestConfigurablePasswordValidator:
    """ConfigurablePasswordValidator 测试."""

    def setup_method(self) -> None:
        self.validator = ConfigurablePasswordValidator()

    def test_default_min_length_allows_valid_password(self) -> None:
        """默认最小长度 8 位，合法密码应通过."""
        with patch(_MODEL_GET_INT, return_value=8):
            with patch(_MODEL_GET_BOOL, return_value=False):
                self.validator.validate("goodpass1")

    def test_password_too_short(self) -> None:
        """密码过短应抛 ValidationError."""
        from django.core.exceptions import ValidationError

        with patch(_MODEL_GET_INT, return_value=8):
            with patch(_MODEL_GET_BOOL, return_value=False):
                with pytest.raises(ValidationError) as exc:
                    self.validator.validate("short")
                assert "至少 8 位" in str(exc.value)

    def test_require_uppercase_violated(self) -> None:
        """要求大写但密码不含大写应失败."""
        from django.core.exceptions import ValidationError

        with patch(_MODEL_GET_INT, return_value=8):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_uppercase",
            ):
                with pytest.raises(ValidationError) as exc:
                    self.validator.validate("nouppercase1")
                assert "大写字母" in str(exc.value)

    def test_require_uppercase_passed(self) -> None:
        """含大写字母应通过."""
        with patch(_MODEL_GET_INT, return_value=8):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_uppercase",
            ):
                self.validator.validate("HasUpperCase1")

    def test_require_lowercase_violated(self) -> None:
        from django.core.exceptions import ValidationError

        with patch(_MODEL_GET_INT, return_value=8):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_lowercase",
            ):
                with pytest.raises(ValidationError) as exc:
                    self.validator.validate("NOLOWERCASE1")
                assert "小写字母" in str(exc.value)

    def test_require_digit_violated(self) -> None:
        from django.core.exceptions import ValidationError

        with patch(_MODEL_GET_INT, return_value=8):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_digit",
            ):
                with pytest.raises(ValidationError) as exc:
                    self.validator.validate("noDigitHere")
                assert "数字" in str(exc.value)

    def test_require_special_violated(self) -> None:
        from django.core.exceptions import ValidationError

        with patch(_MODEL_GET_INT, return_value=8):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_special",
            ):
                with pytest.raises(ValidationError) as exc:
                    self.validator.validate("noSpecial1")
                assert "特殊字符" in str(exc.value)

    def test_get_help_text(self) -> None:
        """帮助文本应包含当前策略摘要."""
        with patch(_MODEL_GET_INT, return_value=10):
            with patch(
                _MODEL_GET_BOOL,
                side_effect=lambda key, default=False: key == "password.require_digit",
            ):
                text = self.validator.get_help_text()
                assert "至少 10 位" in text
                assert "包含数字" in text


@pytest.mark.django_db
class TestPasswordHistoryValidator:
    """PasswordHistoryValidator 测试."""

    def setup_method(self) -> None:
        self.validator = PasswordHistoryValidator()

    def test_history_disabled_passes(self) -> None:
        """history_count=0 时跳过检查."""
        from apps.accounts.models import User

        user = User.objects.create_user(
            username="history_user",
            password="TestPass1",
        )
        with patch(_MODEL_GET_INT, return_value=0):
            self.validator.validate("anynewpass1", user=user)

    def test_password_in_history_fails(self) -> None:
        """密码在历史中应失败."""
        from apps.accounts.models import PasswordHistory, User
        from django.core.exceptions import ValidationError

        user = User.objects.create_user(
            username="history_user2",
            password="TestPass1",
        )
        old_hash = hashlib.sha256("OldPassword1".encode("utf-8")).hexdigest()
        PasswordHistory.objects.create(user=user, password_hash=old_hash)

        with patch(_MODEL_GET_INT, return_value=3):
            with pytest.raises(ValidationError) as exc:
                self.validator.validate("OldPassword1", user=user)
            assert "历史密码" in str(exc.value)

    def test_password_not_in_history_passes(self) -> None:
        """密码不在历史中应通过."""
        from apps.accounts.models import PasswordHistory, User

        user = User.objects.create_user(
            username="history_user3",
            password="TestPass1",
        )
        old_hash = hashlib.sha256("OldPassword1".encode("utf-8")).hexdigest()
        PasswordHistory.objects.create(user=user, password_hash=old_hash)

        with patch(_MODEL_GET_INT, return_value=3):
            self.validator.validate("BrandNewPass2", user=user)

    def test_no_history_passes(self) -> None:
        """无历史记录时通过."""
        from apps.accounts.models import User

        user = User.objects.create_user(
            username="history_user4",
            password="TestPass1",
        )
        with patch(_MODEL_GET_INT, return_value=3):
            self.validator.validate("NewPass123", user=user)

    def test_validate_password_stores_history(self) -> None:
        """validate_password 应将密码存入历史."""
        from apps.accounts.models import PasswordHistory, User

        user = User.objects.create_user(
            username="history_user5",
            password="TestPass1",
        )
        self.validator.validate_password("NewPass123", user=user)
        assert PasswordHistory.objects.filter(user=user).count() == 1
        stored = PasswordHistory.objects.get(user=user)
        expected_hash = hashlib.sha256("NewPass123".encode("utf-8")).hexdigest()
        assert stored.password_hash == expected_hash
