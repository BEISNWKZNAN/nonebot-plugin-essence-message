import httpx
import asyncio
import base64
import time
import os

from .dateset import DatabaseHandler
from .config import config

from nonebot import get_plugin_config
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent


db = DatabaseHandler(config.db())
asyncio.run(db._create_table())
cfg = get_plugin_config(config)


def trigger_rule(event: GroupMessageEvent) -> bool:
    return (event.group_id in cfg.essence_enable_groups) or (
        "all" in cfg.essence_enable_groups
    )


async def get_name(bot: Bot, group_id: int, id: int) -> str:
    ti = int(time.time())
    i = await db.get_latest_nickname(group_id, id)
    if i == None:
        try:
            sender = await asyncio.wait_for(
                bot.get_group_member_info(group_id=group_id, user_id=id), 3
            )
            name = sender["nickname"] if (sender["card"] == None) else sender["card"]
            await db.insert_user_mapping(
                name, sender["group_id"], sender["user_id"], ti
            )
            return name
        except:
            return "<unknown>"
    else:
        if ti - i[1] > 86400:
            try:
                sender = await asyncio.wait_for(
                    bot.get_group_member_info(group_id=group_id, user_id=id), 2
                )
                name = (
                    sender["nickname"] if (sender["card"] == None) else sender["card"]
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


async def format_msg(msg, bot: Bot):
    result = []
    for msg_part in msg["message"]:
        if msg_part["type"] == "text":
            re = [msg_part["type"], msg_part["data"]["text"]]
        elif msg_part["type"] == "image":
            async with httpx.AsyncClient() as client:
                r = await client.get(msg_part["data"]["url"])
            if r.status_code == 200:
                base64str = base64.b64encode(r.content).decode("utf-8")
                re = [msg_part["type"], f"base64://{base64str}"]
            else:
                return None
        elif msg_part["type"] == "at":
            re = [msg_part["type"], msg_part["data"]["qq"]]
        elif msg_part["type"] == "reply":
            try:
                remsg = await bot.get_msg(message_id=msg_part["data"]["id"])
                remsg = await format_msg(remsg, bot)
                remsg = f"[{remsg[0]},{remsg[1]}]"
            except:
                remsg = "[]"
            re = [msg_part["type"], remsg]
            pass
        result.append(re)
    if len(result) == 1:
        result = result[0]
    else:
        remsg = ""
        for re in result:
            remsg = remsg + f"[{re[0]},{re[1]}],"
        result = ["group", remsg]
    return result


async def fetchpic(essencelist):
    image_directory =config.img()
    os.makedirs(image_directory, exist_ok=True)
    savecount = 0
    
    async with httpx.AsyncClient() as client:
        for essence in essencelist:
            sender_time = essence['sender_time']
            sender_nick = essence['sender_nick']
            for content in essence['content']:
                if content['type'] == 'image':
                    image_url = content['data']['url']
                    response = await client.get(image_url)
                    if response.status_code == 200:
                        image_data = response.content
                        image_filename = f"{sender_time}_{sender_nick}.jpeg"
                        image_path_count = 1
                        image_save_path = os.path.join(image_directory, image_filename)
                        while os.path.exists(image_save_path):
                            image_filename = f"{sender_time}_{sender_nick}({image_path_count}).jpeg"
                            image_save_path = os.path.join(image_directory, image_filename)
                            image_path_count += 1
                        with open(image_save_path, 'wb') as image_file:
                            image_file.write(image_data)
                            savecount += 1
    
    return savecount