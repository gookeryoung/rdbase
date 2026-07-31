# iter-15 P5-2 系统设置

## 需求清单

- [x] P5-2 实现 SystemSetting 模型（key-value + value_type）与 CRUD API（admin only）
- [x] P5-2 实现会话超时（JWT access token TTL 可配置化，从 SystemSetting 读取）
- [x] P5-2 实现密码策略（自定义 Django 密码验证器，读取 SystemSetting）
- [x] P5-2 实现数据源加密密钥轮换（原子事务重新加密）
- [x] P5-2 前端系统设置页面（列表/编辑/加密轮换，admin only）
- [x] P5-2 测试：模型/API/密码验证器/加密轮换测试，门禁通过

## 迭代目标

实现系统设置管理功能，使管理员可通过前端页面管理：
1. 会话超时（JWT token 有效期）
2. 密码策略（最小长度、复杂度要求、历史检查）
3. 数据源加密密钥轮换

## 改动文件清单

### 后端新增
- `backend/apps/settings/models.py` — SystemSetting 模型、ValueType 枚举、PRESET_SETTINGS 常量、get_setting/get_setting_int/get_setting_bool 便捷函数
- `backend/apps/settings/schemas.py` — Pydantic Schema（SystemSettingOut/SystemSettingListOut/SystemSettingUpdateIn/RotateKeyIn/RotateKeyOut/MessageOut）
- `backend/apps/settings/api.py` — Ninja Router（列表/更新/预置/初始化/密钥轮换）
- `backend/apps/settings/validators.py` — ConfigurablePasswordValidator + PasswordHistoryValidator
- `backend/apps/settings/migrations/0001_initial.py` — SystemSetting 迁移

### 后端修改
- `backend/rdbase/settings/base.py` — INSTALLED_APPS 增加 apps.settings；AUTH_PASSWORD_VALIDATORS 增加自定义验证器
- `backend/apps/accounts/models.py` — 新增 PasswordHistory 模型
- `backend/apps/accounts/migrations/0002_add_password_history.py` — PasswordHistory 迁移
- `backend/apps/accounts/jwt.py` — JWT 生命周期从 SystemSetting 动态读取
- `backend/api/v1/__init__.py` — 挂载 settings_router

### 前端新增
- `frontend/src/api/settings.ts` — 设置 API 客户端
- `frontend/src/pages/Settings.tsx` — 系统设置页面（Ant Design）

### 前端修改
- `frontend/src/types/index.ts` — 新增 SystemSetting/SystemSettingList/SystemSettingUpdate/RotateKeyRequest/RotateKeyResponse/ValueType 类型
- `frontend/src/routes/index.tsx` — 新增 /settings 路由（admin only）
- `frontend/src/layouts/MainLayout.tsx` — 侧边栏新增"系统设置"菜单项（admin only）

### 测试新增
- `tests/test_settings_models.py` — 26 项测试
- `tests/test_settings_validators.py` — 13 项测试
- `tests/test_settings_api.py` — 15 项测试
- `tests/conftest.py` — 新增 admin_user/regular_user/designer_user/auth_client/mysql_ds fixtures

## 关键决策与依据

### 1. SystemSetting 模型设计
采用 key-value + value_type 结构，value 始终以字符串存储，读取时按 value_type 反序列化为 str/int/bool/json。
优势：新增设置项无需数据库迁移，支持 4 种值类型，前端可根据类型展示不同编辑控件。

### 2. JWT TTL 可配置化
在 jwt.py 中实现 `_access_token_lifetime()` 和 `_refresh_token_lifetime()` 函数，通过 lazy import 从 SystemSetting 读取配置，
读取失败时回退到默认值（15 分钟 / 7 天）。最小值限制为 1 分钟/天防止误配。

### 3. 密码策略验证器
实现 ConfigurablePasswordValidator（复杂度检查）和 PasswordHistoryValidator（历史密码检查），
均通过 lazy import 从 SystemSetting 读取策略。PasswordHistory 使用独立模型存储 SHA-256 哈希。

### 4. 加密密钥轮换
使用 `transaction.atomic()` 原子事务确保全部重加密或全部回滚。流程：当前密钥解密 → 新密钥加密 → 批量更新。
新密钥可自动生成（secrets.token_hex(32)）或显式传入。轮换成功后将新密钥存入 SystemSetting 记录。

### 5. 路由注册顺序
具体路径（/presets、/init）必须在参数化路径（/{setting_key}）之前注册，否则 Django/Ninja 会优先匹配参数化路由导致 405。

### 6. 前端权限控制
Settings 页面通过 RoleRoute 组件限制 admin 角色访问，侧边栏菜单项也根据角色过滤。

## 测试验证结果

全量测试：684 passed, 0 failed

新增测试：
- test_settings_models.py: 26 passed（CRUD、类型反序列化、便捷函数、预置项）
- test_settings_validators.py: 13 passed（密码复杂度、历史检查）
- test_settings_api.py: 15 passed（列表/更新/权限/密钥轮换/预置/初始化）

## 遗留事项

- 前端 Settings 页面尚未与后端 API 联调验证（需启动开发服务器）
- PasswordHistory 的 validate_password 方法需在用户修改密码流程中被调用（目前验证器的 validate 仅检查，不记录）
- 加密密钥轮换后，需同步更新 datasources.crypto 中的默认密钥引用

## 下一轮计划

- P5-3 数据权限与行级安全
- 或继续完善系统设置的前端联调与用户手册
