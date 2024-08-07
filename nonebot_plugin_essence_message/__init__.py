import httpx
import asyncio
import base64
import time

from nonebot import on_type, on_command, get_driver
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    NoticeEvent,
    GroupMessageEvent,
    NetworkError,
    ActionFailed,
    MessageSegment,
    MessageEvent,
)
from nonebot.adapters.onebot.v11 import GROUP, GROUP_ADMIN, GROUP_OWNER
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.params import CommandArg
from nonebot.log import logger

from .dateset import DatabaseHandler
from .config import config

essence_set = on_type(
    (NoticeEvent,),
    priority=10,
    block=False,
)
command_matcher = on_command("essence", priority=5, permission=GROUP, block=False)
admin_command_matcher = on_command(
    "essence", priority=4, permission=GROUP_ADMIN | GROUP_OWNER, block=False
)
cfg = config.model_validate(get_driver().config.model_dump())
db = DatabaseHandler(config.db())


async def get_name(bot: Bot, group_id: int, id: int) -> str:
    i = db.get_latest_nickname(group_id, id)
    if i == None:
        sender = await asyncio.wait_for(
            bot.get_group_member_info(group_id=group_id, user_id=id), 3
        )
        name = sender["nickname"] if (sender["card"] == None) else sender["card"]
        db.insert_user_mapping(
            name, sender["group_id"], sender["user_id"], sender["last_sent_time"]
        )
        return name
    else:
        if time.time() - i[1] > 86400:
            try:
                sender = await asyncio.wait_for(
                    bot.get_group_member_info(group_id=group_id, user_id=id), 2
                )
                name = (
                    sender["nickname"] if (sender["card"] == None) else sender["card"]
                )
                db.insert_user_mapping(
                    name,
                    sender["group_id"],
                    sender["user_id"],
                    sender["last_sent_time"],
                )
                return name
            except:
                return i[0]
        else:
            return i[0]


__time_count = {}
__random_count = {}


def reach_limit(session_id: str) -> bool:
    global __random_count, __time_count
    if session_id not in __random_count:
        __random_count[session_id] = 0
        __time_count[session_id] = 0

    __random_count[session_id] += 1
    if int(time.time()) - __time_count[session_id] > 43200:
        __random_count[session_id] = 1
        __time_count[session_id] = int(time.time())

    # 判断是否超出限制
    if __random_count[session_id] > cfg.essence_random_limit:
        return True
    elif __random_count[session_id] == 1:
        __time_count[session_id] = int(time.time())

    return False


@essence_set.handle()
async def _(event: NoticeEvent, bot: Bot):
    if event.notice_type == "essence" and event.model_extra["sub_type"] == "add":
        msg = await bot.get_msg(message_id=event.model_extra["message_id"])
        if msg["message"][0]["type"] == "text":
            data = [
                event.time,
                event.model_extra["group_id"],
                event.model_extra["sender_id"],
                event.model_extra["operator_id"],
                msg["message"][0]["type"],
                msg["message"][0]["data"]["text"],
            ]
        elif msg["message"][0]["type"] == "image":
            async with httpx.AsyncClient() as client:
                r = await client.get(msg["message"][0]["data"]["url"])
            if r.status_code == 200:
                base64str = base64.b64encode(r.content).decode("utf-8")
                data = [
                    event.time,
                    event.model_extra["group_id"],
                    event.model_extra["sender_id"],
                    event.model_extra["operator_id"],
                    msg["message"][0]["type"],
                    f"base64://{base64str}",
                ]
            else:
                essence_set.finish(MessageSegment.text("呜呜"))
        db.insert_data(data)
    elif event.notice_type == "essence" and event.model_extra["sub_type"] == "delete":
        msg = await bot.get_msg(message_id=event.model_extra["message_id"])
        if msg["message"][0]["type"] == "text":
            data = [
                event.time,
                event.model_extra["group_id"],
                event.model_extra["sender_id"],
                event.model_extra["operator_id"],
                msg["message"][0]["type"],
                msg["message"][0]["data"]["text"],
            ]
        elif msg["message"][0]["type"] == "image":
            async with httpx.AsyncClient() as client:
                r = await client.get(msg["message"][0]["data"]["url"])
            if r.status_code == 200:
                base64str = base64.b64encode(r.content).decode("utf-8")
                data = [
                    event.time,
                    event.model_extra["group_id"],
                    event.model_extra["sender_id"],
                    event.model_extra["operator_id"],
                    msg["message"][0]["type"],
                    f"base64://{base64str}",
                ]
            else:
                essence_set.finish(MessageSegment.text("呜呜"))
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
                + "essence rank operator - 显示管理员设精数量精华消息排行榜"
            )
        else:
            if args[0] == "help":
                await command_matcher.finish(
                    "使用说明:\n"
                    + "essence help - 显示此帮助信息\n"
                    + "essence random - 随机发送一条精华消息\n"
                    + "essence rank sender - 显示发送者精华消息排行榜\n"
                    + "essence rank operator - 显示管理员设精数量精华消息排行榜"
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
                        names = await asyncio.gather(
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
                                f"第{index}名: {await get_name(bot, event.group_id,id)}, {count}条精华消息"
                            )
                        await command_matcher.finish(
                            MessageSegment.text("\n".join(result))
                        )


@admin_command_matcher.handle()
async def command_main(
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
                    except:
                        continue
                    if msg["message"][0]["type"] == "text":
                        data = [
                            essence["operator_time"],
                            event.group_id,
                            essence["sender_id"],
                            essence["operator_id"],
                            msg["message"][0]["type"],
                            msg["message"][0]["data"]["text"],
                        ]
                    elif msg["message"][0]["type"] == "image":
                        async with httpx.AsyncClient() as client:
                            r = await client.get(msg["message"][0]["data"]["url"])
                        if r.status_code == 200:
                            base64str = base64.b64encode(r.content).decode("utf-8")
                            data = [
                                essence["operator_time"],
                                event.group_id,
                                essence["sender_id"],
                                essence["operator_id"],
                                msg["message"][0]["type"],
                                f"base64://{base64str}",
                            ]
                        else:
                            essence_set.finish(MessageSegment.text("呜呜"))
                    if not db.check_entry_exists(data):
                        db.insert_data(data)
                    pass
            elif args[0] == "export":
                path = db.export_group_data(event.group_id)
                try:
                    await bot.upload_group_file(
                        group_id=event.group_id,
                        file=f"file://{path}",
                        name="essence.db",
                    )
                except (ActionFailed, NetworkError) as e:
                    logger.error(e)
                    if isinstance(e, ActionFailed):
                        await bot.send_group_msg(
                            group_id=event.group_id,
                            message=Message(MessageSegment.text(str(e))),
                        )
                    elif isinstance(e, NetworkError):
                        await bot.send_group_msg(
                            group_id=event.group_id,
                            message=Message(
                                MessageSegment.text(
                                    "[ERROR]文件上传失败\r\n[原因]  "
                                    "上传超时(一般来说还在传,建议等待五分钟)"
                                )
                            ),
                        )
                # 此功能尚不可用, 上传文件无法成功, 可以联系bot管理员从data/essence_message目录获取数据库
