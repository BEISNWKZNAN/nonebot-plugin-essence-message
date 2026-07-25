import hashlib
import random
import aiosqlite
import os
from datetime import datetime
import time


class DatabaseHandler:
    @staticmethod
    def _content_hash(message_data: str) -> str:
        return hashlib.sha256(message_data.encode("utf-8")).hexdigest()

    async def initialize(self) -> None:
        """Create and migrate the database without blocking the running event loop."""
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
                "SELECT rowid, message_data FROM essence_data WHERE content_hash IS NULL"
            )
            rows_without_hash = await cursor.fetchall()
            if rows_without_hash:
                await conn.executemany(
                    "UPDATE essence_data SET content_hash = ? WHERE rowid = ?",
                    [
                        (self._content_hash(message_data), rowid)
                        for rowid, message_data in rows_without_hash
                    ],
                )
            await conn.execute("DROP INDEX IF EXISTS idx_essence_exists")
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_essence_identity
                ON essence_data (
                    group_id, sender_id, operator_id, message_type, content_hash
                )
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

            await conn.commit()

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def insert_data(self, data):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute(
                """INSERT INTO essence_data (
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
        return True

    async def delete_data(self, data):
        content_hash = self._content_hash(data["message_data"])
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT rowid 
                       FROM essence_data 
                       WHERE group_id = ? 
                       AND sender_id = ? 
                       AND operator_id = ? 
                       AND message_type = ? 
                       AND content_hash = ?
                       AND message_data = ?
                       LIMIT 1""",
                (
                    data["group_id"],
                    data["sender_id"],
                    data["operator_id"],
                    data["message_type"],
                    content_hash,
                    data["message_data"],
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

    async def random_essence(self, group_id):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM essence_data WHERE group_id = ?", (group_id,)
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
                """SELECT time, group_id, sender_id, operator_id, message_type, message_data
                FROM essence_data
                WHERE group_id = ?
                LIMIT 1 OFFSET ?""",
                (group_id, random_offset),
            )
            return await cursor.fetchone()

    async def sender_rank(self, group_id, sender_id):
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT sender_id, COUNT(*) as count
                   FROM essence_data
                   WHERE group_id = ?
                   GROUP BY sender_id""",
                (group_id,),
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

    async def operator_rank(self, group_id, sender_id):
        user_to_find_id = sender_id
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT operator_id, COUNT(*) as count
                   FROM essence_data
                   WHERE group_id = ?
                   GROUP BY operator_id""",
                (group_id,),
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

    async def search_entries(self, group_id, keyword):
        keyword_escaped = keyword.replace("%", r"\%").replace("_", r"\_")
        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            cursor = await conn.execute(
                """SELECT time, group_id, sender_id, operator_id,
                          message_type, message_data
                FROM essence_data
                WHERE group_id = ? 
                AND message_type = 'text' 
                AND LENGTH(message_data) <= 100 
                AND message_data LIKE ? ESCAPE '\\' 
                ORDER BY RANDOM() 
                LIMIT 5""",
                (group_id, f"%{keyword_escaped}%"),
            )
            return await cursor.fetchall()

    async def export_group_data(self, group_id):
        export_db_path = os.path.join(
            os.path.dirname(self.db_path), f"group_{group_id}_{int(time.time())}.db"
        )

        async with aiosqlite.connect(self.db_path, timeout=5) as conn:
            await conn.execute("PRAGMA synchronous=NORMAL;")
            async with aiosqlite.connect(export_db_path) as export_conn:
                await export_conn.execute(
                    """CREATE TABLE IF NOT EXISTS essence_data (
                       time INTEGER,
                       group_id INTEGER,
                       sender_id INTEGER,
                       operator_id INTEGER,
                       message_type TEXT,
                       message_data TEXT,
                       content_hash TEXT
                    )"""
                )
                cursor = await conn.execute(
                    """SELECT time, group_id, sender_id, operator_id,
                              message_type, message_data, content_hash
                       FROM essence_data WHERE group_id = ?""",
                    (group_id,),
                )
                rows = await cursor.fetchall()
                await export_conn.executemany(
                    """INSERT INTO essence_data 
                       (time, group_id, sender_id, operator_id,
                        message_type, message_data, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                await export_conn.commit()
        return export_db_path

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
                GROUP BY
                    group_id, sender_id, operator_id,
                    message_type, content_hash, message_data
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
                   AND operator_id = ?
                   AND message_type = ? 
                   AND content_hash = ?
                   AND message_data = ?""",
                (
                    data["group_id"],
                    data["sender_id"],
                    data["operator_id"],
                    data["message_type"],
                    content_hash,
                    data["message_data"],
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
