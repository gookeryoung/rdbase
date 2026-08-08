"""Redis 客户端单例.

从 settings.REDIS_URL 创建 ``redis.Redis`` 实例，模块级缓存复用。

- 无 ``REDIS_URL`` 且 ``REDIS_FAKE=False`` 时返回 None（系统降级为无 Redis 模式）。
- ``REDIS_FAKE=True`` 时使用 fakeredis（仅用于开发/测试）。
- ``ping_redis()`` 供健康检查调用。
"""

from __future__ import annotations

import logging
import threading

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

# 模块级单例与初始化标记；用锁保护 check-then-act。
_redis_client: redis.Redis | None = None
_initialized: bool = False
_redis_lock = threading.Lock()


def _build_client() -> redis.Redis | None:
    """根据配置构造 Redis 客户端实例（不做缓存）.

    Returns:
        构造好的 ``redis.Redis`` 实例；未配置且未启用 fakeredis 时返回 None。
    """
    url = str(getattr(settings, "REDIS_URL", "") or "")
    use_fake = bool(getattr(settings, "REDIS_FAKE", False))
    if use_fake:
        try:
            import fakeredis
        except ImportError:
            logger.warning("REDIS_FAKE=True 但未安装 fakeredis，Redis 客户端不可用")
            return None
        # fakeredis 即使无 URL 也能工作，给一个默认地址便于隔离命名空间
        return fakeredis.FakeRedis.from_url(url or "redis://localhost:6379/0", decode_responses=True)
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True)


def get_redis() -> redis.Redis | None:
    """获取 Redis 客户端单例（线程安全）.

    Returns:
        已初始化的 ``redis.Redis`` 实例；未配置时返回 None。
    """
    global _redis_client, _initialized  # noqa: PLW0603 - 单例缓存需修改模块级状态
    if _initialized:
        return _redis_client
    with _redis_lock:
        if _initialized:
            return _redis_client
        client = _build_client()
        _redis_client = client
        _initialized = True
        if client is None:
            logger.info("Redis 未配置（REDIS_URL 为空且 REDIS_FAKE=False），跳过初始化")
        else:
            logger.info("Redis 客户端已初始化")
        return client


def ping_redis() -> tuple[bool, str]:
    """健康检查：尝试 ping Redis.

    Returns:
        元组 (是否成功, 消息)。未配置 Redis 时返回 (False, "Redis 未配置")。
    """
    client = get_redis()
    if client is None:
        return False, "Redis 未配置"
    try:
        client.ping()
    except redis.RedisError as exc:
        return False, f"Redis 连接失败: {exc}"
    return True, "Redis 连接正常"


def flush_redis() -> None:
    """清理已初始化 Redis 客户端的数据（仅测试用）.

    fakeredis 同 URL 跨实例共享 server 数据，``reset_redis_client`` 仅清单例
    不清 server 数据，故跨测试需显式 ``flushall`` 避免残留污染。
    未初始化时空操作，不主动创建客户端。
    """
    # 不加锁读取：flushall 本身是 Redis 侧原子操作，读 _initialized 仅作快照判断。
    if not _initialized or _redis_client is None:
        return
    try:
        _redis_client.flushall()
    except redis.RedisError:
        logger.debug("flush Redis 数据失败", exc_info=True)


def reset_redis_client() -> None:
    """重置单例（仅测试用）.

    清空缓存的客户端与初始化标记，下次 ``get_redis`` 会重新构造。
    """
    global _redis_client, _initialized  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _redis_lock:
        _redis_client = None
        _initialized = False


def close_redis() -> None:
    """关闭 Redis 连接（应用退出时调用）."""
    global _redis_client, _initialized  # noqa: PLW0603 - 单例缓存需修改模块级状态
    with _redis_lock:
        client = _redis_client
        _redis_client = None
        _initialized = False
    if client is not None:
        try:
            client.close()
        except redis.RedisError:
            logger.exception("关闭 Redis 连接失败")


__all__ = [
    "close_redis",
    "flush_redis",
    "get_redis",
    "ping_redis",
    "reset_redis_client",
]
