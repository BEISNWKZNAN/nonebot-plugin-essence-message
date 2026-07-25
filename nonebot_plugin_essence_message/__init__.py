from asyncio import gather, Lock
from typing import Union

from nonebot import get_driver, get_plugin_config, on_notice
from nonebot.adapters.onebot.v11 import (
    NoticeEvent,
    MessageSegment,
    GroupMessageEvent,
)
from nonebot.adapters.onebot.v11.message import Message
from nonebot.permission import SUPERUSER
from nonebot.log import logger
from nonebot.adapters.onebot.v11 import GROUP_ADMIN, GROUP_OWNER
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.plugin import PluginMetadata
from nonebot import require
import os

require("nonebot_plugin_alconna")
from arclet.alconna import Alconna, Args, Subcommand
from nonebot_plugin_alconna import AlconnaMatch, Match, Query, on_alconna
from .dataset import DatabaseHandler
from .config import Config
from .Helper import (
    ReactGoodNoticeEvent,
    EssenceEvent,
    GoodCounter,
    ReactWhaleNoticeEvent,
    SaveMsg,
    RateLimiter,
    SendMsg,
    NoticePermission,
    get_name,
    whale_essnece_set,
)
from .msg import Msg

__plugin_meta__ = PluginMetadata(
    name="精华消息管理",
    description="用于整理精华消息",
    usage=("自动存储精华消息备份并提供一些查询功能"),
    type="application",
    homepage="https://github.com/MovFish/nonebot-plugin-essence-message",
    config=Config,
    supported_adapters={"~onebot.v11"},
)


def essence_enable_rule(event: Union[GroupMessageEvent, NoticeEvent]) -> bool:
    return cfg.essence_pool(int(event.group_id)) is not None  # type: ignore


def good_essence_enabled(group_id: int) -> bool:
    return cfg.pool_feature_enabled(group_id, cfg.good_essence_enable_groups)


def whale_essence_enabled(group_id: int) -> bool:
    return cfg.pool_feature_enabled(group_id, cfg.whale_essnece_enable_groups)


def whale_essnece_rule(event: NoticeEvent):
    try:
        ReactWhaleNoticeEvent(**event.model_dump())
        return essence_enable_rule(event)
    except:
        return False


def essence_set_rule(event: NoticeEvent):
    try:
        EssenceEvent(**event.model_dump())
        return essence_enable_rule(event)
    except:
        return False


def trigood_rule(event: NoticeEvent):
    try:
        ReactGoodNoticeEvent(**event.model_dump())
        return essence_enable_rule(event)
    except:
        return False


cfg = get_plugin_config(Config)
db = DatabaseHandler(str(cfg.db()))
goodcount = GoodCounter(cfg.cache() / "good_cache.json", cfg.good_bound)
ratelimiter = RateLimiter(cfg.essence_random_limit, 43200, cfg.essence_random_cooldown)

fetchall_running: set[int] = set()
clean_running: set[int] = set()
ban_lock = Lock()


@get_driver().on_startup
async def _initialize_database() -> None:
    last_logged_percent = -10
    rebuilding = False

    def report_progress(completed: int, total: int) -> None:
        nonlocal last_logged_percent, rebuilding
        rebuilding = True
        percent = 100 if total == 0 else completed * 100 // total
        if percent >= last_logged_percent + 10 or completed == total:
            logger.info(
                "精华消息数据库重建进度：{}/{}（{}%）",
                completed,
                total,
                percent,
            )
            last_logged_percent = percent

    migrated_rows, migrated_images, backup_path = await db.initialize(report_progress)
    if not rebuilding:
        return
    if backup_path is not None:
        logger.info("重建前数据库备份已保存至：{}", backup_path)
    logger.info(
        "精华消息数据库迁移完成：转换 {} 条记录，提取 {} 张图片",
        migrated_rows,
        migrated_images,
    )

whale_essnece = on_notice(
    rule=whale_essnece_rule,
    priority=9,
    permission=NoticePermission,
    block=False,
)
essence_set = on_notice(rule=essence_set_rule, priority=10, block=False)
trigood = on_notice(rule=trigood_rule, priority=11, block=False)

essence_cmd = on_alconna(
    Alconna(
        "essence",
        Subcommand("help"),
        Subcommand("random"),
        Subcommand("search", Args["keyword", str]),
        Subcommand("rank", Args["type", str]),
    ),
    rule=essence_enable_rule,
    priority=5,
    block=False,
)

