from json import dumps, loads
import httpx
import base64
from typing import cast

from nonebot import on_type, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    NoticeEvent,
    GroupMessageEvent,
    NetworkError,
    ActionFailed,
    MessageSegment,
    MessageEvent,
)
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.params import CommandArg
from nonebot.matcher import current_bot
from nonebot.log import logger

from .dateset import DatabaseHandler
from .config import config

essence_set = on_type(
    (NoticeEvent,),
    priority=10,
    block=False,
)

command_matcher = on_command("essence", priority=5, block=True)
db = DatabaseHandler(config.db())


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
                dumps(msg["message"][0]["data"]),
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
    else:
        pass


@staticmethod
async def upload_group_file(group_id: int, file: str, name: str):
    bot: Bot = cast(Bot, current_bot.get())
    try:
        await bot.upload_group_file(group_id=group_id, file=file, name=name)
    except (ActionFailed, NetworkError) as e:
        logger.error(e)
        if (
            isinstance(e, ActionFailed)
        ):
            await bot.send_group_msg(
                group_id=group_id,
                message=Message(
                    MessageSegment.text(
                        str(e)
                    )
                ),
            )
        elif isinstance(e, NetworkError):
            await bot.send_group_msg(
                group_id=group_id,
                message=Message(
                    MessageSegment.text(
                        "[ERROR]文件上传失败\r\n[原因]  "
                        "上传超时(一般来说还在传,建议等待五分钟)"
                    )
                ),
            )


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
                msg = db.random_essence(event.group_id)
                sender = await bot.get_group_member_info(
                    group_id=event.group_id, user_id=msg[2]
                )
                if msg[4] == "text":
                    await command_matcher.finish(
                        MessageSegment.text(
                            f"{sender['nickname']}:{loads(msg[5])['text']}"
                        )
                    )
                elif msg[4] == "image":
                    await command_matcher.finish(MessageSegment.image(file=msg[5]))
            elif args[0] == "search":
                if len(args) < 2:
                    await command_matcher.finish("请输入关键字")
                else:
                    msg = db.search_entries(event.group_id, args[1])
                    if len(msg) == 0 :
                        await command_matcher.finish("没有找到")
                    result = []
                    for _, _, _, sender_id, _, data in msg:
                        sender = await bot.get_group_member_info(
                            group_id=event.group_id, user_id=sender_id
                        )
                        result.append(f"{sender['nickname']}: {loads(data)['text']}")
                    await command_matcher.finish(MessageSegment.text("\n".join(result)))
            elif args[0] == "rank":
                if len(args) < 2:
                    await command_matcher.finish("请输入完整命令")
                else:
                    if args[1] == "sender":
                        rank = db.sender_rank(event.group_id)
                        result = []
                        for index, (id, count) in enumerate(rank, start=1):
                            sender = await bot.get_group_member_info(
                                group_id=event.group_id, user_id=id
                            )
                            result.append(
                                f"第{index}名: {sender['nickname']}, {count}条精华消息"
                            )
                        await command_matcher.finish(
                            MessageSegment.text("\n".join(result))
                        )
                    elif args[1] == "operator":
                        rank = db.operator_rank(event.group_id)
                        result = []
                        for index, (id, count) in enumerate(rank, start=1):
                            sender = await bot.get_group_member_info(
                                group_id=event.group_id, user_id=id
                            )
                            result.append(
                                f"第{index}名: {sender['nickname']}, {count}条精华消息"
                            )
                        await command_matcher.finish(
                            MessageSegment.text("\n".join(result))
                        )
            elif args[0] == "export":
                # await upload_group_file(
                #     group_id=event.group_id,
                #     file=f'{db.export_group_data(event.group_id)}',
                #     name="essence.db",
                # )
                # 此功能尚不可用, 上传文件无法成功
                pass
