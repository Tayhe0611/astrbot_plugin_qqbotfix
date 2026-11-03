<h1 align="center">QQ 官方适配器修复</h1>

<p align="center"><strong>让 AstrBot 的 QQ 官方适配器更好地支持按钮交互、头像与消息发送。</strong></p>

<p align="center">
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/releases/latest"><img alt="最新版本" src="https://img.shields.io/github/v/release/tangshusheng/astrbot_plugin_qqbotfix?style=for-the-badge&label=release" /></a>
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/blob/main/LICENSE"><img alt="AGPL-3.0-or-later 许可证" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue?style=for-the-badge" /></a>
  <a href="https://github.com/Soulter/AstrBot"><img alt="AstrBot 插件" src="https://img.shields.io/badge/AstrBot-plugin-00A86B?style=for-the-badge" /></a>
  <a href="https://github.com/tangshusheng/astrbot_plugin_qqbotfix/stargazers"><img alt="GitHub Stars" src="https://img.shields.io/github/stars/tangshusheng/astrbot_plugin_qqbotfix?style=for-the-badge&color=gold" /></a>
</p>

<p align="center"><a href="README.md">项目首页</a> · <a href="README_ZH.md">中文详细文档</a> · <a href="README_EN.md">English</a></p>

适用于 AstrBot `qq_official`（Gateway/WS）适配器的运行时兼容补丁，也支持 `qq_official_webhook` 的交互回调和发送者头像映射。本插件不会修改 AstrBot 核心文件，也不会自行伪造 `AstrMessageEvent`。

> **安全默认值：** `managed_platform_ids` 默认为空。只有在消费者完成迁移后，才将对应 QQ 平台实例 ID 加入列表；留空时不会 patch 任何 QQ client。

## 目录