essence_cmd_admin = on_alconna(
    Alconna(
        "essence",
        Subcommand("fetchall"),
        Subcommand("export"),
        Subcommand("clean"),
        Subcommand("switch"),
    ),
    rule=essence_enable_rule,
    priority=6,
    permission=SUPERUSER | GROUP_ADMIN | GROUP_OWNER,
    block=False,
)


# 10024
@whale_essnece.handle()
async def _(event: NoticeEvent, bot: Bot):
    try:
        event = ReactWhaleNoticeEvent(**event.model_dump())
    except:
        await whale_essnece.finish()
    if whale_essence_enabled(event.group_id):
        if event.sub_type == "add":
            msg = None
            try:
                msg = await bot.get_msg(message_id=event.message_id)
            except Exception:
                pass
            if msg is None:
                logger.warning("无法获取精华消息，跳过保存：group_id={}", event.group_id)
                await whale_essnece.finish()
            await SaveMsg(
                db,
                msg,
                bot,
                event.time,
                event.group_id,
                msg["sender"]["user_id"],
                event.operator_id,
            ).add_to_dataset()
            await bot.set_msg_emoji_like(
                message_id=event.message_id,
                emoji_id="10024",
                set=True,
            )
        else:
            msg = None
            try:
                msg = await bot.get_msg(message_id=event.message_id)
            except Exception:
                pass
            if msg is None:
                logger.warning("无法获取精华消息，跳过删除：group_id={}", event.group_id)
                await whale_essnece.finish()
            await SaveMsg(
                db,
                msg,
                bot,
                event.time,
                event.group_id,
                msg["sender"]["user_id"],
                event.operator_id,
            ).del_from_dataset()
            await bot.set_msg_emoji_like(
                message_id=event.message_id,
                emoji_id="10024",
                set=False,
            )
    else:
        await whale_essnece.finish()


@essence_set.handle()
async def ___(event: NoticeEvent, bot: Bot):
    try:
        event = EssenceEvent(**event.model_dump())
    except:
        await essence_set.finish()
    msg = None
    if event.message_id is not None:
        try:
            msg = await bot.get_msg(message_id=event.message_id)
        except Exception:
            pass
    if msg is None and event.sub_type == "add":
        try:
            essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
        except Exception:
            essencelist = []
        for essence in essencelist:
            if essence.get("message_id") == event.message_id:
                msg = {"message": essence["content"]}
                break
    if msg is None:
        logger.warning(
            "无法获取精华消息内容，跳过事件：group_id={} sub_type={}",
            event.group_id,
            event.sub_type,
        )
        await essence_set.finish()
    if event.sub_type == "add":
        await SaveMsg(
            db, msg, bot, event.time, event.group_id, event.sender_id, event.operator_id
        ).add_to_dataset()
    elif event.sub_type == "delete":
        global clean_running
        if event.group_id in clean_running:
            await essence_set.finish()
        await SaveMsg(
            db, msg, bot, event.time, event.group_id, event.sender_id, event.operator_id
        ).del_from_dataset()
    await essence_set.finish()


