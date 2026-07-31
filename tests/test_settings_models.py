"""系统设置模型测试.

验证 SystemSetting 模型的创建、读取、更新、删除、typed_value 反序列化、
get_setting/get_setting_int/get_setting_bool 便捷函数。
"""

from __future__ import annotations

import json

import pytest
from apps.settings.models import (
    PRESET_SETTINGS,
    SystemSetting,
    ValueType,
    _deserialize,
    get_setting,
    get_setting_bool,
    get_setting_int,
)


@pytest.mark.django_db
class TestSystemSettingCRUD:
    """SystemSetting CRUD 测试."""

    def test_create_setting(self) -> None:
        """创建设置项应成功."""
        s = SystemSetting.objects.create(
            key="test.key",
            value="hello",
            value_type=ValueType.STR,
            description="测试",
        )
        assert s.pk is not None
        assert s.key == "test.key"
        assert s.value == "hello"
        assert s.value_type == ValueType.STR

    def test_unique_key_constraint(self) -> None:
        """重复 key 应抛 IntegrityError."""
        SystemSetting.objects.create(key="unique.key", value="1")
        with pytest.raises(Exception):
            SystemSetting.objects.create(key="unique.key", value="2")

    def test_ordering_by_key(self) -> None:
        """列表应按 key 升序排列."""
        SystemSetting.objects.create(key="z.key", value="1")
        SystemSetting.objects.create(key="a.key", value="2")
        SystemSetting.objects.create(key="m.key", value="3")
        keys = list(SystemSetting.objects.all().values_list("key", flat=True))
        assert keys == ["a.key", "m.key", "z.key"]

    def test_str(self) -> None:
        """__str__ 应返回 key = value 摘要."""
        s = SystemSetting(key="session.timeout", value="30")
        assert "session.timeout" in str(s)
        assert "30" in str(s)

    def test_typed_value_str(self) -> None:
        """str 类型应原样返回."""
        s = SystemSetting(value="hello", value_type=ValueType.STR)
        assert s.typed_value == "hello"

    def test_typed_value_int(self) -> None:
        """int 类型应反序列化为整数."""
        s = SystemSetting(value="42", value_type=ValueType.INT)
        assert s.typed_value == 42
        assert isinstance(s.typed_value, int)

    def test_typed_value_int_invalid_fallback(self) -> None:
        """int 类型非法值应回退为 0."""
        s = SystemSetting(value="not-a-number", value_type=ValueType.INT)
        assert s.typed_value == 0

    def test_typed_value_bool_true(self) -> None:
        """bool 类型 true/1/yes/on 应返回 True."""
        for val in ("true", "True", "1", "yes", "on"):
            s = SystemSetting(value=val, value_type=ValueType.BOOL)
            assert s.typed_value is True

    def test_typed_value_bool_false(self) -> None:
        """bool 类型其他值应返回 False."""
        s = SystemSetting(value="false", value_type=ValueType.BOOL)
        assert s.typed_value is False

    def test_typed_value_json(self) -> None:
        """json 类型应反序列化为 Python 对象."""
        s = SystemSetting(value='{"a": 1}', value_type=ValueType.JSON)
        assert s.typed_value == {"a": 1}

    def test_typed_value_json_empty(self) -> None:
        """json 类型空值应返回 None."""
        s = SystemSetting(value="", value_type=ValueType.JSON)
        assert s.typed_value is None

    def test_typed_value_json_invalid(self) -> None:
        """json 类型非法值应返回 None."""
        s = SystemSetting(value="{invalid", value_type=ValueType.JSON)
        assert s.typed_value is None


class TestDeserialize:
    """_deserialize 函数测试."""

    def test_str_type(self) -> None:
        assert _deserialize("hello", ValueType.STR) == "hello"

    def test_int_type(self) -> None:
        assert _deserialize("42", ValueType.INT) == 42

    def test_int_type_invalid(self) -> None:
        assert _deserialize("abc", ValueType.INT) == 0

    def test_bool_type_true(self) -> None:
        assert _deserialize("true", ValueType.BOOL) is True

    def test_bool_type_false(self) -> None:
        assert _deserialize("false", ValueType.BOOL) is False

    def test_json_type(self) -> None:
        assert _deserialize('[1, 2, 3]', ValueType.JSON) == [1, 2, 3]


@pytest.mark.django_db
class TestGetSetting:
    """get_setting 便捷函数测试."""

    def test_get_setting_returns_value(self) -> None:
        """存在的 key 应返回类型化的值."""
        SystemSetting.objects.create(
            key="my.int",
            value="100",
            value_type=ValueType.INT,
        )
        val = get_setting("my.int")
        assert val == 100
        assert isinstance(val, int)

    def test_get_setting_missing_returns_default(self) -> None:
        """不存在的 key 应返回默认值."""
        val = get_setting("nonexistent", "fallback")
        assert val == "fallback"

    def test_get_setting_int(self) -> None:
        """get_setting_int 应返回整数."""
        SystemSetting.objects.create(
            key="timeout",
            value="30",
            value_type=ValueType.INT,
        )
        assert get_setting_int("timeout") == 30

    def test_get_setting_int_default(self) -> None:
        """get_setting_int 不存在时返回默认值."""
        assert get_setting_int("missing", 99) == 99

    def test_get_setting_bool(self) -> None:
        """get_setting_bool 应返回布尔值."""
        SystemSetting.objects.create(
            key="enabled",
            value="true",
            value_type=ValueType.BOOL,
        )
        assert get_setting_bool("enabled") is True

    def test_get_setting_bool_default(self) -> None:
        """get_setting_bool 不存在时返回默认值."""
        assert get_setting_bool("missing", True) is True


class TestPresets:
    """PRESET_SETTINGS 常量测试."""

    def test_presets_have_required_keys(self) -> None:
        """预置设置应包含关键配置项."""
        assert "session.access_token_minutes" in PRESET_SETTINGS
        assert "session.refresh_token_days" in PRESET_SETTINGS
        assert "password.min_length" in PRESET_SETTINGS

    def test_presets_format(self) -> None:
        """每个预置项应为 (value, type, desc) 三元组."""
        for key, entry in PRESET_SETTINGS.items():
            assert len(entry) == 3
            value, vtype, desc = entry
            assert isinstance(value, str)
            assert vtype in {ValueType.STR, ValueType.INT, ValueType.BOOL, ValueType.JSON}
            assert isinstance(desc, str)