- [功能一览](#功能一览)
- [安装与快速配置](#安装与快速配置)
- [常用配置](#常用配置)
- [所有权与配置](#所有权与配置)
- [消费者 API](#消费者-api)
- [生命周期约束](#生命周期约束)
- [QQ 群聊被动键盘回复](#qq-群聊被动键盘回复)
- [QQ 发送者头像兼容补丁](#qq-发送者头像兼容补丁)
- [可选的频道私聊主动文本发送](#可选的频道私聊主动文本发送)
- [CS Match 迁移](#cs-match-迁移)
- [本地测试](#本地测试)

## 功能一览

| 能力 | 说明 |
| --- | --- |
| **按钮交互桥接** | 按平台实例 ID 注册精确或前缀路由；每次交互只分发给一条路由，支持自动 / 手动 ACK。 |
| **群聊被动键盘回复** | 回复当前有效的 QQ 群聊事件；不支持主动群推送，也不会在超时后重试。 |
| **发送者头像兼容** | 为受管实例补全频道私聊头像，并将原始头像映射到 `sender.avatar`；查找失败不会阻塞消息处理。 |
| **频道私聊文本发送（可选）** | 默认关闭；仅限指定的 `qq_official` WS 实例，目标不明确时拒绝发送，不回退到 C2C。 |

## 安装与快速配置

1. 在 AstrBot 插件管理中搜索 **QQ 官方适配器修复** 安装，或从[最新 Release](https://github.com/tangshusheng/astrbot_plugin_qqbotfix/releases/latest)下载 ZIP 后导入。
2. 先迁移要使用本桥接器的消费者，再在插件配置中填写对应的 QQ 平台实例 ID。不要将仍由旧消费者管理的实例交给桥接器。
3. 保存并重载插件。频道私聊主动文本发送为可选功能，默认关闭。

## 常用配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关；关闭后还原本插件安装的运行时补丁。 |
| `managed_platform_ids` | `[]` | 本桥接器接管的 QQ 实例 ID；留空时 fail-closed，不 patch client。 |
| `ack_timeout` | `3.0` 秒 | 单次 interaction ACK 请求超时；不会自动重试。 |
| `avatar_patch_enabled` | `true` | 为受管 WS / Webhook 实例启用头像兼容补丁。 |
| `channel_dm_push_enabled` | `false` | 为受管 `qq_official` WS 实例启用频道私聊文本发送。 |
| `debug_log_payload` | `false` | 输出完整按钮回调 payload；其中可能包含用户、群、频道标识及按钮数据。 |

Copyright (C) 2026 tangshusheng。本项目基于 [GNU Affero 通用公共许可证第 3 版或更高版本](LICENSE)授权。

## 所有权与配置

桥接器采用 **fail-closed** 策略：`_conf_schema.json` 中的 `managed_platform_ids` 默认为空列表，因此在操作人员明确将平台实例 ID 分配给此桥接器之前，不会 patch 任何 QQ 客户端。请使用 AstrBot 平台配置中显示的实例 ID。只有这些 ID 才会挂接回调；对于 WS，也只有这些 ID 会启用 interaction intent。

消费者还必须将各自的路由限定为其负责处理的确切平台 ID。只有桥接器的受管 ID 列表和消费者路由都包含适配器的 `meta().id` 时，才会分发 interaction。这样，一个 QQ 实例可以继续由尚未迁移的插件管理，而另一个实例则完成迁移。

如果旧消费者仍在 patch 同一客户端的回调或 intents，请勿将对应 ID 添加到 `managed_platform_ids`。桥接器会拒绝客户端实例字典中由其他插件安装的回调，但之后加载的旧插件仍可能替换该回调。请先迁移该消费者，再分配此 ID。

## 消费者 API

在消费者的 `initialize()` 中注册，并保留 token 以供 `terminate()` 使用：

```python
from data.plugins.astrbot_plugin_qqbotfix.main import (
    register_interaction_handler,
)

async def refresh_handler(ctx):
    # 规范化字段包括：
    # ctx.platform_id, ctx.scene, ctx.button_id, ctx.button_data,
    # ctx.message_id, ctx.channel_id, ctx.guild_id, ctx.user_id,
    # ctx.interaction（原始 BotPy 对象）, ctx.client 和 ctx.adapter。
    await ctx.ack_once(0)  # 仅在使用 ack_mode="manual" 时调用
    ...

registration = register_interaction_handler(
    self.context,
    refresh_handler,
    platform_ids={"your-platform-instance-id"},
    button_data_prefix="example:",
    ack_mode="manual",
    fallback_ack_code=1,
)
```

支持的匹配器为一个精确 `button_data` 或一个 `button_data_prefix`。精确匹配优先；多个前缀匹配时，最长前缀优先。同一匹配器和平台 ID 的重复重叠注册会被拒绝。一次 interaction 只会分发到一条路由。

返回的 token 的 `unregister()` 只会移除该消费者的路由。路由表保存在 AstrBot 共享的 `Context` 上，因此重新加载此桥接模块后订阅仍会保留；消费者仍应在自身正常生命周期中注销并重新创建自己的 token。

### ACK 模式

- `ack_mode="auto"`（默认）：桥接器会在调用处理器前尝试发送 code `0`。处理器不能指望再选择其他 code。
- `ack_mode="manual"`：处理器在自己选择的时机调用 `await ctx.ack_once(code)`。桥接器接受的 code 为 `0` 至 `5`。如果处理器抛出异常、未发送 ACK 就返回，或错过 `ack_deadline`，桥接器会尝试发送 `fallback_ack_code`（默认 `1`）。
- 每个已分发的上下文最多进行一次 API 尝试，即使 HTTP 请求失败或超时也如此。重复调用 `ack_once()` 会返回 `False`；不会为该上下文发送第二次 ACK。消费者负责业务级重复检测，并可使用自己选择的 code 对重复投递进行 ACK。
- `handler_timeout` 限制消费者工作时长。如果手动 ACK 处理器在尝试 ACK 前超过 ACK deadline，桥接器会发送回退 ACK 并取消处理器。如果处理器已经发送 ACK，超时只会取消/记录处理器；不会再发送第二次 ACK。

`is_interaction_bridge_active(context, platform_id)` 会报告该确切实例当前是否使用桥接器回调。消费者可据此决定发送 type-1 按钮，还是回退到 type-2 按钮。

## 生命周期约束

- WS intent 会在平台加载钩子返回之前进行 OR 设置；如果桥接器检测到 BotPy 连接已经启动，会发出警告。如果添加 intent 太晚，请重启该平台实例并验证实际按钮点击；仅更改内存中的整数不会更新已经建立的 Gateway 订阅。
- 桥接器会串接基线回调，而不是将每个 callable 都视为外部所有者。通过客户端类解析的回调（例如 AstrBot 自己的 BotPy 客户端子类提供的绑定方法）属于基础设施：桥接器会包装该回调，并将没有被已注册路由认领的每个 interaction 转发给它，使核心按钮回调可以与 QQ 菜单插件并行工作。认领和转发互斥；已路由的 interaction 不会再传递给基线回调，因此一次点击只会 ACK 一次、处理一次。
- 只有其他插件安装到客户端*实例字典中*的回调仍会触发所有权冲突。插件通常安装普通函数或闭包，因此如果在实例字典中发现非方法函数，该函数会原样保留，并跳过该客户端。如果发现实例字典中残留的 qqbotfix 桥接回调（热重载时跳过了 `terminate()`），则会替换该回调而非与之串接，并且卸载时不会将其恢复。
- 在 BotPy 客户端的整个生命周期中都会保留 intent 位。消费者注销时不得移除其他路由仍依赖的 intent。
- BotPy 回调和 AstrBot 适配器客户端仍是内部集成点；请针对实际部署的 AstrBot/BotPy 版本进行测试。

## QQ 群聊被动键盘回复

`sendgroupkeyboard()` 是一个底层、单次尝试的辅助函数，用于回复正在处理的 QQ 群聊事件。它要求提供准确的 AstrBot `platform_id`、原始 BotPy `group_event`、字符串 `content`，以及恰好一个与该事件匹配的 `msg_id` 或 `event_id`。该辅助函数会从群聊事件中推导 `group_openid`，并拒绝 C2C 事件、不匹配的平台 ID 和通用 UMO/session ID。使用 `msg_id` 时必须提供 `msg_seq`；从 `1` 开始，如果还要回复同一条源消息，则递增该值。

```python
from data.plugins.astrbot_plugin_qqbotfix.main import sendgroupkeyboard

# 这些值必须来自当前有效的 QQ 群聊事件和现有 BotPy 上传流程。
# 按 QQ 当前键盘 schema 构造 keyboard_payload。
await sendgroupkeyboard(
    self.context,
    platform_id=qq_platform_instance_id,
    group_event=raw_group_event,
    msg_id=raw_group_event.id,
    msg_seq=1,
    content="",  # 卡片的实际文本应放在 markdown_payload["content"] 中。
    msg_type=2,
    markdown=markdown_payload,
    keyboard=keyboard_payload,
)
```

对于 Markdown（`msg_type=2`），`content` 可以为空。现有调用方仍可提供非空文本，但此辅助函数始终会向 BotPy 发送空的外层 `content`。卡片的实际文本保留在传入的 `markdown` payload 中，并原样传递，包括 `force_verify_image_resource`。媒体消息（`msg_type=7`）仍要求 `content` 非空，并会原样保留该字段。

升级后，请重启 AstrBot，或先重新加载 `qqbotfix`，再重新加载其消费者插件，以便它们导入的辅助函数使用新版本。

此辅助函数会原样传递提供的键盘 payload；它不会编造按钮 action 值，也不会注册回调处理器。在 QQ 的按钮 schema 中，URL 跳转按钮使用 `action.type=0`，目标 URL 放在 `action.data` 中；`action.type=1` 是回调，此处不需要。首次实际验证应发送一条 Markdown 消息并搭配 URL 键盘（`msg_type=2`），不带媒体。然后，在另一个测试事件中，尝试将已上传的 `media` 与相同键盘一起发送（`msg_type=7`）：

```python
await sendgroupkeyboard(
    self.context,
    platform_id=qq_platform_instance_id,
    group_event=raw_group_event,
    msg_id=raw_group_event.id,
    msg_seq=1,
    content=" ",  # QQ 的媒体消息仍要求 content 字段。
    msg_type=7,
    media=uploaded_media,
    keyboard=keyboard_payload,
)
```

`media` 值必须是 BotPy 上传后得到的媒体/文件信息对象，而不能是本地路径或公开图片 URL。SDK 同时暴露 `media` 和 `keyboard` 参数，并不保证 QQ 接受它们一起使用；只有真实机器人/群聊测试才能确认。如果 QQ 明确拒绝组合 payload，可对同一条消息进行两次被动回复：先发送 `msg_seq=1` 的纯媒体消息，再发送 `msg_seq=2` 的 Markdown 和键盘消息。该媒体回退步骤支持 `keyboard=None`。超时后不要自动回退或重试：原始请求可能已经被接受，重试可能造成重复回复。

截至 2025-04-21，QQ 当前发送文档说明不再提供主动群推送。仅在处理可回复的群聊事件时使用此辅助函数；不要用于脱离事件的后台任务或延迟任务：被动回复必须在文档规定的五分钟窗口内送达，并且每条源消息的回复次数有限。QQ 在服务端执行时效性和回复数量限制；该辅助函数要求提供回复标识符，但无法证明该标识符仍然有效。URL 键盘/自定义按钮功能也可能需要相应的 QQ 机器人权限/模板配置。

## QQ 发送者头像兼容补丁

`avatar_patch_enabled` 默认为 `true`。只有 `enabled` 为 true，且确切的 QQ 实例 ID 出现在 `managed_platform_ids` 中时，才会应用补丁。`qq_official`（WS）和 `qq_official_webhook` 均受支持。ID 列表为空时仍采用 fail-closed 策略；不会 patch 任何适配器。

```json
{
  "enabled": true,
  "managed_platform_ids": ["your-qq-platform-instance-id"],
  "avatar_patch_enabled": true
}
```

独立的 `core/avatar_patch.py` 模块处理两个阶段：

1. **频道私聊补全，新增于 `0.4.1-beta.4`**：在 AstrBot 解析消息之前，包装 BotPy 客户端的 `on_direct_message_create()` 回调。如果 `message.author.avatar` 缺失/为空，则先复用 `raw_data.author.avatar` 中已有的头像。否则，调用 `get_guild_member(guild_id=message.src_guild_id, user_id=message.author.id)` 查询，并读取返回成员的 `user.avatar`。将成功查到的值写入 `message.author.avatar`，并在可用时写入 `raw_data.author.avatar`，然后调用原始回调。如果 BotPy 未提供相应对象字段，也可以从 `raw_data` 读取源频道和用户 ID。
2. **发送者映射，自 `0.4.1-beta.3` 起提供**：包装每个受管适配器的 `commit_event()` 方法。在解析之后、加入队列之前，将非空的 `message.raw_message.author.avatar` 复制到 `message.sender.avatar`。这不会全局替换共享的 QQ 静态解析器，也不会编辑 AstrBot 核心、`MessageMember`、`bridge.py` 或交互回调。

查找/缓存行为：

- 只有频道私聊回调会查询成员；群聊/C2C 事件以及已有头像都不会触发该查询。不会编造头像 URL。私聊的 `guild_id` 是会话/对话 ID，**不是源频道**，绝不会在成员查询中代替 `src_guild_id`。
- 缓存按适配器实例隔离，键为 `(source guild ID, user ID)`。成功查找缓存一小时；失败/空结果缓存一分钟。每个实例最多保留 1,024 条 LRU 缓存项。缓存仅存在于当前进程中，并会在头像补丁禁用或卸载时清空。
- 同一键的并发消息会共用一次查找。单次查找超时时间为三秒；每个实例最多允许 32 次不同查找处于等待中。超出上限的查找会跳过，不会阻塞消息送达。
- 缺少源/用户 ID、API/权限不可用、请求失败、结果为空以及字段不可写时，都会降级为原始消息处理。成员 API 可能不会返回头像；补丁安装成功并不保证每条频道私聊都能获取头像。

生命周期与消费者行为：

- 已有的非空 `sender.avatar` 会被保留，包括未来核心版本或其他插件提供的原生字段。缺少发送者或发送者对象不可写不会阻碍事件送达。
- 可选的频道私聊 session/push 桥接器先安装；头像回调在其外层包装，因此会在原始消息解析器运行前完成补全。重新加载钩子会暂时拆开并重新挂接仅头像回调，同时保留其缓存，以便 push 桥接器协调所有权。重复初始化不会叠加包装器。
- 禁用/卸载时会恢复原始方法，并取消等待中的查找，但不会丢弃正在等待查找的消息。如果后续插件已经包装/替换某个方法，则会保留后续插件的方法，并停用本补丁保留的包装器，而不是覆盖新的所有者。受管 ID 和平台移除操作会在下一次加载钩子时协调。
- `sender.avatar` 仍是运行时兼容属性，不是正式的数据类字段。消费者可以使用 `getattr(event.message_obj.sender, "avatar", None)`，但不会扩展数据类序列化或类型声明。已经排队的消息不会被追溯修改。长期的上游修复应在 `MessageMember` 中添加可选的 `avatar` 字段，并在 QQ 适配器解析器中填充该字段。

升级后，重新加载 `qqbotfix` 或重启 AstrBot。请检查每个已配置实例是否出现 `sender-avatar compatibility patch installed`，然后测试真实频道私聊。如果原始事件没有头像，但有 `src_guild_id` 和 `author.id`，请验证在权限允许的成员查询后，原始 author 和 `sender.avatar` 均被填充。将 `avatar_patch_enabled` 设为 `false` 并重新加载会禁用两个头像阶段，但不会禁用交互桥接或单独启用的频道私聊 push 功能。

## 可选的频道私聊主动文本发送

此功能独立于交互回调桥接，且**默认关闭**。如需启用，请设置 `channel_dm_push_enabled: true`，并将指定的 `qq_official` WS 实例 ID 加入 `managed_platform_ids`。此版本不支持 `qq_official_webhook`。

此 shim 会观察 BotPy `DirectMessage` 事件，并在后续 `send_by_session` 调用中使用消息的成员 ID 和私聊 session ID。缓存仅在当前进程中有效：重启后，必须再次观察到某成员的私聊 session，此路径才可用。对于 `/skdtestpush`，请在启用 shim 后从频道私聊中运行该命令，以便当前 `DirectMessage` 事件填充缓存。如果观察到同一成员对应多个私聊 session ID，则目标被视为不明确，发送会 fail-closed；不会回退到 C2C。未被观察为频道私聊的普通 C2C session 会继续使用 AstrBot 原有发送方法。重启后尚未重新观察到的已保存频道私聊 UMO，无法与普通好友 UMO 区分；在观察到 `DirectMessage` 之前，它会保留原有 C2C 行为。

仅支持纯文本和 Markdown 链。富媒体链会被拒绝，不会通过 C2C 专用上传逻辑发送。该 shim 恰好进行一次 `post_dms` API 尝试（不重试，也不回退到 Markdown），以避免意外消耗频道私聊主动发送配额。补丁检查成功不保证 QQ 会接受发送：Gateway 必须在线，并且仍受 QQ 主动消息限制约束。

消费者可使用 `is_channel_dm_push_active(context, platform_id)` 作为能力门控。它只报告运行时 shim 已安装，不会改变消费者策略。例如，主动过滤频道私聊的提醒插件必须单独选择启用，才能向这些会话发送提醒。

## CS Match 迁移

具体的 `astrbot_plugin_cs_match` 变更请见 [`CS_MIGRATION.md`](CS_MIGRATION.md)：注册前缀路由、使用 `ctx.ack_once()`，并移除其直接 patch 回调/intent 的逻辑。

## 本地测试

```powershell
python -m unittest discover -s tests -v
```