@trigood.handle()
async def __(event: NoticeEvent, bot: Bot):
    try:
        event = ReactGoodNoticeEvent(**event.model_dump())
    except:
        await trigood.finish()
    if good_essence_enabled(event.group_id):
        msg_session = f"{event.group_id}_{event.message_id}"
        if isinstance(event.count, int):
            oldcount: int = goodcount.get(msg_session)
            goodcount.modify(msg_session, event.count)
            if oldcount <= event.count and goodcount.too_good_to_essence(msg_session):
                await whale_essnece_set(
                    whale_essence_enabled(event.group_id),
                    event.group_id,
                    event.message_id,
                    True,
                    bot,
                )
            if oldcount > event.count and not goodcount.too_good_to_essence(
                msg_session
            ):
                try:
                    msg = await bot.get_msg(message_id=event.message_id)
                    sender = msg["sender"]["user_id"]
                except Exception:
                    msg = None
                    sender = None
                    try:
                        essencelist = await bot.get_essence_msg_list(
                            group_id=event.group_id
                        )
                    except Exception:
                        essencelist = []
                    for essence in essencelist:
                        if essence.get("message_id") == event.message_id:
                            msg = {"message": essence["content"]}
                            sender = essence.get("sender_id")
                            break
                if msg is None or sender is None:
                    logger.warning(
                        "无法获取取消点赞的消息，跳过数据库删除：group_id={}",
                        event.group_id,
                    )
                    await trigood.finish()
                await SaveMsg(
                    db, msg, bot, event.time, event.group_id, sender, int(bot.self_id)
                ).del_from_dataset()
                try:
                    await whale_essnece_set(
                        whale_essence_enabled(event.group_id),
                        event.group_id,
                        event.message_id,
                        False,
                        bot,
                    )
                except:
                    await trigood.finish()
        else:
            if event.sub_type == "add":
                goodcount.add(msg_session)
                if goodcount.too_good_to_essence(msg_session):
                    await whale_essnece_set(
                        whale_essence_enabled(event.group_id),
                        event.group_id,
                        event.message_id,
                        True,
                        bot,
                    )
            elif event.sub_type == "remove":
                goodcount.remove(msg_session)
                if not goodcount.too_good_to_essence(msg_session):
                    try:
                        msg = await bot.get_msg(message_id=event.message_id)
                        sender = msg["sender"]["user_id"]
                    except Exception:
                        msg = None
                        sender = None
                        try:
                            essencelist = await bot.get_essence_msg_list(
                                group_id=event.group_id
                            )
                        except Exception:
                            essencelist = []
                        for essence in essencelist:
                            if essence.get("message_id") == event.message_id:
                                msg = {"message": essence["content"]}
                                sender = essence.get("sender_id")
                                break
                    if msg is None or sender is None:
                        logger.warning(
                            "无法获取取消点赞的消息，跳过数据库删除：group_id={}",
                            event.group_id,
                        )
                        await trigood.finish()
                    await SaveMsg(
                        db,
                        msg,
                        bot,
                        event.time,
                        event.group_id,
                        sender,
                        int(bot.self_id),
                    ).del_from_dataset()
                    await whale_essnece_set(
                        whale_essence_enabled(event.group_id),
                        event.group_id,
                        event.message_id,
                        False,
                        bot,
                    )


@essence_cmd.assign("help")
async def help_cmd():
    await essence_cmd.finish(
        "使用说明:\n"
        + "essence help - 显示此帮助信息\n"
        + "essence random - 从当前随机发送一条精华消息\n"
        + "essence rank sender - 显示发送者排行榜\n"
        + "essence rank operator - 显示设精数量排行榜\n"
        + "essence search <str> - 搜索全部文本，随机恢复至多 5 条\n"
        + "essence fetchall - [管理员]同步群内全部精华消息及媒体文件到本地\n"
        + "essence export - [管理员]打包导出数据库、CSV 和媒体文件\n"
        + "essence switch - [管理员]切换手动清理模式，暂停或恢复删精事件的数据库联动\n"
        + "essence clean - [管理员]备份后删除群内全部精华消息，数据库记录保留"
    )


@essence_cmd.assign("random")
async def random_cmd(event: GroupMessageEvent, bot: Bot):
    if ratelimiter.reach_limit(event.get_session_id()):
        await essence_cmd.finish("过量抽精华有害身心健康")
    else:
        pool = cfg.essence_pool(event.group_id)
        msg = await db.random_essence(pool or ())
        if msg == None:
            await essence_cmd.finish(
                MessageSegment.text(
                    "目前数据库里没有精华消息，可以使用essence fetchall抓取群里的精华消息"
                )
            )
        else:
            rand = SendMsg(Msg.from_database(msg[4], msg[5]), db, bot, msg[1])
            random = (
                MessageSegment.text(f"{await rand.get_name(msg[2])}:")
                + await rand.get_msg()
            )
            random.reduce()
            await essence_cmd.finish(random)


@essence_cmd.assign("search")
async def search_cmd(
    event: GroupMessageEvent, bot: Bot, keyword: Match[str] = AlconnaMatch("keyword")
):
    pool = cfg.essence_pool(event.group_id)
    msg = await db.search_entries(pool or (), keyword.result)
    if not any(msg):
        await essence_cmd.finish("没有找到")
    sender_ids = [sender_id for _, _, sender_id, _, _, _ in msg]
    names = await gather(
        *[get_name(db, bot, row[1], sender_id) for row, sender_id in zip(msg, sender_ids)]
    )
    result = Message()
    for index, (name, row) in enumerate(zip(names, msg)):
        if index:
            result += MessageSegment.text("\n\n")
        message_type, message_data = row[4], row[5]
        rendered = SendMsg(
            Msg.from_database(message_type, message_data),
            db,
            bot,
            row[1],
        )
        result += MessageSegment.text(f"{name}:")
        result += await rendered.get_msg()
    result.reduce()
    await essence_cmd.finish(result)


