<div align="center">
  <a href="https://v2.nonebot.dev/store"><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/nbp_logo.png" width="180" height="180" alt="NoneBotPluginLogo"></a>
  <br>
  <p><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/NoneBotPlugin.svg" width="240" alt="NoneBotPluginText"></p>
</div>

<div align="center">

# nonebot-plugin-essence-message

_✨ 用于整理精华消息 ✨_


<a href="./LICENSE">
    <img src="https://img.shields.io/github/license/BEISNWKZNAN/nonebot-plugin-essence-message.svg" alt="license">
</a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-essence-message">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-essence-message.svg" alt="pypi">
</a>
<img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="python">

</div>


## 📖 介绍

如果你群有精华消息过多的困扰, 可以考虑使用此插件.  
请注意，此插件可能进一步推进你群精华消息通货膨胀。

## ⚠️ 注意事项

**📊 数据库结构变更提示**
1. 在从较旧的版本更新到 0.7.0 时，涉及以下数据库结构调整：
   - user_mapping 新增 `UNIQUE` 唯一性约束（nickname + group_id + user_id 组合）
   - 消息改用带版本号的 JSON 格式存储
   - 图片从数据库 Base64 数据迁移至数据库同目录下的 `img/`，数据库仅保存相对路径
2. 每次重建前会通过 SQLite 在线备份 API 自动备份数据库，备份文件位于数据库同目录的
   `backups/`；媒体目录仍需单独备份。旧版本已经截断的数据无法通过迁移恢复
3. 数据库文件在nonebot_plugin_localstore给出的插件数据目录的中的essence_message子目录下, 文件名为essence_message.db
4. 根据[Nonebot文档](https://nonebot.dev/docs/best-practice/data-storing)以下是默认插件数据目录
    - macOS: `~/Library/Application` Support/nonebot2
    - Unix: `~/.local/share/nonebot2` or in `$XDG_DATA_HOME`, if defined
    - Win XP (not roaming): `C:\Documents and Settings\<username>\Application Data\nonebot2`
    - Win 7 (not roaming): `C:\Users\<username>\AppData\Local\nonebot2`

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot_plugin_essence_message

</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details>
<summary>pip</summary>

    pip install nonebot_plugin_essence_message
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_essence_message"]

</details>

## ⚙️ 配置

在 nonebot2 项目的`.env`文件中添加下表中的必填配置

|           配置项            | 必填  | 默认值 |                                 说明                                  |
| :-------------------------: | :---: | :----: | :-------------------------------------------------------------------: |
|    essence_random_limit     |  否   |   5    |                  `essence random` 指令的使用次数上限                  |
|   essence_random_cooldown   |  否   |   5    |                `essence random` 指令的使用次数冷却时间                |
|    essence_enable_groups    |  否   | `["all"]` |       启用群列表或二维共享池配置，详见下方说明                         |
| good_essence_enable_groups  |  否   |   []   | 启用 n 赞加精；共享池内任一群启用时，整个池都启用                     |
|         good_bound          |  否   |   3    |                                 如上                                  |
| whale_essnece_enable_groups |  否   |   []   | 使用 Reaction 🐳代替设精；共享池内任一群启用时，整个池都启用          |

`essence_enable_groups` 支持以下形式：

- `["all"]`：启用所有群，每个群使用独立精华池。
- `[1, 2]`：仅启用群 1、2，并分别使用独立精华池。
- `[[1], [2, 3]]`：群 1 使用独立池，群 2、3 共享一个精华池。
- `[1, [2, 3]]`：支持混合配置；群 1 使用独立池，群 2、3 共享一个精华池。
- `[["all"]]`：启用所有群，并让所有群共享同一个全局精华池。

共享池会影响 `random`、`search`、`rank` 和 `export`；消息仍按实际来源群号
写入数据库，`fetchall` 与 `clean` 也只操作当前群。

如果要寻找数据库和缓存的位置,请参考nonebot文档的[data-storing](https://nonebot.dev/docs/best-practice/data-storing)章节
## 🎉 使用
### 指令表
| 指令                    | 权限   | 需要@ | 范围 | 说明                                                   |
| ----------------------- | ------ | ----- | ---- | ------------------------------------------------------ |
| `essence help`          | 群员   | 否    | 群聊 | 显示此帮助信息                                         |
| `essence random`        | 群员   | 否    | 群聊 | 从当前随机发送一条精华消息                             |
| `essence search <str>`  | 群员   | 否    | 群聊 | 搜索全部文本，随机恢复至多 5 条                        |
| `essence rank sender`   | 群员   | 否    | 群聊 | 显示发送者排行榜                                       |
| `essence rank operator` | 群员   | 否    | 群聊 | 显示设精数量排行榜                                     |
| `essence fetchall`      | 管理员 | 否    | 群聊 | 同步群内全部精华消息及媒体文件到本地                   |
| `essence export`        | 管理员 | 否    | 群聊 | 打包导出数据库、CSV 和媒体文件                         |
| `essence switch`        | 管理员 | 否    | 群聊 | 切换手动清理模式，暂停或恢复删精事件的数据库联动       |
| `essence clean`         | 管理员 | 否    | 群聊 | 备份后删除群内全部精华消息，数据库记录保留             |

`essence fetchall` 已包含媒体文件持久化，因此不再单独提供 `essence saveall`。

`essence export` 生成 ZIP 包，其中包含 `essence.db`、带 UTF-8 BOM 的
`messages.csv`，以及当前群消息引用的本地图片、语音和视频。媒体文件在压缩包中
保留原相对目录结构。

### 精华事件
- 本插件在正常工作时, 会对精华消息做出响应, 随之把消息存入或删除数据库.  
- 当精华消息空间满了之后，可以使用 `essence clean` 先同步备份，再清空群精华；数据库记录不会删除。
- `switch` 与 `clean` 共用清理状态。如果自动清理不可用，可先执行一次 `essence switch` 进入手动清理模式，再手动删除群精华；完成后再次执行，恢复数据库同步删除。手动清理模式下再次执行 `clean` 会提示清理正在运行。

### Reaction事件
- 本插件在正常工作时,会对🐳(code:128051)和👍(code:74)做出响应.  
- 如果启用了n赞加精功能,此功能会对点赞数超过good_bound的消息自动加精,使得每个群友都有设精权  
- 如果启用了 whale-essnece 功能,此功能会对管理员(包括SUPERUSER,群主和群管理员)的🐳(code:128051)Reaction事件做出反应,把该条消息放入数据库,并回复一个✨(code:10024)表示操作完成
- 如果两个功能同时启用,n赞加精会用🐳代替设精  
*此功能的正常运行需要reaction作为一个onebot的event事件传入nonebot, 通过最近几个版本的热门onebot的实现似乎都不能很好的运行此功能. 因此暂时不建议开启

### 效果图
![alt text](out.png)
