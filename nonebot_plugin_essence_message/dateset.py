import sqlite3
import os
from contextlib import closing
from datetime import datetime
import time


class DatabaseHandler:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._create_table()

    def _create_table(self):
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS essence_data (
                time INTEGER,
                group_id INTEGER,
                sender_id INTEGER,
                operator_id INTEGER,
                message_type TEXT,
                message_data TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_mapping (
                    nickname TEXT,
                    group_id INTEGER,
                    user_id INTEGER,
                    time INTEGER
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS del_essence_data (
                time INTEGER,
                group_id INTEGER,
                sender_id INTEGER,
                operator_id INTEGER,
                message_type TEXT,
                message_data TEXT
                )"""
            )

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def insert_data(self, data):
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO essence_data (time, group_id, sender_id, operator_id, message_type, message_data) 
                            VALUES (?, ?, ?, ?, ?, ?)""",
                data,
            )

    def insert_del_data(self, data):
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO del_essence_data (time, group_id, sender_id, operator_id, message_type, message_data) 
                            VALUES (?, ?, ?, ?, ?, ?)""",
                data,
            )

    def fetch_all(self):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("SELECT * FROM essence_data")
            return cursor.fetchall()

    def summary_by_date(self, date, group_id):
        with closing(self._connect()) as conn, conn:
            start_time = int(datetime.strptime(date, "%Y-%m-%d").timestamp())
            end_time = start_time + 86400  # Add one day in seconds
            cursor = conn.execute(
                "SELECT * FROM essence_data WHERE time BETWEEN ? AND ? AND group_id = ?",
                (start_time, end_time, group_id),
            )
            return cursor.fetchall()

    def random_essence(self, group_id):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "SELECT * FROM essence_data WHERE group_id = ? ORDER BY RANDOM() LIMIT 1",
                (group_id,),
            )
            return cursor.fetchone()

    def sender_rank(self, group_id):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """SELECT sender_id, COUNT(*) as count 
                                     FROM essence_data 
                                     WHERE group_id = ? 
                                     GROUP BY sender_id 
                                     ORDER BY count DESC
                                     LIMIT 7""",
                (group_id,),
            )
            return cursor.fetchall()

    def operator_rank(self, group_id):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """SELECT operator_id, COUNT(*) as count 
                                     FROM essence_data 
                                     WHERE group_id = ? 
                                     GROUP BY operator_id 
                                     ORDER BY count DESC
                                     LIMIT 7""",
                (group_id,),
            )
            return cursor.fetchall()

    def delete_data_by_group(self, group_id):
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM essence_data WHERE group_id = ?", (group_id,))
            conn.commit()

    def search_entries(self, group_id, keyword):
        with closing(self._connect()) as conn, conn:
            keyword_escaped = keyword.replace("%", "\%").replace("_", "\_")
            cursor = conn.execute(
                """SELECT * FROM essence_data 
                   WHERE group_id = ? 
                   AND message_type = 'text' 
                   AND LENGTH(message_data) <= 30
                   AND message_data LIKE ? ESCAPE '\\' 
                   ORDER BY RANDOM()
                   LIMIT 5""",
                (group_id, f"%{keyword_escaped}%"),
            )
            return cursor.fetchall()

    def export_group_data(self, group_id):
        export_db_path = os.path.join(
            os.path.dirname(self.db_path), f"group_{group_id}_{int(time.time())}.db"
        )
        with closing(self._connect()) as conn, conn:
            export_conn = sqlite3.connect(export_db_path)
            with closing(export_conn) as export_conn, export_conn:
                export_conn.execute(
                    """CREATE TABLE IF NOT EXISTS essence_data (
                    time INTEGER,
                    group_id INTEGER,
                    sender_id INTEGER,
                    operator_id INTEGER,
                    message_type TEXT,
                    message_data TEXT
                )"""
                )
                cursor = conn.execute(
                    "SELECT * FROM essence_data WHERE group_id = ?", (group_id,)
                )
                rows = cursor.fetchall()
                export_conn.executemany(
                    """INSERT INTO essence_data 
                                           (time, group_id, sender_id, operator_id, message_type, message_data) 
                                           VALUES (?, ?, ?, ?, ?, ?)""",
                    rows,
                )
        return export_db_path

    def get_latest_nickname(self, group_id, user_id):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """SELECT nickname, time 
                   FROM user_mapping 
                   WHERE group_id = ? AND user_id = ? 
                   ORDER BY time DESC 
                   LIMIT 1""",
                (group_id, user_id),
            )
            return cursor.fetchone()

    def insert_user_mapping(self, nickname, group_id, user_id, time):
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO user_mapping (nickname, group_id, user_id, time) 
                   VALUES (?, ?, ?, ?)""",
                (nickname, group_id, user_id, time),
            )
            conn.commit()

    def delete_matching_entry(self, group_id):
        with closing(self._connect()) as conn, conn:
            # Fetch the latest entry from del_essence_data for the specified group
            cursor = conn.execute(
                """SELECT time, group_id, sender_id, operator_id, message_type, message_data 
                FROM del_essence_data 
                WHERE group_id = ?
                ORDER BY time DESC 
                LIMIT 1""",
                (group_id,),
            )
            latest_del_entry = cursor.fetchone()
            if not latest_del_entry:
                return None

            del_time, _, sender_id, operator_id, message_type, message_data = (
                latest_del_entry
            )

            message_data = message_data[:50]

            # Find a matching entry in essence_data within the specified group
            cursor = conn.execute(
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
            matching_entry = cursor.fetchone()
            if matching_entry:
                conn.execute(
                    """DELETE FROM essence_data 
                    WHERE time = ? 
                    AND group_id = ? 
                    AND sender_id = ? 
                    AND operator_id = ? 
                    AND message_type = ? 
                    AND message_data = ?""",
                    matching_entry,
                )
                conn.execute(
                    """DELETE FROM del_essence_data 
                    WHERE time = ? 
                    AND group_id = ? 
                    AND sender_id = ? 
                    AND operator_id = ? 
                    AND message_type = ? 
                    AND message_data = ?""",
                    matching_entry,
                )
                conn.commit()
            return matching_entry


    def check_entry_exists(self, data):
        operator_time, group_id, sender_id, operator_id, message_type, message_data = data

        with closing(self._connect()) as conn:
            cursor = conn.execute(
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
                    message_data,
                    operator_time - 1000,
                    operator_time + 1000,
                ),
            )
            return cursor.fetchone()[0] > 0
