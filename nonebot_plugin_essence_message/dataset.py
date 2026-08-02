import asyncio
import csv
import hashlib
import io
from dataclasses import dataclass
import aiosqlite
import regex
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Sequence

from nonebot.log import logger

from .msg import Msg


DATABASE_SCHEMA_VERSION = 3
EssenceRow = tuple[int, int, int, int, str, str]
MatchMode = Literal["contains", "exact", "regex"]
ResultOrder = Literal["random", "newest", "oldest"]
MESSAGE_TYPES = frozenset(
    {"text", "image", "record", "forward", "mixed"}
)
MAX_REGEX_LENGTH = 256
MAX_MATCH_TEXT_LENGTH = 20_000
REGEX_TIMEOUT_SECONDS = 0.05


class QueryValidationError(ValueError):
    """A query option or regular expression is invalid."""


@dataclass(frozen=True)
class EssenceQuery:
    group_ids: tuple[int, ...]
    from_time: Optional[int] = None
    to_time: Optional[int] = None
    sender_id: Optional[int] = None
    operator_id: Optional[int] = None
    exclude_sender_id: Optional[int] = None
    exclude_operator_id: Optional[int] = None
    source_group_id: Optional[int] = None
    message_type: Optional[str] = None
    keyword: Optional[str] = None
    match_mode: MatchMode = "contains"
    order: ResultOrder = "random"
    count: int = 1

    def __post_init__(self) -> None:
        groups = tuple(dict.fromkeys(self.group_ids))
        object.__setattr__(self, "group_ids", groups)
        for label, value in (
            ("发送者 ID", self.sender_id),
            ("操作者 ID", self.operator_id),
            ("排除发送者 ID", self.exclude_sender_id),
            ("排除操作者 ID", self.exclude_operator_id),
            ("来源群 ID", self.source_group_id),
        ):
            if value is not None and value <= 0:
                raise QueryValidationError(f"{label} 必须为正整数")
        if self.count < 1:
            raise QueryValidationError("返回数量必须大于 0")
        if self.from_time is not None and self.to_time is not None:
            if self.from_time > self.to_time:
                raise QueryValidationError("开始时间不能晚于结束时间")
        if self.sender_id is not None and self.sender_id == self.exclude_sender_id:
            raise QueryValidationError("发送者 ID 不能同时包含和排除")
        if self.operator_id is not None and self.operator_id == self.exclude_operator_id:
            raise QueryValidationError("操作者 ID 不能同时包含和排除")
        if (
            groups
            and self.source_group_id is not None
            and self.source_group_id not in groups
        ):
            raise QueryValidationError("指定群不属于当前精华共享池")
        if self.message_type is not None and self.message_type not in MESSAGE_TYPES:
            supported = "/".join(sorted(MESSAGE_TYPES))
            raise QueryValidationError(f"未知消息类型, 支持: {supported}")
        if self.order not in {"random", "newest", "oldest"}:
            raise QueryValidationError("排序仅支持 random、newest 或 oldest")
        if self.match_mode not in {"contains", "exact", "regex"}:
            raise QueryValidationError("未知文本匹配模式")
        if self.match_mode != "contains" and self.keyword is None:
            raise QueryValidationError("精确或正则匹配必须提供关键词")


