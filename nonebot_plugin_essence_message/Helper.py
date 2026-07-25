import asyncio
import json
import os
from pathlib import Path
from time import time
import httpx
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11 import NoticeEvent
from nonebot.adapters.onebot.v11.message import Message
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.permission import Permission
from pydantic import BaseModel

from .dataset import DatabaseHandler
from .msg import Msg, resolve_image_path


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

    def _save(self) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_file.open("w", encoding="utf-8") as f:
            json.dump(self.goodmap, f, ensure_ascii=False, indent=4)

    def get(self, message_session: str) -> int:
        return self.goodmap.get(message_session, 0)

    def add(self, message_session: str) -> int:
        self.goodmap[message_session] = self.goodmap.get(message_session, 0) + 1
        self._save()
        return self.goodmap[message_session]

    def remove(self, message_session: str) -> int:
        self.goodmap[message_session] = max(0, self.goodmap.get(message_session, 0) - 1)
        self._save()
        return self.goodmap[message_session]

    def modify(self, message_session: str, count: int) -> int:
        self.goodmap[message_session] = count
        self._save()
        return self.goodmap[message_session]

    def too_good_to_essence(self, message_session: str) -> bool:
        return self.get(message_session) >= self.good_bound


class SaveData(TypedDict):
    time: int
    group_id: int
    sender_id: int
    operator_id: int
    message_type: str
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
        data = await Msg.from_onebot(
            self.msg,
            self.bot,
            Path(self.db.db_path).parent / "img",
            Path(self.db.db_path).parent,
        )
        self.msg_data = {
            "time": self.timestamp,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "operator_id": self.operator_id,
            "message_type": data.type,
            "message_data": data.serialize(),
        }
        if not await self.db.entry_exists(self.msg_data):
            await get_name(
                self.db, self.bot, self.group_id, self.sender_id, False
            )  # 设精更新用户昵称
            return await self.db.insert_data(self.msg_data)
        else:
            return False

    async def del_from_dataset(self):
        data = await Msg.from_onebot(
            self.msg,
            self.bot,
            Path(self.db.db_path).parent / "img",
            Path(self.db.db_path).parent,
        )
        self.msg_data = {
            "time": self.timestamp,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "operator_id": self.operator_id,
            "message_type": data.type,
            "message_data": data.serialize(),
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
    data: Msg

    def __init__(
        self,
        data: Msg,
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

    async def get_msg(self) -> Message:
        messages = self.data.children if self.data.type == "group" else [self.data]
        return await self._render(messages, self.depth)

    async def _render(self, messages: list[Msg], depth: int) -> Message:
        result = Message()
        database_dir = Path(self.db.db_path).parent
        for message in messages:
            if message.type == "at":
                result += MessageSegment.text(
                    f"@{await self.get_name(int(message.data.get('qq', 0)))} "
                )
            elif message.type == "image":
                source = resolve_image_path(message.data, database_dir)
                if source is not None:
                    result += MessageSegment.image(file=source)
            elif message.type == "text":
                result += MessageSegment.text(str(message.data.get("text", "")))
            elif message.type == "reply":
                result += await self._render(message.children, depth + 1)
                result += MessageSegment.text("\n" + ">" * depth + " ")
            elif message.type == "face":
                result += MessageSegment.face(int(message.data.get("id", 0)))
            else:
                result += MessageSegment(type=message.type, data=message.data)
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
