from asyncio import gather

from nonebot import on_type, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    NoticeEvent,
    GroupMessageEvent,
    MessageSegment,
    MessageEvent,
)
from nonebot.adapters.onebot.v11 import GROUP, GROUP_ADMIN, GROUP_OWNER
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .Helper import format_msg, reach_limit, get_name, trigger_rule, db
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

essence_set = on_type((NoticeEvent,), priority=10, block=False, rule=trigger_rule)

command_matcher = on_command(
    "essence", priority=5, permission=GROUP, block=False, rule=trigger_rule
)
admin_command_matcher = on_command(
    "essence",
    priority=4,
    permission=GROUP_ADMIN | GROUP_OWNER,
    block=False,
    rule=trigger_rule,
)


@essence_set.handle()
async def _(event: NoticeEvent, bot: Bot):
    if event.notice_type == "essence" and event.model_extra["sub_type"] == "add":
        msg = await bot.get_msg(message_id=event.model_extra["message_id"])
        msg = await format_msg(msg, bot)
        if msg == None:
            essence_set.finish(MessageSegment.text("呜呜"))
        data = [
            event.time,
            event.model_extra["group_id"],
            event.model_extra["sender_id"],
            event.model_extra["operator_id"],
            msg[0],
            msg[1],
        ]
        db.insert_data(data)
    elif event.notice_type == "essence" and event.model_extra["sub_type"] == "delete":
        msg = await bot.get_msg(message_id=event.model_extra["message_id"])
        msg = await format_msg(msg, bot)
        if msg == None:
            essence_set.finish(MessageSegment.text("呜呜"))
        data = [
            event.time,
            event.model_extra["group_id"],
            event.model_extra["sender_id"],
            event.model_extra["operator_id"],
            msg[0],
            msg[1],
        ]
        db.insert_del_data(data)
        pass


@command_matcher.handle()
async def command_main(
    event: MessageEvent,
    bot: Bot,
    args: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent):
        cmdarg = args.extract_plain_text()
        args = cmdarg.split()

        if len(args) == 0:
            await command_matcher.finish(
                "使用说明:\n"
                + "essence help - 显示此帮助信息\n"
                + "essence random - 随机发送一条精华消息\n"
                + "essence rank sender - 显示发送者精华消息排行榜\n"
                + "essence rank operator - 显示管理员设精数量精华消息排行榜\n"
                + "essence cancel - 在数据库中删除最近取消的一条精华消息\n"
                + "essence fetchall - 获取群内所有精华消息\n"
                + "essence export - 导出的精华消息\n"
            )
        else:
            if args[0] == "help":
                await command_matcher.finish(
                    "使用说明:\n"
                    + "essence help - 显示此帮助信息\n"
                    + "essence random - 随机发送一条精华消息\n"
                    + "essence rank sender - 显示发送者精华消息排行榜\n"
                    + "essence rank operator - 显示管理员设精数量精华消息排行榜\n"
                    + "essence cancel - 在数据库中删除最近取消的一条精华消息\n"
                    + "essence fetchall - 获取群内所有精华消息\n"
                    + "essence export - 导出的精华消息\n"
                )
            elif args[0] == "random":
                if reach_limit(event.get_session_id()):
                    await command_matcher.finish("过量抽精华有害身心健康")
                msg = db.random_essence(event.group_id)
                if msg[4] == "text":
                    await command_matcher.finish(
                        MessageSegment.text(
                            f"{await get_name(bot, event.group_id,msg[2])}:{msg[5]}"
                        )
                    )
                elif msg[4] == "image":
                    await command_matcher.finish(MessageSegment.image(file=msg[5]))
            elif args[0] == "search":
                if len(args) < 2:
                    await command_matcher.finish("请输入关键字")
                else:
                    msg = db.search_entries(event.group_id, args[1])
                    if len(msg) == 0:
                        await command_matcher.finish("没有找到")
                    result = []
                    for _, _, sender_id, _, _, data in msg:
                        result.append(
                            f"{await get_name(bot, event.group_id,sender_id)}: {data}"
                        )
                    await command_matcher.finish(MessageSegment.text("\n".join(result)))
            elif args[0] == "rank":
                if len(args) < 2:
                    await command_matcher.finish("请输入完整命令")
                else:
                    if args[1] == "sender":
                        rank = db.sender_rank(event.group_id)
                        result = []
                        names = await gather(
                            *[get_name(bot, event.group_id, id) for id, _ in rank]
                        )
                        for index, (name, (_, count)) in enumerate(
                            zip(names, rank), start=1
                        ):
                            result.append(f"第{index}名: {name}, {count}条精华消息")
                        await command_matcher.finish(
                            MessageSegment.text("\n".join(result))
                        )
                    elif args[1] == "operator":
                        rank = db.operator_rank(event.group_id)
                        result = []
                        for index, (id, count) in enumerate(rank, start=1):
                            result.append(
                                f"第{index}名: {await get_name(bot, event.group_id, id)}, {count}条精华消息"
                            )
                        await command_matcher.finish(
                            MessageSegment.text("\n".join(result))
                        )


@admin_command_matcher.handle()
async def admin_command(
    event: MessageEvent,
    bot: Bot,
    args: Message = CommandArg(),
):
    if isinstance(event, GroupMessageEvent):
        cmdarg = args.extract_plain_text()
        args = cmdarg.split()
        if len(args) == 0:
            pass
        else:
            if args[0] == "cancel":
                del_data = db.delete_matching_entry(event.group_id)
                if del_data == None:
                    await command_matcher.finish("没有删除任何精华消息")
                else:
                    await command_matcher.finish(
                        f"已删除 {await get_name(bot,event.group_id,del_data[2])} 的一条精华消息"
                    )
            elif args[0] == "fetchall":
                essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
                for essence in essencelist:
                    try:
                        msg = await bot.get_msg(message_id=essence["message_id"])
                        msg = await format_msg(msg, bot)
                        data = [
                            event.time,
                            event.model_extra["group_id"],
                            event.model_extra["sender_id"],
                            event.model_extra["operator_id"],
                            msg[0],
                            msg[1],
                        ]
                    except:
                        continue
                    if not db.check_entry_exists(data):
                        db.insert_data(data)
                    pass
            elif args[0] == "export":
                path = db.export_group_data(event.group_id)
                try:
                    await bot.upload_group_file(
                        group_id=event.group_id,
                        file=f"{path}",
                        name="essence.db",
                    )
                except:
                    pass
