import asyncio
import base64
import json
import os
from pathlib import Path
from time import time
import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11 import NoticeEvent
from nonebot.adapters.onebot.v11.message import Message
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict, Union, cast
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.permission import Permission
from pydantic import BaseModel

from .dataset import DatabaseHandler


async def _notice_permission(event: NoticeEvent, bot: "Bot") -> bool:
    try:
        user_id = event.user_id  # type: ignore
        member_info = await bot.get_group_member_info(group_id=event.group_id, user_id=user_id)  # type: ignore
    except Exception:
        return False
    return (
        f"{bot.adapter.get_name().split(maxsplit=1)[0].lower()}:{user_id}"
        in bot.config.superusers
        or str(user_id) in bot.config.superusers
        or member_info["role"] == "admin"  # type: ignore
        or member_info["role"] == "owner"  # type: ignore
    )


NoticePermission: Permission = Permission(_notice_permission)


async def whale_essnece_set(
    enable_whale: bool, group_id: int, message_id: int, is_add: bool, bot: "Bot"
):
    if enable_whale:
        await bot.set_msg_emoji_like(
            message_id=message_id, emoji_id="128051", set=is_add
        )
    else:
        if is_add:
            await bot.set_essence_msg(message_id=message_id)
        else:
            await bot.delete_essence_msg(message_id=message_id)


class GoodEmojiLike(BaseModel):
    emoji_id: Literal["76"]
    count: int


class WhaleEmojiLike(BaseModel):
    emoji_id: Literal["128051"]
    count: int


class ReactGoodNoticeEvent(NoticeEvent):
    message_id: int
    group_id: int
    user_id: int
    notice_type: Literal["group_msg_emoji_like"]
    is_add: bool
    likes: List[GoodEmojiLike]

    @property
    def sub_type(self) -> str:
        return "add" if self.is_add else "remove"

    @property
    def code(self) -> str:
        return self.likes[0].emoji_id if self.likes else "76"

    @property
    def count(self) -> int:
        return self.likes[0].count if self.likes else 0


class ReactWhaleNoticeEvent(NoticeEvent):
    message_id: int
    user_id: int
    group_id: int
    notice_type: Literal["group_msg_emoji_like"]
    is_add: bool
    likes: List[WhaleEmojiLike]

    @property
    def operator_id(self) -> int:
        return self.user_id

    @property
    def sub_type(self) -> str:
        return "add" if self.is_add else "remove"

    @property
    def code(self) -> str:
        return self.likes[0].emoji_id if self.likes else "128051"


class EssenceEvent(NoticeEvent):
    group_id: int
    notice_type: Literal["essence"]
    sub_type: Union[Literal["add"], Literal["delete"]]
    sender_id: int
    message_id: Optional[int] = None
    operator_id: int


class GoodCounter:
    goodmap: dict[str, int] = {}
    cache_file: Path
    good_bound: int

    def __init__(self, cache_file: Path, good_bound: int):
        self.cache_file = cache_file
        self.good_bound = good_bound
        if self.cache_file.exists():
            with self.cache_file.open("r", encoding="utf-8") as f:
                try:
                    self.goodmap = json.load(f)
                except json.JSONDecodeError:
                    self.goodmap = {}
        else:
            self.goodmap = {}

    def __del__(self):
        with self.cache_file.open("w", encoding="utf-8") as f:
            json.dump(self.goodmap, f, ensure_ascii=False, indent=4)

    def get(self, message_session: str) -> int:
        return self.goodmap.get(message_session, 0)

    def add(self, message_session: str) -> int:
        self.goodmap[message_session] = self.goodmap.get(message_session, 0) + 1
        return self.goodmap[message_session]

    def remove(self, message_session: str) -> int:
        self.goodmap[message_session] = max(0, self.goodmap.get(message_session, 0) - 1)
        return self.goodmap[message_session]

    def modify(self, message_session: str, count: int) -> int:
        self.goodmap[message_session] = count
        return self.goodmap[message_session]

    def ToogoodToessence(self, message_session: str):
        return self.goodmap[message_session] >= self.good_bound


MessageType = Literal["text", "image", "at", "reply", "group", "face"]


MessageResult = Tuple[MessageType, str]


