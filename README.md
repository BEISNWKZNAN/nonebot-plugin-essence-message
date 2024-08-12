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

| 配置项              | 必填 | 默认值 | 说明 |
|:-------------------:|:----:|:------:|:----:|
| essence_random_limit | 否   | 5      | `essence random` 指令的使用次数上限。 |
| essence_enable_groups| 否   | all    | 启用群号列表，默认为 `all` 表示所有群都启用。 |

## 🎉 使用
### 指令表
| 指令 | 权限 | 需要@ | 范围 | 说明 |
|:-----:|:----:|:----:|:----:|:----:|
| essence help | 群员 | 否 | 群聊 | 显示所有可用指令及其说明 |
| essence random | 群员 | 否 | 群聊 | 随机发送一条精华消息 |
| essence rank sender | 群员 | 否 | 群聊 | 显示发送者精华消息排行榜 |
| essence rank operator | 群员 | 否 | 群聊 | 显示管理员设精数量精华消息排行榜 |
| essence cancel | 管理员 | 否 | 群聊 | 在数据库中删除最近取消的一条精华消息 |
| essence fetchall | 管理员 | 否 | 群聊 | 获取群内所有精华消息，并存储到数据库中 |
| essence export | 管理员 | 否 | 群聊 | 导出当前群的精华消息数据库文件 |
### 效果图
![alt text](out.png)
