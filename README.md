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
<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">

</div>


## 📖 介绍

如果你群有精华消息过多的困扰, 可以考虑使用此插件.  
请注意, 此插件可能进一步推进你群精华消息通货膨胀.

## ⚠️ 注意事项

**📊 数据库结构变更提示**
1. 在从较旧的版本更新到 0.7.0 时, 涉及以下数据库结构调整: 
   - user_mapping 新增 `UNIQUE` 唯一性约束(nickname + group_id + user_id 组合)
   - 消息改用带版本号的 JSON 格式存储
   - 图片从数据库 Base64 数据迁移至数据库同目录下的 `img/`, 数据库仅保存相对路径
2. 每次重建前会通过 SQLite 在线备份 API 自动备份数据库, 备份文件位于数据库同目录的`backups/`；媒体目录仍需单独备份.
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
|    essence_enable_groups    |  否   | `["all"]` |       启用群列表或二维共享池配置, 详见下方说明                         |
| good_essence_enable_groups  |  否   |   []   | 启用 n 赞加精；共享池内任一群启用时, 整个池都启用                     |
|         good_bound          |  否   |   3    |                                 如上                                  |
| whale_essnece_enable_groups |  否   |   []   | 使用 Reaction 🐳代替设精；共享池内任一群启用时, 整个池都启用          |

`essence_enable_groups` 支持以下形式: 

- `["all"]`: 启用所有群, 每个群使用独立精华池.
- `[1, 2]`: 仅启用群 1、2, 并分别使用独立精华池.
- `[[1], [2, 3]]`: 群 1 使用独立池, 群 2、3 共享一个精华池.
- `[1, [2, 3]]`: 支持混合配置；群 1 使用独立池, 群 2、3 共享一个精华池.
- `[["all"]]`: 启用所有群, 并让所有群共享同一个全局精华池.

共享池会影响 `random`、`search`、`rank` 和 `export`；消息仍按实际来源群号
写入数据库, `fetchall` 与 `clean` 也只操作当前群.`random` 和 `search` 返回其他来源群的消息时, 会在发送者名称后自动标注来源群号.

如果要寻找数据库和缓存的位置,请参考nonebot文档的[data-storing](https://nonebot.dev/docs/best-practice/data-storing)章节
## 🎉 使用
### 指令表
| 指令                    | 权限   | 需要@ | 范围 | 说明                                                   |
| ----------------------- | ------ | ----- | ---- | ------------------------------------------------------ |
| `essence help`          | 群员   | 否    | 群聊 | 显示此帮助信息                                         |
| `essence random [options]` | 群员   | 否    | 群聊 | 从筛选结果随机发送精华消息                             |
| `essence search [keyword] [options]` | 群员 | 否 | 群聊 | 搜索或筛选精华消息, 默认返回至多 5 条              |
| `essence rank sender`   | 群员   | 否    | 群聊 | 显示发送者排行榜                                       |
| `essence rank operator` | 群员   | 否    | 群聊 | 显示设精数量排行榜                                     |
| `essence fetchall`      | 管理员 | 否    | 群聊 | 同步群内全部精华消息及媒体文件到本地                   |
| `essence export`        | 管理员 | 否    | 群聊 | 打包导出数据库、CSV 和媒体文件                         |
| `essence switch`        | 管理员 | 否    | 群聊 | 切换手动清理模式, 暂停或恢复删精事件的数据库联动       |
| `essence clean`         | 管理员 | 否    | 群聊 | 备份后删除群内全部精华消息, 数据库记录保留             |

`random` 与 `search` 支持以下筛选选项: 

- `--from <time>`、`--to <time>`: 按设精时间筛选.格式为 `YYYY-MM-DD`
  或 `YYYY-MM-DDTHH:mm[:ss]`.
- `--last <duration>`: 筛选最近一段时间, 例如 `30m`、`12h`、`7d`、`4w`；
  不能和 `--from`、`--to` 同时使用.
- `--sender-id <qq>`(兼容 `--send-id`)、`--operator-id <qq>`: 按发送者
  或设精操作者筛选.
- `--exclude-sender-id <qq>`、`--exclude-operator-id <qq>`: 排除指定用户.
- `--group-id <group>`: 限定共享池内的实际来源群.
- `--type <type>`: 支持 `text`、`image`、`record`、`forward` 和 `mixed`.
- `--count/-n <count>`: 控制返回数量.`random` 默认 1 条、最多 10 条；
  `search` 默认 5 条、最多 20 条.

`search` 额外支持 `--order random|newest|oldest`、完整文本匹配
`--exact` 和安全限时的正则匹配 `--regex`.关键词可以省略, 例如: 

```text
essence random --last 7d --type image -n 3
essence search 测试 --sender-id 123456 --order newest
essence search --group-id 123456789 --from 2026-01-01
essence search "^今天.*天气$" --regex --last 30d
```

`essence fetchall` 已包含媒体文件持久化, 因此不再单独提供 `essence saveall`.
新获取的图片、语音和视频会保存到插件数据目录；数据库消息中只记录本地媒体的相对路径.媒体下载失败时会跳过对应消息段并记录日志, 不会保存临时 URL 或文件 ID.

`essence export` 生成 ZIP 包, 其中包含 `essence.db`、带 UTF-8 BOM 的
`messages.csv`, 以及当前群消息引用的本地图片、语音和视频.媒体文件在压缩包中
保留原相对目录结构.

### 精华事件
- 本插件在正常工作时, 会对精华消息做出响应, 随之把消息存入或删除数据库.  
- 当精华消息空间满了之后, 可以使用 `essence clean` 先同步备份, 再清空群精华；数据库记录不会删除.
- `switch` 与 `clean` 共用清理状态.如果自动清理不可用, 可先执行一次 `essence switch` 进入手动清理模式, 再手动删除群精华；完成后再次执行, 恢复数据库同步删除.手动清理模式下再次执行 `clean` 会提示清理正在运行.

### Reaction事件
- 本插件在正常工作时,会对🐳(code:128051)和👍(code:76)做出响应.
- 如果启用了n赞加精功能,此功能会对点赞数超过good_bound的消息自动加精,使得每个群友都有设精权  
- 如果启用了 whale-essnece 功能,此功能会对管理员(包括SUPERUSER,群主和群管理员)的🐳(code:128051)Reaction事件做出反应,把该条消息放入数据库,并回复一个✨(code:10024)表示操作完成
- 如果两个功能同时启用,n赞加精会用🐳代替设精  

### 效果图
![alt text](out.png)