async def format_msg(raw_msg: dict[str, Any], bot: "Bot") -> MessageResult:
    msg = raw_msg
    result: List[Tuple[MessageType, str]] = []

    for msg_part in msg["message"]:
        msg_type = msg_part["type"]

        if msg_type == "text":
            data = msg_part["data"]
            re: Tuple[MessageType, str] = ("text", data["text"])

        elif msg_type == "image":
            data = msg_part["data"]
            async with httpx.AsyncClient() as client:
                r = await client.get(data["url"])
            if r.status_code == 200:
                base64str = base64.b64encode(r.content).decode("utf-8")
                re = ("image", f"base64://{base64str}")
            else:
                raise ValueError(f"Failed to fetch image: status code {r.status_code}")

        elif msg_type == "at":
            data = msg_part["data"]
            re = ("at", data["qq"])

        elif msg_type == "face":
            data = msg_part["data"]
            re = ("face", data["id"])

        elif msg_type == "reply":
            data = msg_part["data"]
            try:
                remsg = await bot.get_msg(message_id=int(data["id"]))
                remsg = await format_msg(remsg, bot)
                remsg_str = f"[{remsg[0]},{remsg[1]}]"
            except Exception as e:
                remsg_str = "[]"
            re = ("reply", remsg_str)

        else:
            raise ValueError(f"Unsupport message type: {msg_type}")

        result.append(re)

    if len(result) == 1:
        return result[0]
    else:
        remsg = ""
        for re in result:
            remsg = remsg + f"[{re[0]},{re[1]}],"
        return ("group", remsg)


class SaveData(TypedDict):
    time: int
    group_id: int
    sender_id: int
    operator_id: int
    message_type: MessageType
    message_data: str


class SaveMsg:
    msg_data: SaveData
    db: DatabaseHandler

    def __init__(
        self,
        db: DatabaseHandler,
        msg: dict[str, Any],
        bot: "Bot",
        timestamp: int,
        group_id: int,
        sender_id: int,
        operator_id: int,
    ) -> None:
        self.db = db
        self.msg = msg
        self.bot = bot
        self.timestamp = timestamp
        self.group_id = group_id
        self.sender_id = sender_id
        self.operator_id = operator_id

    async def add_to_dataset(self):
        data = await format_msg(self.msg, self.bot)
        self.msg_data = {
            "time": self.timestamp,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "operator_id": self.operator_id,
            "message_type": data[0],
            "message_data": data[1],
        }
        if not await self.db.entry_exists(self.msg_data):
            await get_name(
                self.db, self.bot, self.group_id, self.sender_id, False
            )  # 设精更新用户昵称
            return await self.db.insert_data(self.msg_data)
        else:
            return False

    async def del_from_dataset(self):
        data = await format_msg(self.msg, self.bot)
        self.msg_data = {
            "time": self.timestamp,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "operator_id": self.operator_id,
            "message_type": data[0],
            "message_data": data[1],
        }
        return await self.db.delete_data(self.msg_data)


class RateLimiter:
    def __init__(self, limit: int, reset_interval: int, cooldown: int):
        self.limit: int = limit
        self.reset_interval: int = reset_interval
        self.cooldown: int = cooldown
        self.random_count: Dict[str, int] = {}
        self.first_time_count: Dict[str, int] = {}
        self.last_time_count: Dict[str, int] = {}

    def reach_limit(self, session_id: str) -> bool:
        current_time = int(time())
        if (
            session_id not in self.random_count
            or current_time - self.first_time_count[session_id] >= self.reset_interval
        ):
            self.random_count[session_id] = 0
            self.first_time_count[session_id] = current_time
            self.last_time_count[session_id] = current_time - 10 - self.cooldown
        reach_CD = (current_time - self.last_time_count[session_id]) < self.cooldown
        reach_limit = self.random_count[session_id] >= self.limit or reach_CD
        self.last_time_count[session_id] = current_time
        self.random_count[session_id] += int(not reach_CD)

        return reach_limit


class SendMsgData:
    message_type: MessageType
    contain_msg: Union["SendMsgData", list["SendMsgData"], str]

    def __init__(self, message_type: MessageType, data: str) -> None:
        self.message_type = message_type

        def parse(input_str):
            while input_str.startswith("[") and input_str.endswith("]"):
                input_str = input_str[1:-1]
            input_str = "[" + input_str + "]"
            input_str = input_str.strip().strip(",").strip()
            result = []
            stack = []
            current = ""
            for char in input_str:
                if char == "[":
                    if stack:
                        current += char
                    stack.append(char)
                elif char == ",":
                    if len(stack) == 1:
                        current = current.strip().strip(",").strip()
                        result.append(current)
                        current = ""
                    else:
                        current += char
                elif char == "]":
                    if len(stack) == 1:
                        current = current.strip().strip(",").strip()
                        result.append(current)
                        current = ""
                    else:
                        current += char
                    stack.pop()
                else:
                    current += char
            return [item.strip().strip(",").strip() for item in result if item]

        if message_type == "group":
            self.contain_msg = []
            result = parse(data)
            for i in range(len(result)):
                resul = parse(result[i])
                if resul[0] == "text" and len(resul) == 1:
                    resul.append(" ")
                self.contain_msg.append(SendMsgData(resul[0], resul[1]))
            if len(self.contain_msg) == 1:
                self.message_type = self.contain_msg[0].message_type
                self.contain_msg = self.contain_msg[0].contain_msg
        elif message_type == "reply":
            result = parse(data)
            if len(result) == 0:
                self.contain_msg = SendMsgData("text", "")
            else:
                rust: str = ""
                if result[0] == "group":
                    for i in range(1, len(result)):
                        rust += result[i] + ","
                else:
                    rust = result[1]
                self.contain_msg = SendMsgData(result[0], rust)
        else:
            self.contain_msg = data


