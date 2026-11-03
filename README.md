<h1 align="center">QQ 官方适配器修复</h1>

<p align="center">
  <strong>让 AstrBot 的 QQ 官方适配器更好地支持按钮交互、头像与消息发送。</strong>
</p>

<p align="center">
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/tangshusheng/astrbot_plugin_qqbotfix?style=for-the-badge&label=release" /></a>
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/blob/main/LICENSE"><img alt="License: AGPL-3.0-or-later" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?style=for-the-badge" /></a>
  <a href="https://github.com/Soulter/AstrBot"><img alt="AstrBot plugin" src="https://img.shields.io/badge/AstrBot-plugin-00A86B?style=for-the-badge" /></a>
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/tangshusheng/astrbot_plugin_qqbotfix?style=for-the-badge&color=gold" /></a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> · <a href="README_EN.md">English</a> · <a href="README_ZH.md">中文详细文档</a>
</p>

适用于 AstrBot `qq_official`（Gateway/WS）适配器的运行时兼容补丁，也支持 `qq_official_webhook` 的交互回调和发送者头像映射。本插件不修改 AstrBot 核心文件，也不会自行伪造 `AstrMessageEvent`。

> **安全默认值：** `managed_platform_ids` 默认为空。只有将已迁移消费者对应的 QQ 平台实例 ID 明确加入列表后，插件才会接管该实例；留空时不会 patch 任何 QQ client。

## 目录

- [功能一览](#功能一览)
- [安装与快速配置](#安装与快速配置)
- [常用配置](#常用配置)
- [详细文档](#详细文档)
- [作者与许可证](#作者与许可证)

## 功能一览

| 能力 | 说明 |
| --- | --- |
| **按钮交互桥接** | 消费者按平台实例 ID 注册精确或前缀路由；每次交互只分发给一条路由，并提供自动 / 手动 ACK。 |
| **群聊被动键盘回复** | 使用 `sendgroupkeyboard()` 回复当前有效的 QQ 群聊事件；不做主动群推送，也不会在超时后重试。 |
| **发送者头像兼容** | 为受管实例补全频道私聊头像，并将原始头像映射到 `sender.avatar`；查询失败不阻塞原消息处理。 |
| **频道私聊文本发送（可选）** | 默认关闭；仅限指定的 `qq_official` WS 实例，发送目标不明确时拒绝发送，不回退到 C2C。 |

## 安装与快速配置

1. 在 AstrBot 插件管理中搜索 **QQ 官方适配器修复** 安装；或从[最新 Release](https://github.com/tangshusheng/astrbot_plugin_qqbotfix/releases/latest)下载 ZIP 后导入。
2. 在插件配置中找到 QQ 平台实例 ID。先迁移使用本桥接器的消费者，再只填写这些实例 ID；未迁移的实例保持不变。
3. 保存并重载插件。可选频道私聊文本发送需另外开启；它默认关闭。

## 常用配置

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭时还原本插件安装的运行时补丁。 |
| `managed_platform_ids` | `[]` | 由本桥接器接管的 QQ 实例 ID。留空时 fail-closed，不 patch client。 |
| `ack_timeout` | `3.0` 秒 | 单次 interaction ACK 请求超时；不会自动重试。 |
| `avatar_patch_enabled` | `true` | 为受管 WS / Webhook 实例启用头像兼容补丁。 |
| `channel_dm_push_enabled` | `false` | 为受管 `qq_official` WS 实例启用频道私聊文本发送。 |
| `debug_log_payload` | `false` | 输出完整按钮回调 payload；其中可能含用户、群、频道标识及按钮数据。 |

**迁移提示：** 不要让旧消费者和本桥接器同时 patch 同一 QQ client 的回调或 intent。消费者迁移示例见 [CS Match 迁移说明](CS_MIGRATION.md)。

## 详细文档

- [中文详细文档](README_ZH.md)：接口、ACK、生命周期、群键盘、头像与频道私聊发送行为。
- [English detailed README](README_EN.md)
- [插件配置真源](./_conf_schema.json)
- [CS Match 迁移说明](./CS_MIGRATION.md)

## 作者与许可证

Copyright (C) 2026 tangshusheng。基于 [GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）](LICENSE)授权。
