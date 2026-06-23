# bot/auth.py — 授权校验模块

> 共享授权工具模块，无 NoneBot2 依赖。提供统一的授权用户校验接口。

## 导出

| 名称 | 类型 | 说明 |
|------|------|------|
| `AUTHORIZED_USER_IDS` | `frozenset[str]` | 从环境变量读取的授权用户白名单 |
| `is_authorized` | `(str) -> bool` | 校验用户是否在白名单中 |
| `log_unauthorized` | `(str, str) -> None` | 记录非授权用户访问日志 |

## 依赖

| 依赖项 | 来源 | 说明 |
|--------|------|------|
| `AUTHORIZED_USER_IDS` | 环境变量 | 授权用户白名单（逗号分隔 QQ 号） |

## 行为

1. 模块加载时从 `AUTHORIZED_USER_IDS` 环境变量解析白名单为 `frozenset`
2. `is_authorized(user_id)` 判断 user_id 是否在白名单中
3. `log_unauthorized(user_id, command)` 以 DEBUG 级别记录非授权访问