async def get_name(
    db: DatabaseHandler, bot: Bot, group_id: int, id: int, use_cache: bool = True
) -> str:
    ti = int(time())
    i = await db.get_latest_nickname(group_id, id)
    if i == None:
        try:
            sender = await asyncio.wait_for(
                bot.get_group_member_info(group_id=group_id, user_id=id), 3
            )
            name = (
                sender["nickname"]
                if (sender["card"] == None or sender["card"] == "")
                else sender["card"]
            )
            await db.insert_user_mapping(
                name, sender["group_id"], sender["user_id"], ti
            )
            return name
        except:
            return "<unknown>"
    else:
        if not use_cache:
            try:
                sender = await asyncio.wait_for(
                    bot.get_group_member_info(
                        group_id=group_id, user_id=id, no_cache=True
                    ),
                    2,
                )
                name = (
                    sender["nickname"]
                    if (sender["card"] == None or sender["card"] == "")
                    else sender["card"]
                )
                await db.insert_user_mapping(
                    name,
                    sender["group_id"],
                    sender["user_id"],
                    ti,
                )
                return name
            except:
                return i[0]
        else:
            return i[0]


class SendMsg:
    sender_id: int
    data: SendMsgData

    def __init__(
        self,
        data: SendMsgData,
        db: DatabaseHandler,
        bot: "Bot",
        group_id: int,
        depth: int = 0,
    ) -> None:
        self.data = data
        self.db = db
        self.bot = bot
        self.group_id = group_id
        self.depth = depth

    async def get_name(self, id: int) -> str:
        return await get_name(self.db, self.bot, self.group_id, id)

    async def get_msg(self) -> Union[MessageSegment, Message]:
        if self.data.message_type == "at":
            result = MessageSegment.text(
                f"@{await self.get_name(int(cast(str, self.data.contain_msg)))} "
            )
        elif self.data.message_type == "image":
            result = MessageSegment.image(file=cast(str, self.data.contain_msg))
        elif self.data.message_type == "text":
            content = (
                "" if self.data.contain_msg == "None" else str(self.data.contain_msg)
            )
            result = MessageSegment.text(content)
        elif self.data.message_type == "reply":
            result = await SendMsg(
                cast(SendMsgData, self.data.contain_msg),
                self.db,
                self.bot,
                self.group_id,
                depth=self.depth,
            ).get_msg() + MessageSegment.text(("\n" + ">" * self.depth + " "))
        elif self.data.message_type == "group":
            resul: List[Union[MessageSegment, Message]] = []
            for msg in cast(list[SendMsgData], self.data.contain_msg):
                resul.append(
                    await SendMsg(
                        msg, self.db, self.bot, self.group_id, depth=self.depth + 1
                    ).get_msg()
                )
            result = resul[0]
            for i in range(1, len(resul)):
                result = result + resul[i]
        elif self.data.message_type == "face":
            result = MessageSegment.face(int(cast(str, self.data.contain_msg)))

        return result


async def fetchpic(essencelist, image_directory):
    os.makedirs(image_directory, exist_ok=True)
    savecount = 0

    async with httpx.AsyncClient() as client:
        for essence in essencelist:
            sender_time = essence["operator_time"]
            sender_nick = essence["sender_nick"]
            for content in essence["content"]:
                if content["type"] == "image":
                    image_url = content["data"]["url"]
                    response = await client.get(image_url)
                    if response.status_code == 200:
                        image_data = response.content
                        image_filename = f"{sender_time}_{sender_nick}.jpeg"
                        image_path_count = 1
                        image_save_path = os.path.join(image_directory, image_filename)
                        while os.path.exists(image_save_path):
                            image_filename = (
                                f"{sender_time}_{sender_nick}({image_path_count}).jpeg"
                            )
                            image_save_path = os.path.join(
                                image_directory, image_filename
                            )
                            image_path_count += 1
                        with open(image_save_path, "wb") as image_file:
                            image_file.write(image_data)
                            savecount += 1
    return savecount
