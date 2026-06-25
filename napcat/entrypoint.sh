#!/bin/sh
# NapCat 自定义入口脚本
# 根据 ACCOUNT 环境变量生成 OneBot v11 反向 WebSocket 配置

CONFIG_FILE="/app/napcat/config/onebot11_${ACCOUNT}.json"
WEBUI_FILE="/app/napcat/config/webui.json"

# 生成 WebUI 配置（固定 Token，避免每次从日志查找）
if [ ! -f "$WEBUI_FILE" ]; then
  cat > "$WEBUI_FILE" << WEBCFGEOF
{
  "host": "::",
  "port": 6099,
  "token": "${NAPCAT_WEBUI_TOKEN:-memepilot}",
  "loginRate": 10,
  "autoLoginAccount": "",
  "disableWebUI": false,
  "accessControlMode": "none",
  "ipWhitelist": [],
  "ipBlacklist": [],
  "enableXForwardedFor": false,
  "enable2FA": false,
  "totpSecret": ""
}
WEBCFGEOF
  echo "[memepilot] 已生成 WebUI 配置: $WEBUI_FILE (token: ${NAPCAT_WEBUI_TOKEN:-memepilot})"
fi

# 生成 OneBot v11 反向 WebSocket 配置
if [ ! -f "$CONFIG_FILE" ]; then
  cat > "$CONFIG_FILE" << 'CFGEOF'
{
  "network": {
    "websocketServers": [],
    "websocketClients": [
      {
        "name": "memepilot-reverse-ws",
        "enable": true,
        "url": "ws://bot:8080/onebot/v11/ws",
        "token": "",
        "reconnectInterval": 5000,
        "heartInterval": 30000,
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "debug": false
      }
    ],
    "httpClients": [],
    "httpSseServers": [],
    "plugins": []
  },
  "musicSignUrl": "",
  "enableLocalFile2Url": true,
  "parseMultMsg": true
}
CFGEOF
  echo "[memepilot] 已生成 NapCat 配置: $CONFIG_FILE"
fi

# 执行 NapCat 原始入口点（位于 /app/entrypoint.sh）
exec bash /app/entrypoint.sh "$@"
