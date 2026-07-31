"""settings Router - 系统设置管理接口.

仅管理员可访问。提供：
- GET /settings：列表查询
- PATCH /settings/{key}：更新单个设置
- GET /settings/rotate-key：加密密钥轮换（重新加密所有数据源密码）
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Router
from ninja.errors import HttpError

from apps.accounts.auth import JWTAuth
from apps.accounts.permissions import require_admin
from apps.datasources.models import DataSource

from .models import PRESET_SETTINGS, SystemSetting
from .schemas import (
    MessageOut,
    RotateKeyIn,
    RotateKeyOut,
    SystemSettingListOut,
    SystemSettingOut,
    SystemSettingUpdateIn,
)

router = Router(tags=["settings"], auth=JWTAuth())


def _setting_dict(setting: SystemSetting) -> dict[str, Any]:
    """构造设置项响应字典."""
    return {
        "id": setting.pk,  # type: ignore[no-any-return]
        "key": setting.key,
        "value": setting.value,
        "value_type": setting.value_type,
        "description": setting.description,
        "updated_at": setting.updated_at.isoformat(),  # type: ignore[missing-attribute]
    }


@router.get("/settings", response={200: SystemSettingListOut})
def list_settings_view(request: HttpRequest) -> HttpResponse:
    """列出所有系统设置（仅管理员）."""
    require_admin(request)
    qs = SystemSetting.objects.all().order_by("key")
    items = [SystemSettingOut(**_setting_dict(s)).model_dump() for s in qs]
    total = qs.count()
    body = SystemSettingListOut(items=items, total=total).model_dump()
    return JsonResponse(body)


# 注意：具体路径（/presets、/init）必须在参数化路径（/{setting_key}）之前注册，
# 否则 Django/Ninja 会优先匹配参数化路由导致 405。


@router.get("/settings/presets", response={200: list[SystemSettingOut]})
def list_presets_view(request: HttpRequest) -> HttpResponse:
    """列出预置设置项定义（仅管理员，供前端参考类型与默认值）."""
    require_admin(request)
    items: list[SystemSettingOut] = []
    for key, (value, value_type, description) in PRESET_SETTINGS.items():
        items.append(
            SystemSettingOut(
                id=0,
                key=key,
                value=value,
                value_type=value_type,
                description=description,
                updated_at="",
            )
        )
    body = [item.model_dump() for item in items]
    return JsonResponse(body, safe=False)


@router.post("/settings/init", response={200: MessageOut})
def init_settings_view(request: HttpRequest) -> HttpResponse:
    """初始化预置设置项（仅管理员，幂等：已存在的不覆盖）."""
    require_admin(request)
    created = 0
    for key, (value, value_type, description) in PRESET_SETTINGS.items():
        _, is_new = SystemSetting.objects.update_or_create(
            key=key,
            defaults={
                "value": value,
                "value_type": value_type,
                "description": description,
            },
        )
        if is_new:
            created += 1
    body = MessageOut(detail=f"初始化完成，新增 {created} 项预置设置").model_dump()
    return JsonResponse(body)


@router.patch("/settings/{setting_key}", response={200: SystemSettingOut})
def update_setting_view(
    request: HttpRequest,
    setting_key: str,
    payload: SystemSettingUpdateIn,
) -> HttpResponse:
    """更新单个系统设置（仅管理员）."""
    require_admin(request)
    try:
        setting = SystemSetting.objects.get(key=setting_key)
    except SystemSetting.DoesNotExist:  # type: ignore[missing-attribute]
        raise HttpError(404, f"设置项 {setting_key} 不存在") from None
    data = payload.model_dump(exclude_unset=True)
    if "value" in data:
        setting.value = data["value"]  # type: ignore[bad-assignment]
    if "description" in data and data["description"] is not None:
        setting.description = data["description"]  # type: ignore[bad-assignment]
    setting.save()
    body = SystemSettingOut(**_setting_dict(setting)).model_dump()
    return JsonResponse(body)


@router.post("/rotate-key", response={200: RotateKeyOut})
def rotate_encryption_key_view(request: HttpRequest, payload: RotateKeyIn) -> HttpResponse:
    """轮换数据源加密密钥（仅管理员，需 confirm=true 二次确认）.

    流程：
    1. 用当前密钥解密所有数据源密码
    2. 用新密钥重新加密
    3. 原子事务批量更新

    新密钥可显式传入，或自动生成随机密钥。
    """
    require_admin(request)
    if not payload.confirm:
        raise HttpError(400, "须确认操作（confirm=true）")

    from django.conf import settings

    from apps.datasources.crypto import decrypt_password, encrypt_password

    old_key = settings.SECRET_KEY
    # 新密钥：显式传入 或 自动生成
    new_key = payload.new_key.strip()
    if not new_key:
        import secrets

        new_key = secrets.token_hex(32)

    # 读取所有非 SQLite 且有密码的数据源
    dss = list(DataSource.objects.exclude(password_encrypted="").exclude(engine="sqlite"))
    if not dss:
        body = RotateKeyOut(success=True, message="无需要轮换的数据源", rotated_count=0).model_dump()
        return JsonResponse(body)

    rotated = 0
    errors: list[str] = []
    try:
        with transaction.atomic():
            for ds in dss:
                try:
                    plaintext = decrypt_password(ds.password_encrypted, old_key)
                    new_cipher = encrypt_password(plaintext, new_key)
                    DataSource.objects.filter(pk=ds.pk).update(password_encrypted=new_cipher)
                    rotated += 1
                except Exception as exc:
                    errors.append(f"{ds.name}: {exc}")
                    raise  # 任一失败回滚全部
    except Exception:
        body = RotateKeyOut(
            success=False,
            message=f"轮换失败，已回滚。错误：{'; '.join(errors)}",
            rotated_count=rotated,
        ).model_dump()
        return JsonResponse(body, status=400)

    # 轮换成功后，将新密钥持久化到 SystemSetting（用于标记）
    SystemSetting.objects.update_or_create(
        key="encryption.current_key",
        defaults={
            "value": new_key,
            "value_type": "str",
            "description": "当前数据源加密密钥（轮换后自动更新）",
        },
    )
    body = RotateKeyOut(
        success=True,
        message=f"密钥轮换成功，共 {rotated} 个数据源",
        rotated_count=rotated,
    ).model_dump()
    return JsonResponse(body)


__all__ = ["router"]