@essence_cmd.assign("rank")
async def rank_cmd(
    event: GroupMessageEvent, bot: Bot, type: Query[str] = Query("~type")
):
    pool = cfg.essence_pool(event.group_id)
    if type.result == "sender":
        rank = await db.sender_rank(pool or (), event.user_id)
    elif type.result == "operator":
        rank = await db.operator_rank(pool or (), event.user_id)
    else:
        await essence_cmd.finish("排行类型仅支持 sender 或 operator")

    names = await gather(*[get_name(db, bot, event.group_id, id) for id, _, _ in rank])
    result = [
        f"第{r}名: {name}, {count}条精华消息"
        for name, (_, count, r) in zip(names, rank)
    ]
    await essence_cmd.finish(MessageSegment.text("\n".join(result)))


@essence_cmd_admin.assign("fetchall")
async def fetchall_cmd(event: GroupMessageEvent, bot: Bot):
    global fetchall_running
    async with ban_lock:
        if event.group_id in fetchall_running:
            frist = False
            await essence_cmd.finish("fetchall正在运行")
        else:
            fetchall_running.add(event.group_id)
            frist = True
    if frist:
        try:
            essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
            savecount = 0
            for essence in essencelist:
                try:
                    msg = {"message": essence["content"]}
                    savecount += int(
                        await SaveMsg(
                            db,
                            msg,
                            bot,
                            essence.get("operator_time", event.time),
                            event.group_id,
                            essence["sender_id"],
                            essence["operator_id"],
                        ).add_to_dataset()
                    )
                except:
                    continue
        except Exception as e:
            async with ban_lock:
                fetchall_running.remove(event.group_id)
            await essence_cmd.finish(f"fetchall过程中出错{e}")
        async with ban_lock:
            fetchall_running.remove(event.group_id)
        await essence_cmd.finish(
            f"成功保存 {savecount}/{len(essencelist)} 条精华消息"
        )


@essence_cmd_admin.assign(
    "export",
)
async def export_cmd(event: GroupMessageEvent, bot: Bot):
    pool = cfg.essence_pool(event.group_id)
    path = await db.export_group_data(pool or (), event.group_id)
    try:
        await bot.upload_group_file(
            group_id=event.group_id,
            file=path,
            name=os.path.basename(path),
        )
        await essence_cmd.finish("导出完成，请检查群文件")
    except:
        await essence_cmd.finish(
            "上传失败，请联系 Bot 管理员获取插件数据目录下的 "
            f"{os.path.basename(path)}"
        )


@essence_cmd_admin.assign(
    "clean",
)
async def clean_cmd(event: GroupMessageEvent, bot: Bot):
    global clean_running
    async with ban_lock:
        if event.group_id in clean_running:
            frist = False
            await essence_cmd.finish("clean 正在运行")
        else:
            clean_running.add(event.group_id)
            frist = True
    if frist:
        try:
            essencelist = await bot.get_essence_msg_list(group_id=event.group_id)
            await essence_cmd.send("开始抓取目前精华消息")
            savecount = 0
            for essence in essencelist:
                try:
                    msg = {"message": essence["content"]}
                    savecount += int(
                        await SaveMsg(
                            db,
                            msg,
                            bot,
                            essence.get("operator_time", event.time),
                            event.group_id,
                            essence["sender_id"],
                            essence["operator_id"],
                        ).add_to_dataset()
                    )
                except:
                    continue
            await essence_cmd.send("开始清理")
            delcount = 0
            for essence in essencelist:
                try:
                    await bot.delete_essence_msg(message_id=essence["message_id"])
                    delcount += 1
                except Exception as e:
                    continue
        except Exception as e:
            async with ban_lock:
                clean_running.remove(event.group_id)
            await essence_cmd.finish(f"clean过程中出错{e}")
        async with ban_lock:
            clean_running.remove(event.group_id)
        await essence_cmd.finish(
            f"成功删除 {delcount}/{len(essencelist)} 条精华消息"
        )


@essence_cmd_admin.assign(
    "switch",
)
async def switch_cmd(event: GroupMessageEvent, bot: Bot):
    if event.group_id in clean_running:
        clean_running.remove(event.group_id)
        await essence_cmd.finish("已关闭手动清理模式，删精时将同步删除数据库记录")
    else:
        clean_running.add(event.group_id)
        await essence_cmd.finish("已开启手动清理模式，删精时将保留数据库记录")
