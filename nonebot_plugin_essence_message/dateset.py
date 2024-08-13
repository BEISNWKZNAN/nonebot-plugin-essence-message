import aiosqlite
import os
from datetime import datetime
import time


class DatabaseHandler:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _create_table(self):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS essence_data (
                time INTEGER,
                group_id INTEGER,
                sender_id INTEGER,
                operator_id INTEGER,
                message_type TEXT,
                message_data TEXT
                )"""
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS user_mapping (
                    nickname TEXT,
                    group_id INTEGER,
                    user_id INTEGER,
                    time INTEGER
                )"""
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS del_essence_data (
                time INTEGER,
                group_id INTEGER,
                sender_id INTEGER,
                operator_id INTEGER,
                message_type TEXT,
                message_data TEXT
                )"""
            )
            await conn.commit()

    async def insert_data(self, data):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO essence_data (time, group_id, sender_id, operator_id, message_type, message_data) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                data,
            )
            await conn.commit()

    async def insert_del_data(self, data):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO del_essence_data (time, group_id, sender_id, operator_id, message_type, message_data) 
                   VALUES (?, ?, ?, ?, ?, ?)""",
                data,
            )
            await conn.commit()

    async def fetch_all(self):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("SELECT * FROM essence_data")
            return await cursor.fetchall()

    async def summary_by_date(self, date, group_id):
        start_time = int(datetime.strptime(date, "%Y-%m-%d").timestamp())
        end_time = start_time + 86400  # Add one day in seconds

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT * FROM essence_data WHERE time BETWEEN ? AND ? AND group_id = ?",
                (start_time, end_time, group_id),
            )
            return await cursor.fetchall()

    async def random_essence(self, group_id):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT * FROM essence_data 
                   WHERE group_id = ? 
                   AND (message_type = 'text' OR message_type = 'image') 
                   ORDER BY RANDOM() LIMIT 1""",
                (group_id,),
            )
            return await cursor.fetchone()

    async def sender_rank(self, group_id):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT sender_id, COUNT(*) as count 
                   FROM essence_data 
                   WHERE group_id = ? 
                   GROUP BY sender_id 
                   ORDER BY count DESC 
                   LIMIT 7""",
                (group_id,),
            )
            return await cursor.fetchall()

    async def operator_rank(self, group_id):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT operator_id, COUNT(*) as count 
                   FROM essence_data 
                   WHERE group_id = ? 
                   GROUP BY operator_id 
                   ORDER BY count DESC 
                   LIMIT 7""",
                (group_id,),
            )
            return await cursor.fetchall()

    async def delete_data_by_group(self, group_id):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "DELETE FROM essence_data WHERE group_id = ?", (group_id,)
            )
            await conn.commit()

    async def search_entries(self, group_id, keyword):
        keyword_escaped = keyword.replace("%", "\%").replace("_", "\_")

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT * FROM essence_data 
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

        async with aiosqlite.connect(self.db_path) as conn:
            async with aiosqlite.connect(export_db_path) as export_conn:
                await export_conn.execute(
                    """CREATE TABLE IF NOT EXISTS essence_data (
                       time INTEGER,
                       group_id INTEGER,
                       sender_id INTEGER,
                       operator_id INTEGER,
                       message_type TEXT,
                       message_data TEXT
                    )"""
                )
                cursor = await conn.execute(
                    "SELECT * FROM essence_data WHERE group_id = ?", (group_id,)
                )
                rows = await cursor.fetchall()
                await export_conn.executemany(
                    """INSERT INTO essence_data 
                       (time, group_id, sender_id, operator_id, message_type, message_data) 
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                await export_conn.commit()
        return export_db_path

    async def get_latest_nickname(self, group_id, user_id):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT nickname, time 
                   FROM user_mapping 
                   WHERE group_id = ? AND user_id = ? 
                   ORDER BY time DESC 
                   LIMIT 1""",
                (group_id, user_id),
            )
            result = await cursor.fetchone()
            if result:
                nickname, _ = result
                current_time = int(time.time())
                await conn.execute(
                    """UPDATE user_mapping 
                       SET time = ? 
                       WHERE group_id = ? AND user_id = ? AND nickname = ?""",
                    (current_time - 100, group_id, user_id, nickname),
                )
                await conn.commit()
            return result

    async def insert_user_mapping(self, nickname, group_id, user_id, time):
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """INSERT INTO user_mapping (nickname, group_id, user_id, time) 
                   VALUES (?, ?, ?, ?)""",
                (nickname, group_id, user_id, time),
            )
            await conn.commit()

    async def delete_matching_entry(self, group_id):
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT time, group_id, sender_id, operator_id, message_type, message_data 
                   FROM del_essence_data 
                   WHERE group_id = ? 
                   ORDER BY time DESC 
                   LIMIT 1""",
                (group_id,),
            )
            latest_del_entry = await cursor.fetchone()
            if not latest_del_entry:
                return None

            del_time, _, sender_id, operator_id, message_type, message_data = (
                latest_del_entry
            )
            message_data = message_data[:50]

            cursor = await conn.execute(
                """SELECT * 
                   FROM essence_data 
                   WHERE group_id = ? 
                   AND sender_id = ? 
                   AND operator_id = ? 
                   AND message_type = ? 
                   AND message_data LIKE ? 
                   ORDER BY ABS(time - ?) 
                   LIMIT 1""",
                (
                    group_id,
                    sender_id,
                    operator_id,
                    message_type,
                    f"{message_data}%",
                    del_time,
                ),
            )
            matching_entry = await cursor.fetchone()
            if matching_entry:
                await conn.execute(
                    """DELETE FROM essence_data 
                       WHERE time = ? 
                       AND group_id = ? 
                       AND sender_id = ? 
                       AND operator_id = ? 
                       AND message_type = ? 
                       AND message_data = ?""",
                    matching_entry,
                )
                await conn.execute(
                    """DELETE FROM del_essence_data 
                       WHERE time = ? 
                       AND group_id = ? 
                       AND sender_id = ? 
                       AND operator_id = ? 
                       AND message_type = ? 
                       AND message_data = ?""",
                    matching_entry,
                )
                await conn.commit()
            return matching_entry

    async def check_entry_exists(self, data):
        operator_time, group_id, sender_id, operator_id, message_type, message_data = (
            data
        )

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                """SELECT COUNT(*) 
                   FROM essence_data 
                   WHERE group_id = ? 
                   AND sender_id = ? 
                   AND operator_id = ? 
                   AND message_type = ? 
                   AND message_data LIKE ? 
                   AND time BETWEEN ? AND ?""",
                (
                    group_id,
                    sender_id,
                    operator_id,
                    message_type,
                    message_data[:50],
                    operator_time - 1000,
                    operator_time + 1000,
                ),
            )
            return (await cursor.fetchone())[0] > 0
