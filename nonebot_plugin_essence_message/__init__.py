from asyncio import gather

from nonebot import on_type
from nonebot.adapters.onebot.v11 import (
    NoticeEvent,
    MessageSegment,
    GroupMessageEvent,
)
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.plugin import PluginMetadata
from nonebot import require

require("nonebot_plugin_alconna")
from arclet.alconna import Alconna, Args, Subcommand, Option
from nonebot_plugin_alconna import AlconnaMatch, Match, Query, on_alconna

from .Helper import fetchpic, format_msg, reach_limit, get_name, trigger_rule, db
from .config import config

__plugin_meta__ = PluginMetadata(
    name="精华消息管理",
    description="用于整理精华消息",
    usage=("自动存储精华消息备份并提供一些查询功能"),
    type="application",
    homepage="https://github.com/BEISNWKZNAN/nonebot-plugin-essence-message",
    config=config,
    supported_adapters={"~onebot.v11"},
)

essence_set = on_type((NoticeEvent,), priority=10, block=False)

essence_cmd = on_alconna(
    Alconna(
        "essence",
        Subcommand("help"),
        Subcommand("random"),
        Subcommand("search", Args["keyword", str]),
        Subcommand("rank", Args["type", str]),
        Subcommand("cancel"),
        Subcommand("fetchall"),
        Subcommand("export"),
        Subcommand("saveall"),
        Subcommand("clean"),
    ),
    rule=trigger_rule,
    priority=5,
)


@essence_set.handle()
async def _(event: NoticeEvent, bot: Bot):
    if event.notice_type == "essence" and event.sub_type == "add":
        msg = await bot.get_msg(message_id=event.message_id)
        msg = await format_msg(msg, bot)
        if msg == None:
            essence_set.finish(MessageSegment.text("呜呜"))
        data = [
            event.time,
            event.group_id,
            event.sender_id,
            event.operator_id,
            msg[0],
            msg[1],
        ]
        await db.insert_data(data)
    elif event.notice_type == "essence" and event.sub_type == "delete":
        msg = await bot.get_msg(message_id=event.message_id)
        msg = await format_msg(msg, bot)
        if msg == None:
            essence_set.finish(MessageSegment.text("呜呜"))
        data = [
            event.time,
            event.group_id,
            event.sender_id,
            event.operator_id,
            msg[0],
            msg[1],
        ]
        await db.insert_del_data(data)
        pass


@essence_cmd.dispatch("help").handle()
async def help_cmd():
    await essence_cmd.finish(
        "使用说明:\n"
        + "essence help - 显示此帮助信息\n"
        + "essence random - 随机发送一条精华消息\n"
        + "essence rank sender - 显示发送者精华消息排行榜\n"
        + "essence rank operator - 显示管理员设精数量精华消息排行榜\n"
        + "essence cancel - 在数据库中删除最近取消的一条精华消息\n"
        + "essence fetchall - 获取群内所有精华消息\n"
        + "essence export - 导出精华消息\n"
        + "essence sevaall - 将群内所有精华消息图片存至本地\n"
        + "essence clean - 删除群里所有精华消息(数据库中保留)"
    )


@essence_cmd.dispatch("random").handle()
async def random_cmd(event: GroupMessageEvent, bot: Bot):
    if reach_limit(event.get_session_id()):
        await essence_cmd.finish("过量抽精华有害身心健康")
    msg = await db.random_essence(event.group_id)
    if msg == None:
        await essence_cmd.finish(MessageSegment.text("目前数据库里没有精华消息，可以使用essence fetchall抓取群里的精华消息"))
    if msg[4] == "text":
        await essence_cmd.finish(
            MessageSegment.text(
                f"{await get_name(bot, event.group_id,msg[2])}:{msg[5]}"
            )
        )
    elif msg[4] == "image":
        await essence_cmd.finish(MessageSegment.image(file=msg[5]))


@essence_cmd.dispatch("search").handle()
async def search_cmd(
    event: GroupMessageEvent, bot: Bot, keyword: Match[str] = AlconnaMatch("keyword")
):
    msg = await db.search_entries(event.group_id, keyword.result)
    if len(msg) == 0:
        await essence_cmd.finish("没有找到")
    result = []
    for _, _, sender_id, _, _, data in msg:
        result.append(f"{await get_name(bot, event.group_id, sender_id)}: {data}")
    await essence_cmd.finish(MessageSegment.text("\n".join(result)))


@essence_cmd.dispatch("rank").handle()
async def rank_cmd(
    event: GroupMessageEvent, bot: Bot, type: Query[str] = Query("~type")
):
    if type.result == "sender":
        rank = await db.sender_rank(event.group_id)
    elif type.result == "operator":
        rank = await db.operator_rank(event.group_id)
    names = await gather(*[get_name(bot, event.group_id, id) for id, _ in rank])
    result = [
        f"第{index}名: {name}, {count}条精华消息"
        for index, (name, (_, count)) in enumerate(zip(names, rank), start=1)
    ]
    await essence_cmd.finish(MessageSegment.text("\n".join(result)))


@essence_cmd.dispatch(
    "cancel",
    permission=GROUP_ADMIN | GROUP_OWNER,
).handle()
async def cancel_cmd(event: GroupMessageEvent, bot: Bot):
    del_data = await db.delete_matching_entry(event.group_id)
    if del_data is None:
        await essence_cmd.finish("没有删除任何精华消息")
    else:
        await essence_cmd.finish(
            f"已删除 {await get_name(bot, event.group_id, del_data[2])} 的一条精华消息"
        )


@essence_cmd.dispatch(
    "fetchall",
    permission=GROUP_ADMIN | GROUP_OWNER,
).handle()
async def fetchall_cmd(event: GroupMessageEvent, bot: Bot):
    essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
    savecount = 0
    for essence in essencelist:
        try:
            msg = {"message": essence["content"]}
            msg = await format_msg(msg, bot)
            data = [
                essence["operator_time"],
                event.group_id,
                essence["sender_id"],
                essence["operator_id"],
                msg[0],
                msg[1],
            ]
            savecount += 1
        except:
            continue
        if not await db.check_entry_exists(data):
            await db.insert_data(data)
    await essence_cmd.finish(f"成功保存 {savecount}/{len(essencelist)} 条精华消息")
    


@essence_cmd.dispatch(
    "saveall",
    permission=GROUP_ADMIN | GROUP_OWNER,
).handle()
async def sevaall_cmd(event: GroupMessageEvent, bot: Bot):
    essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
    savecount = await fetchpic(essencelist)
    await essence_cmd.finish(f"总共找到 {len(essencelist)} 条精华消息，成功保存 {savecount} 张图片")

@essence_cmd.dispatch(
    "export",
    permission=GROUP_ADMIN | GROUP_OWNER,
).handle()
async def export_cmd(event: GroupMessageEvent, bot: Bot):
    path = await db.export_group_data(event.group_id)
    try:
        await bot.upload_group_file(
            group_id=event.group_id, file=path, name="essence.db"
        )
        await essence_cmd.finish(f"请检查群文件")
    except:
        pass
    
@essence_cmd.dispatch(
    "clean",
    permission=GROUP_ADMIN | GROUP_OWNER,
).handle()
async def clean_cmd(event: GroupMessageEvent, bot: Bot):
    essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
    delcount = 0
    for essence in essencelist:
        try:
            await bot.delete_essence_msg(message_id=essence['message_id'])
            delcount += 1
        except:
            continue
    await essence_cmd.finish(f"成功删除 {delcount}/{len(essencelist)} 条精华消息")