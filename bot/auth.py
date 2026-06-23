"""授权校验模块。

从环境变量 AUTHORIZED_USER_IDS 读取授权用户白名单，
提供 is_authorized() 供各插件统一校验。
"""

import logging
import os

logger = logging.getLogger(__name__)

# 授权用户白名单（逗号分隔的 QQ 号）
AUTHORIZED_USER_IDS: frozenset[str] = frozenset(
    uid.strip()
    for uid in os.environ.get("AUTHORIZED_USER_IDS", "").split(",")
    if uid.strip()
)


def is_authorized(user_id: str) -> bool:
    """校验用户是否在授权白名单中。

    Args:
        user_id: QQ 用户 ID。

    Returns:
        True 表示授权用户，False 表示非授权用户。
    """
    return user_id in AUTHORIZED_USER_IDS


def log_unauthorized(user_id: str, command: str) -> None:
    """记录非授权用户的访问日志。

    Args:
        user_id: QQ 用户 ID。
        command: 触发的命令名称（如 "help"、"refresh"）。
    """
    logger.debug("非授权用户 %s 的 /%s 请求，静默忽略", user_id, command)