def parse_query_time(value: str, *, end_of_day: bool = False) -> int:
    value = value.strip()
    parsed = None
    for format_string in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(value, format_string)
            break
        except ValueError:
            pass
    if parsed is None:
        raise QueryValidationError(
            "时间格式应为 YYYY-MM-DD 或 YYYY-MM-DDTHH:mm[:ss]"
        )
    if end_of_day and len(value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return int(parsed.timestamp())


def parse_duration(value: str) -> timedelta:
    match = regex.fullmatch(r"([1-9]\d*)([mhdw])", value.strip(), timeout=0.05)
    if match is None:
        raise QueryValidationError("时长格式应为正整数加 m、h、d 或 w")
    amount = int(match.group(1))
    seconds = amount * {
        "m": 60,
        "h": 3600,
        "d": 86400,
        "w": 604800,
    }[match.group(2)]
    try:
        return timedelta(seconds=seconds)
    except OverflowError as error:
        raise QueryValidationError("相对时长过大") from error


def build_query_from_args(
    get_value: Callable[[str], Any],
    command: Literal["random", "search"],
    group_ids: Sequence[int],
) -> EssenceQuery:
    prefix = f"{command}."
    from_value = get_value(prefix + "from.from_time")
    to_value = get_value(prefix + "to.to_time")
    last_value = get_value(prefix + "last.last_time")
    if last_value is not None and (from_value is not None or to_value is not None):
        raise QueryValidationError("--last 不能与 --from 或 --to 同时使用")

    from_time = parse_query_time(from_value) if from_value is not None else None
    to_time = (
        parse_query_time(to_value, end_of_day=True)
        if to_value is not None
        else None
    )
    if last_value is not None:
        from_time = int((datetime.now() - parse_duration(last_value)).timestamp())

    exact = get_value(prefix + "exact") is not None
    use_regex = get_value(prefix + "regex") is not None
    if exact and use_regex:
        raise QueryValidationError("--exact 与 --regex 不能同时使用")

    count = get_value(prefix + "count.count")
    default_count, max_count = (1, 10) if command == "random" else (5, 20)
    if count is None:
        count = default_count
    if count > max_count:
        raise QueryValidationError(f"{command} 最多返回 {max_count} 条")

    order = get_value(prefix + "order.order")
    return EssenceQuery(
        group_ids=tuple(group_ids),
        from_time=from_time,
        to_time=to_time,
        sender_id=get_value(prefix + "sender-id.sender_id"),
        operator_id=get_value(prefix + "operator-id.operator_id"),
        exclude_sender_id=get_value(prefix + "exclude-sender-id.exclude_sender_id"),
        exclude_operator_id=get_value(
            prefix + "exclude-operator-id.exclude_operator_id"
        ),
        source_group_id=get_value(prefix + "group-id.source_group_id"),
        message_type=get_value(prefix + "type.message_type"),
        keyword=get_value(prefix + "keyword"),
        match_mode="regex" if use_regex else "exact" if exact else "contains",
        order="random" if order is None else order,
        count=count,
    )


def message_category(message: Msg) -> str:
    segments = message.children if message.type == "group" else [message]
    types = {segment.type for segment in segments}
    if len(types) != 1:
        return "mixed"
    only = next(iter(types))
    return "forward" if only in {"node", "forward"} else only


class DatabaseHandler:
    @staticmethod
    def _group_query(group_ids: Sequence[int]) -> tuple[str, tuple[int, ...]]:
        groups = tuple(dict.fromkeys(group_ids))
        if not groups:
            return "1 = 1", ()
        placeholders = ",".join("?" for _ in groups)
        return f"group_id IN ({placeholders})", groups

    @staticmethod
    def _query_where(query: EssenceQuery) -> tuple[str, tuple[int, ...]]:
        group_query, groups = DatabaseHandler._group_query(query.group_ids)
        clauses = [group_query]
        parameters: list[int] = list(groups)
        for column, operator, value in (
            ("time", ">=", query.from_time),
            ("time", "<=", query.to_time),
            ("sender_id", "=", query.sender_id),
            ("operator_id", "=", query.operator_id),
            ("sender_id", "!=", query.exclude_sender_id),
            ("operator_id", "!=", query.exclude_operator_id),
            ("group_id", "=", query.source_group_id),
        ):
            if value is not None:
                clauses.append(f"{column} {operator} ?")
                parameters.append(value)
        return " AND ".join(clauses), tuple(parameters)

    @staticmethod
    def _compile_pattern(query: EssenceQuery):
        if query.match_mode != "regex":
            return None
        assert query.keyword is not None
        if len(query.keyword) > MAX_REGEX_LENGTH:
            raise QueryValidationError(
                f"正则表达式不能超过 {MAX_REGEX_LENGTH} 个字符"
            )
        try:
            return regex.compile(query.keyword)
        except regex.error as error:
            raise QueryValidationError(f"正则表达式无效: {error}") from error

    @staticmethod
    def _row_matches(row: EssenceRow, query: EssenceQuery, pattern) -> bool:
        try:
            message = Msg.from_database(row[4], row[5])
        except (TypeError, UnicodeError, ValueError):
            return False
        category = message_category(message)
        if category == "video":
            return False
        if message.contains_only_truncated_images():
            return False
        if (
            query.message_type is not None
            and category != query.message_type
        ):
            return False
        if query.keyword is None:
            return True
        text = message.text_content()
        if not text:
            return False
        if query.match_mode == "contains":
            return query.keyword in text
        if query.match_mode == "exact":
            return query.keyword == text
        try:
            return (
                pattern.search(
                    text[:MAX_MATCH_TEXT_LENGTH],
                    timeout=REGEX_TIMEOUT_SECONDS,
                )
                is not None
            )
        except TimeoutError as error:
            raise QueryValidationError("正则表达式匹配超时, 请简化表达式") from error

    @classmethod
    def _stored_media_paths(cls, message: Msg) -> set[str]:
        paths: set[str] = set()
        if message.type in {"image", "record", "video"}:
            path = message.data.get("local_path", message.data.get("path"))
            if isinstance(path, str):
                paths.add(path)
        for child in message.children:
            paths.update(cls._stored_media_paths(child))
        return paths

    @classmethod
    def _build_group_export(
        cls,
        export_path: Path,
        export_db_path: Path,
        database_dir: Path,
        rows: list[tuple],
    ) -> None:
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer)
        writer.writerow(
            (
                "time",
                "group_id",
                "sender_id",
                "operator_id",
                "message_type",
                "text_content",
                "message_data",
                "content_hash",
            )
        )

        media_paths: set[str] = set()
        for row in rows:
            message_type, message_data = row[4], row[5]
            try:
                message = Msg.from_database(message_type, message_data)
                text_content = message.text_content()
                media_paths.update(cls._stored_media_paths(message))
            except (TypeError, UnicodeError, ValueError):
                text_content = ""
            writer.writerow((*row[:5], text_content, message_data, row[6]))

        database_root = database_dir.resolve()
        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(export_db_path, "essence.db")
            archive.writestr(
                "messages.csv",
                b"\xef\xbb\xbf" + csv_buffer.getvalue().encode("utf-8"),
            )
            for stored_path in sorted(media_paths):
                path = Path(stored_path)
                if path.is_absolute():
                    continue
                source = (database_dir / path).resolve()
                if source.is_file() and source.is_relative_to(database_root):
                    archive.write(
                        source,
                        path.as_posix(),
                        compress_type=zipfile.ZIP_STORED,
                    )

    @staticmethod
    def _content_hash(message_data: str) -> str:
        return hashlib.sha256(message_data.encode("utf-8")).hexdigest()

    def _backup_before_rebuild(self) -> Optional[Path]:
        database_path = Path(self.db_path)
        if not database_path.exists() or database_path.stat().st_size == 0:
            return None

        backup_dir = database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = backup_dir / f"{database_path.stem}-pre-rebuild-{timestamp}.db"
        with sqlite3.connect(database_path) as source:
            with sqlite3.connect(backup_path) as destination:
                source.backup(destination)
        return backup_path

    async def initialize(
        self, progress: Optional[Callable[[int, int], None]] = None
    ) -> tuple[int, int, Optional[Path]]:
        """Create and migrate the database without blocking the running event loop."""
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            cursor = await conn.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            if row is not None and row[0] >= DATABASE_SCHEMA_VERSION:
                return 0, 0, None

        backup_path = await asyncio.to_thread(self._backup_before_rebuild)
        migrated_image_count = 0
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS essence_data (
                    time INTEGER,
                    group_id INTEGER,
                    sender_id INTEGER,
                    operator_id INTEGER,
                    message_type TEXT,
                    message_data TEXT,
                    content_hash TEXT
                )"""
            )
            cursor = await conn.execute("PRAGMA table_info(essence_data)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "content_hash" not in columns:
                await conn.execute("ALTER TABLE essence_data ADD COLUMN content_hash TEXT")

            database_dir = Path(self.db_path).parent
            image_dir = database_dir / "img"
            cursor = await conn.execute("SELECT COUNT(*) FROM essence_data")
            total_rows = (await cursor.fetchone() or (0,))[0]
            if progress:
                progress(0, total_rows)
            migrated_row_count = 0
            completed = 0
            last_rowid = 0
            while True:
                cursor = await conn.execute(
                    """SELECT rowid, message_type,
                              CAST(message_data AS BLOB), content_hash
                       FROM essence_data
                       WHERE rowid > ?
                       ORDER BY rowid
                       LIMIT 100""",
                    (last_rowid,),
                )
                rows = await cursor.fetchall()
                if not rows:
                    break
                migrated_rows = []
                deleted_rowids = []
                for rowid, message_type, message_data, old_hash in rows:
                    last_rowid = rowid
                    try:
                        message = Msg.from_database(message_type, message_data)
                    except (TypeError, UnicodeError, ValueError):
                        logger.warning(
                            "精华消息格式无法迁移, 跳过记录: rowid={} type={}",
                            rowid,
                            message_type,
                        )
                        deleted_rowids.append((rowid,))
                        completed += 1
                        if progress:
                            progress(completed, total_rows)
                        continue
                    try:
                        migrated_images = message.migrate_images(
                            image_dir, database_dir
                        )
                        migrated_image_count += migrated_images
                        message.discard_stored_media_sources()
                        message.prune_empty_children()
                        message = message.normalize_root()
                        if not message.has_content():
                            logger.warning(
                                "精华消息没有可迁移内容, 跳过记录: rowid={} type={}",
                                rowid,
                                message_type,
                            )
                            deleted_rowids.append((rowid,))
                            continue
                        serialized = message.serialize()
                        content_hash = self._content_hash(serialized)
                        migrated_rows.append(
                            (message.type, serialized, content_hash, rowid)
                        )
                    except Exception:
                        logger.exception(
                            "精华消息迁移失败: rowid={} type={}",
                            rowid,
                            message_type,
                        )
                        raise
                    finally:
                        completed += 1
                        if progress:
                            progress(completed, total_rows)
                if migrated_rows:
                    await conn.executemany(
                        """UPDATE essence_data
                           SET message_type = ?, message_data = ?, content_hash = ?
                           WHERE rowid = ?""",
                        migrated_rows,
                    )
                    migrated_row_count += len(migrated_rows)
                if deleted_rowids:
                    await conn.executemany(
                        "DELETE FROM essence_data WHERE rowid = ?",
                        deleted_rowids,
                    )
            await conn.execute("DROP INDEX IF EXISTS idx_essence_exists")
            await conn.execute("DROP INDEX IF EXISTS idx_essence_identity")
            await conn.execute("DROP INDEX IF EXISTS idx_essence_time_identity")
            await conn.execute(
                """
                DELETE FROM essence_data
                WHERE rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM essence_data
                    GROUP BY group_id, sender_id, time, content_hash
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_essence_identity
                ON essence_data (group_id, sender_id, content_hash)
                """
            )
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_essence_time_identity
                ON essence_data (group_id, sender_id, time, content_hash)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_essence_group_time
                ON essence_data (group_id, time)
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_essence_group_operator
                ON essence_data (group_id, operator_id)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_mapping (
                    nickname TEXT NOT NULL,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    time INTEGER NOT NULL
                )
                """
            )
            # Older releases had no unique constraint. Deduplicate in place rather
            # than dropping and rebuilding the table on every plugin startup.
            await conn.execute(
                """
                DELETE FROM user_mapping
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid,
                               ROW_NUMBER() OVER (
                                   PARTITION BY nickname, group_id, user_id
                                   ORDER BY time DESC, rowid DESC
                               ) AS position
                        FROM user_mapping
                    )
                    WHERE position = 1
                )
                """
            )
            await conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_user_mapping_unique
                ON user_mapping (nickname, group_id, user_id)
                """
            )
            await conn.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")

            await conn.commit()
        return migrated_row_count, migrated_image_count, backup_path

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def insert_data(self, data):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """INSERT OR IGNORE INTO essence_data (
                       time, group_id, sender_id, operator_id,
                       message_type, message_data, content_hash
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["time"],
                    data["group_id"],
                    data["sender_id"],
                    data["operator_id"],
                    data["message_type"],
                    data["message_data"],
                    self._content_hash(data["message_data"]),
                ),
            )
            await conn.commit()
        return cursor.rowcount > 0

    async def delete_data(self, data):
        content_hash = self._content_hash(data["message_data"])
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT rowid
                   FROM essence_data
                   WHERE group_id = ?
                     AND sender_id = ?
                     AND content_hash = ?
                   ORDER BY ABS(time - ?) ASC, rowid ASC
                   LIMIT 1""",
                (
                    data["group_id"],
                    data["sender_id"],
                    content_hash,
                    data["time"],
                ),
            )
            row = await cursor.fetchone()

            if row:
                rowid = row[0]
                await conn.execute("DELETE FROM essence_data WHERE rowid = ?", (rowid,))
                await conn.commit()
                return True
            return False

    async def query_entries(self, query: EssenceQuery) -> list[EssenceRow]:
        where, parameters = self._query_where(query)
        ordering = {
            "random": "RANDOM()",
            "newest": "time DESC, rowid DESC",
            "oldest": "time ASC, rowid ASC",
        }[query.order]
        pattern = self._compile_pattern(query)
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT time, group_id, sender_id, operator_id,
                           message_type, message_data
                    FROM essence_data
                    WHERE {where}
                    ORDER BY {ordering}""",
                parameters,
            )
            results: list[EssenceRow] = []
            while len(results) < query.count:
                rows = await cursor.fetchmany(100)
                if not rows:
                    break
                for row in rows:
                    if self._row_matches(row, query, pattern):
                        results.append(row)
                        if len(results) == query.count:
                            break
            return results

    async def sender_rank(self, group_ids: Sequence[int], sender_id):
        group_query, groups = self._group_query(group_ids)
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT sender_id, COUNT(*) as count
                   FROM essence_data
                   WHERE {group_query}
                   GROUP BY sender_id""",
                groups,
            )
            all_sender_counts = await cursor.fetchall()

        if not all_sender_counts:
            return []
        sorted_sender_counts = sorted(
            all_sender_counts, key=lambda item: item[1], reverse=True
        )
        ranked_results = []
        prev_count = -1
        rank_value = 0
        sender_index_in_ranked = -1

        for i, (user_id, count) in enumerate(sorted_sender_counts):
            current_position = i + 1
            if count != prev_count:
                rank_value = current_position
            ranked_results.append((user_id, count, rank_value))
            prev_count = count
            if user_id == sender_id:
                sender_index_in_ranked = i

        users_to_include_ids = set()
        for user_id, count, rank in ranked_results:
            if rank <= 5:
                users_to_include_ids.add(user_id)

        users_to_include_ids.add(sender_id)
        if sender_index_in_ranked != -1:
            if sender_index_in_ranked > 0:
                users_to_include_ids.add(ranked_results[sender_index_in_ranked - 1][0])
            if sender_index_in_ranked < len(ranked_results) - 1:
                users_to_include_ids.add(ranked_results[sender_index_in_ranked + 1][0])

        final_results = []
        for user_id, count, rank in ranked_results:
            if user_id in users_to_include_ids:
                final_results.append((user_id, count, rank))

        final_results.sort(key=lambda item: item[2])
        return final_results

    async def operator_rank(self, group_ids: Sequence[int], sender_id):
        user_to_find_id = sender_id
        group_query, groups = self._group_query(group_ids)
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT operator_id, COUNT(*) as count
                   FROM essence_data
                   WHERE {group_query}
                   GROUP BY operator_id""",
                groups,
            )
            all_operator_counts = await cursor.fetchall()

        if not all_operator_counts:
            return []
        sorted_operator_counts = sorted(
            all_operator_counts, key=lambda item: item[1], reverse=True
        )
        ranked_results = []
        prev_count = -1
        rank_value = 0
        operator_index_in_ranked = -1
        for i, (user_id, count) in enumerate(sorted_operator_counts):
            current_position = i + 1

            if count != prev_count:
                rank_value = current_position

            ranked_results.append((user_id, count, rank_value))
            prev_count = count

            if user_id == user_to_find_id:
                operator_index_in_ranked = i
        users_to_include_ids = set()

        for user_id, count, rank in ranked_results:
            if rank <= 5:
                users_to_include_ids.add(user_id)

        users_to_include_ids.add(user_to_find_id)
        if operator_index_in_ranked != -1:
            if operator_index_in_ranked > 0:
                users_to_include_ids.add(
                    ranked_results[operator_index_in_ranked - 1][0]
                )
            if operator_index_in_ranked < len(ranked_results) - 1:
                users_to_include_ids.add(
                    ranked_results[operator_index_in_ranked + 1][0]
                )
        final_results = []
        for user_id, count, rank in ranked_results:
            if user_id in users_to_include_ids:
                final_results.append((user_id, count, rank))

        final_results.sort(key=lambda item: item[2])

        return final_results

    async def export_group_data(
        self, group_ids: Sequence[int], requested_group_id: int
    ) -> str:
        group_query, groups = self._group_query(group_ids)
        database_dir = Path(self.db_path).parent
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        export_db_path = database_dir / f"group_{requested_group_id}_{timestamp}.db"
        export_zip_path = database_dir / f"group_{requested_group_id}_{timestamp}.zip"

        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT time, group_id, sender_id, operator_id,
                          message_type, message_data, content_hash
                   FROM essence_data
                   WHERE {group_query}""",
                groups,
            )
            rows = await cursor.fetchall()
            cursor = await conn.execute(
                f"""SELECT nickname, group_id, user_id, time
                   FROM user_mapping
                   WHERE {group_query}""",
                groups,
            )
            user_mappings = await cursor.fetchall()

            async with aiosqlite.connect(str(export_db_path)) as export_conn:
                await export_conn.execute(
                    """CREATE TABLE essence_data (
                       time INTEGER,
                       group_id INTEGER,
                       sender_id INTEGER,
                       operator_id INTEGER,
                       message_type TEXT,
                       message_data TEXT,
                       content_hash TEXT
                    )"""
                )
                await export_conn.executemany(
                    """INSERT INTO essence_data
                       (time, group_id, sender_id, operator_id,
                        message_type, message_data, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                await export_conn.execute(
                    """CREATE TABLE user_mapping (
                       nickname TEXT NOT NULL,
                       group_id INTEGER NOT NULL,
                       user_id INTEGER NOT NULL,
                       time INTEGER NOT NULL
                    )"""
                )
                await export_conn.executemany(
                    """INSERT INTO user_mapping
                       (nickname, group_id, user_id, time)
                       VALUES (?, ?, ?, ?)""",
                    user_mappings,
                )
                await export_conn.execute(
                    """CREATE INDEX idx_essence_identity
                       ON essence_data (group_id, sender_id, content_hash)"""
                )
                await export_conn.execute(
                    """CREATE UNIQUE INDEX idx_essence_time_identity
                       ON essence_data
                       (group_id, sender_id, time, content_hash)"""
                )
                await export_conn.execute(
                    """CREATE UNIQUE INDEX idx_user_mapping_unique
                       ON user_mapping (nickname, group_id, user_id)"""
                )
                await export_conn.execute(
                    f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}"
                )
                await export_conn.commit()

        try:
            await asyncio.to_thread(
                self._build_group_export,
                export_zip_path,
                export_db_path,
                database_dir,
                rows,
            )
        finally:
            export_db_path.unlink(missing_ok=True)
        return str(export_zip_path)

    async def get_latest_nickname(self, group_id, user_id):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT nickname, time 
                   FROM user_mapping 
                   WHERE group_id = ? AND user_id = ? 
                   ORDER BY time DESC 
                   LIMIT 1""",
                (group_id, user_id),
            )
            result = await cursor.fetchone()
            return result

    async def insert_user_mapping(self, nickname, group_id, user_id, time):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute(
                """INSERT INTO user_mapping (nickname, group_id, user_id, time) 
                VALUES (?, ?, ?, ?)
                ON CONFLICT(nickname, group_id, user_id) 
                DO UPDATE SET time = excluded.time""",
                (nickname, group_id, user_id, time),
            )
            await conn.commit()
