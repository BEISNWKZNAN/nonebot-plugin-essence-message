import asyncio
import csv
import hashlib
import io
import random
import aiosqlite
import os
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from .msg import Msg


DATABASE_SCHEMA_VERSION = 3


class DatabaseHandler:
    @staticmethod
    def _group_query(group_ids: Sequence[int]) -> tuple[str, tuple[int, ...]]:
        groups = tuple(dict.fromkeys(group_ids))
        if not groups:
            return "1 = 1", ()
        placeholders = ",".join("?" for _ in groups)
        return f"group_id IN ({placeholders})", groups

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

            cursor = await conn.execute(
                """SELECT rowid, message_type,
                          CAST(message_data AS BLOB), content_hash
                   FROM essence_data"""
            )
            migrated_rows = []
            database_dir = Path(self.db_path).parent
            image_dir = database_dir / "img"
            rows = await cursor.fetchall()
            total_rows = len(rows)
            if progress:
                progress(0, total_rows)
            for position, (
                rowid,
                message_type,
                message_data,
                old_hash,
            ) in enumerate(rows, 1):
                message = Msg.from_database(message_type, message_data)
                migrated_images = message.migrate_images(image_dir, database_dir)
                migrated_image_count += migrated_images
                message.discard_stored_media_sources()
                serialized = message.serialize()
                content_hash = self._content_hash(serialized)
                # Startup reconstruction is intentional: normalize every row and
                # recalculate its hash after format or media-storage changes.
                migrated_rows.append((message.type, serialized, content_hash, rowid))
                if progress:
                    progress(position, total_rows)
            if migrated_rows:
                await conn.executemany(
                    """UPDATE essence_data
                       SET message_type = ?, message_data = ?, content_hash = ?
                       WHERE rowid = ?""",
                    migrated_rows,
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
        return len(migrated_rows), migrated_image_count, backup_path

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

    async def fetch_all(self):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute("SELECT * FROM essence_data")
            return await cursor.fetchall()

    async def summary_by_date(self, date, group_id):
        start_time = int(datetime.strptime(date, "%Y-%m-%d").timestamp())
        end_time = start_time + 86400  # Add one day in seconds

        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                "SELECT * FROM essence_data WHERE time BETWEEN ? AND ? AND group_id = ?",
                (start_time, end_time, group_id),
            )
            return await cursor.fetchall()

    async def random_essence(self, group_ids: Sequence[int]):
        group_query, groups = self._group_query(group_ids)
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT COUNT(*) FROM essence_data
                    WHERE {group_query}""",
                groups,
            )
            re = await cursor.fetchone()
            if re == None:
                return None
            else:
                count = re[0]

            if count == 0:
                return None
            random_offset = random.randint(0, count - 1)
            cursor = await conn.execute(
                f"""SELECT time, group_id, sender_id, operator_id, message_type, message_data
                FROM essence_data
                WHERE {group_query}
                LIMIT 1 OFFSET ?""",
                (*groups, random_offset),
            )
            return await cursor.fetchone()

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

    async def search_entries(self, group_ids: Sequence[int], keyword):
        group_query, groups = self._group_query(group_ids)
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                f"""SELECT time, group_id, sender_id, operator_id,
                          message_type, message_data
                FROM essence_data
                WHERE {group_query}""",
                groups,
            )
            matched = []
            for row in await cursor.fetchall():
                try:
                    text = Msg.deserialize(row[5]).text_content()
                except (TypeError, ValueError):
                    continue
                if keyword in text:
                    matched.append(row)
            return random.sample(matched, min(5, len(matched)))

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

    async def clean_duplicate_entries(self):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute(
                """
                CREATE TEMPORARY TABLE IF NOT EXISTS min_rowids AS
                SELECT MIN(rowid) as min_rowid
                FROM essence_data
                GROUP BY group_id, sender_id, time, content_hash
            """
            )
            cursor = await conn.execute(
                """
                DELETE FROM essence_data
                WHERE rowid NOT IN (SELECT min_rowid FROM min_rowids)
            """
            )
            deleted_count = cursor.rowcount
            await conn.commit()
            await conn.execute("DROP TABLE IF EXISTS min_rowids")
            return deleted_count

    async def entry_exists(self, data):
        content_hash = self._content_hash(data["message_data"])
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT COUNT(*)
                   FROM essence_data
                   WHERE group_id = ?
                     AND sender_id = ?
                     AND time = ?
                     AND content_hash = ?""",
                (
                    data["group_id"],
                    data["sender_id"],
                    data["time"],
                    content_hash,
                ),
            )
            one = await cursor.fetchone()
            return one != None and one[0] != 0

    async def migrate_group_data(
        self, old_group_id: int, new_group_id: int
    ) -> tuple[int, int]:

        essence_updated_count = 0
        user_mapping_updated_count = 0

        try:
            async with aiosqlite.connect(self.db_path, timeout=10) as conn:
                await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.execute("PRAGMA synchronous=NORMAL;")
                await conn.execute(
                    """
                    DELETE FROM essence_data AS source
                    WHERE source.group_id = ?
                      AND EXISTS (
                          SELECT 1
                          FROM essence_data AS destination
                          WHERE destination.group_id = ?
                            AND destination.sender_id = source.sender_id
                            AND destination.time = source.time
                            AND destination.content_hash = source.content_hash
                      )
                    """,
                    (old_group_id, new_group_id),
                )
                cursor_essence = await conn.execute(
                    """UPDATE essence_data
                    SET group_id = ?
                    WHERE group_id = ?""",
                    (new_group_id, old_group_id),
                )
                essence_updated_count = cursor_essence.rowcount
                cursor = await conn.execute(
                    """SELECT nickname, group_id, user_id, time
                    FROM user_mapping
                    WHERE group_id = ?""",
                    (old_group_id,),
                )
                old_group_mappings = await cursor.fetchall()

                if not old_group_mappings:
                    print(f"No user mapping data found for group {old_group_id}")
                    user_mapping_updated_count = 0
                else:
                    migrating_data = [
                        (
                            nickname,
                            new_group_id,
                            user_id,
                            time_val,
                        )
                        for nickname, group_id, user_id, time_val in old_group_mappings
                    ]

                    cursor = await conn.execute(
                        """SELECT nickname, group_id, user_id, time
                        FROM user_mapping
                        WHERE group_id = ?""",
                        (new_group_id,),
                    )
                    existing_new_group_mappings = await cursor.fetchall()
                    existing_mapping_dict = {
                        (nickname, user_id): time_val
                        for nickname, group_id, user_id, time_val in existing_new_group_mappings
                    }
                    records_to_insert = []
                    records_to_update = []

                    for nickname, group_id, user_id, time_val in migrating_data:
                        key = (nickname, user_id)

                        if key in existing_mapping_dict:
                            existing_time = existing_mapping_dict[key]
                            if time_val > existing_time:
                                records_to_update.append(
                                    (nickname, new_group_id, user_id, time_val)
                                )
                        else:
                            records_to_insert.append(
                                (nickname, new_group_id, user_id, time_val)
                            )

                    cursor_delete = await conn.execute(
                        """DELETE FROM user_mapping WHERE group_id = ?""",
                        (old_group_id,),
                    )

                    if records_to_insert:
                        await conn.executemany(
                            """INSERT INTO user_mapping (nickname, group_id, user_id, time)
                            VALUES (?, ?, ?, ?)""",
                            records_to_insert,
                        )

                    if records_to_update:
                        await conn.executemany(
                            """INSERT OR REPLACE INTO user_mapping (nickname, group_id, user_id, time)
                            VALUES (?, ?, ?, ?)""",
                            records_to_update,
                        )

                    user_mapping_updated_count = len(records_to_insert) + len(
                        records_to_update
                    )
                await conn.commit()

        except aiosqlite.Error as e:
            print(f"Database error during migration: {e}")
            raise e

        return essence_updated_count, user_mapping_updated_count
