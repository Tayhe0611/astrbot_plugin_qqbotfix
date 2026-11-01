# astrbot_plugin_qqbotfix

[简体中文](README.md) | [English](README_EN.md)

AstrBot QQ 官方适配器（`qq_official` Gateway/WS）的运行时兼容补丁，支持按钮交互、发送者头像映射、群聊被动键盘回复，以及可选的频道私聊文本发送。交互回调和发送者头像映射也支持 `qq_official_webhook`。本插件不会修改 AstrBot 核心文件，也不会自行伪造 `AstrMessageEvent`。

> **安全默认值：**`managed_platform_ids` 默认为空列表，采用 fail-closed 策略。只有显式配置实例 ID 后，插件才会为指定 QQ 实例挂接回调或启用 WS interaction intent。迁移消费者之前，不要将实例 ID 添加到此列表。

## 功能概览

- **交互桥接：**消费者可按平台实例 ID 注册精确 `button_data` 或前缀路由；每次交互只分发到一个路由，支持自动/手动 ACK 和回退 ACK。
- **群聊被动键盘回复：**通过 `sendgroupkeyboard()` 回复当前有效群聊事件；不支持主动群推送，不会在超时后自动重试。
- **发送者头像兼容补丁：**默认启用；为受管实例补全频道私聊头像，并将原始消息头像映射到 `sender.avatar`。查找失败时继续原始消息处理。
- **可选频道私聊主动文本发送：**默认关闭；仅支持指定 `qq_official` WS 实例上的文本/Markdown，不回退到 C2C。
- **消费者迁移：**CS Match 迁移说明见 [`CS_MIGRATION.md`](CS_MIGRATION.md)。

## 配置与安装

在 AstrBot 插件配置中设置：

- `enabled`: 是否启用补丁，默认 `true`。
- `managed_platform_ids`: 默认 `[]`。填写已迁移消费者并由此桥接器管理的 QQ 平台实例 ID；每个消费者路由也必须限定其处理的平台 ID。
- `ack_timeout`: 单次 interaction ACK 请求超时（秒），默认 `3.0`；超时不会重试。
- `channel_dm_push_enabled`: 默认 `false`；仅对受管 `qq_official` WS 实例生效。
- `avatar_patch_enabled`: 默认 `true`；仅对受管 QQ 官方 WS/Webhook 实例生效。
- `debug_log_payload`: 默认 `false`；完整 payload 可能包含用户、群、频道标识及按钮数据。

完整行为、生命周期限制、接口用法和示例请见 [简体中文详细文档](README_ZH.md)；原始英文详细文档请见 [English detailed README](README_EN.md)。

本地测试：

```powershell
python -m unittest discover -s tests -v
```
