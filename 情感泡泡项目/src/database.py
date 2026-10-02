"""
database.py - 本地数据库模块
使用 SQLite 存储所有数据（全部在本地）
"""
import sqlite3
import os
from datetime import datetime


class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            app_dir = os.path.join(os.path.expanduser("~"), ".EmotionBubble")
            os.makedirs(app_dir, exist_ok=True)
            db_path = os.path.join(app_dir, "emotion_bubble.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS bubbles (
                id TEXT PRIMARY KEY,
                author_nickname TEXT NOT NULL,
                author_node_id TEXT DEFAULT '',
                content TEXT NOT NULL,
                emotion_tag TEXT NOT NULL,
                reply_mode TEXT NOT NULL CHECK(reply_mode IN ('single', 'dialog')),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'resolved', 'saved_public', 'saved_private')),
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bubble_id TEXT NOT NULL,
                replier_nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (bubble_id) REFERENCES bubbles(id)
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bubble_id TEXT NOT NULL,
                sender_nickname TEXT NOT NULL,
                receiver_nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (bubble_id) REFERENCES bubbles(id)
            )
        """)
        self.conn.commit()
        self._migrate_bubbles_table()

    def _migrate_bubbles_table(self):
        self.cursor.execute("PRAGMA table_info(bubbles)")
        existing_columns = [row[1] for row in self.cursor.fetchall()]

        if 'author_node_id' not in existing_columns:
            self.cursor.execute(
                "ALTER TABLE bubbles ADD COLUMN author_node_id TEXT DEFAULT ''"
            )

        if 'reply_mode' not in existing_columns:
            self.cursor.execute(
                "ALTER TABLE bubbles ADD COLUMN reply_mode TEXT NOT NULL DEFAULT 'single'"
            )

        if 'status' not in existing_columns:
            self.cursor.execute(
                "ALTER TABLE bubbles ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )

        if 'resolved_at' not in existing_columns:
            self.cursor.execute(
                "ALTER TABLE bubbles ADD COLUMN resolved_at TEXT"
            )

        self.conn.commit()

    def save_bubble(self, bubble_id, author, content, emotion_tag, reply_mode, status='active', created_at=None, node_id=""):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            INSERT OR REPLACE INTO bubbles
            (id, author_nickname, author_node_id, content, emotion_tag, reply_mode, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (bubble_id, author, node_id, content, emotion_tag, reply_mode, status, created_at))
        self.conn.commit()

    def get_all_active_bubbles(self):
        self.cursor.execute("""
            SELECT * FROM bubbles WHERE status = 'active' OR status = 'saved_public'
            ORDER BY created_at DESC
        """)
        return self.cursor.fetchall()

    def get_bubble(self, bubble_id):
        self.cursor.execute("SELECT * FROM bubbles WHERE id = ?", (bubble_id,))
        return self.cursor.fetchone()

    def get_bubbles_by_emotion(self, emotion_tag):
        self.cursor.execute("""
            SELECT * FROM bubbles
            WHERE (status = 'active' OR status = 'saved_public') AND emotion_tag = ?
            ORDER BY created_at DESC
        """, (emotion_tag,))
        return self.cursor.fetchall()

    def resolve_bubble(self, bubble_id):
        now = datetime.now().isoformat()
        self.cursor.execute("""
            UPDATE bubbles SET status = 'resolved', resolved_at = ? WHERE id = ?
        """, (now, bubble_id))
        self.conn.commit()

    def save_bubble_as(self, bubble_id, is_public):
        status = 'saved_public' if is_public else 'saved_private'
        now = datetime.now().isoformat()
        self.cursor.execute("""
            UPDATE bubbles SET status = ?, resolved_at = ? WHERE id = ?
        """, (status, now, bubble_id))
        self.conn.commit()

    def save_reply(self, bubble_id, replier, content, created_at=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            SELECT 1 FROM replies WHERE bubble_id = ? AND replier_nickname = ? AND content = ? AND created_at = ?
        """, (bubble_id, replier, content, created_at))
        if self.cursor.fetchone():
            return
        self.cursor.execute("""
            INSERT INTO replies (bubble_id, replier_nickname, content, created_at)
            VALUES (?, ?, ?, ?)
        """, (bubble_id, replier, content, created_at))
        self.conn.commit()

    def get_replies(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM replies WHERE bubble_id = ? ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def save_chat_message(self, bubble_id, sender, receiver, content, created_at=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            SELECT 1 FROM chat_messages WHERE bubble_id = ? AND sender_nickname = ? AND receiver_nickname = ? AND content = ? AND created_at = ?
        """, (bubble_id, sender, receiver, content, created_at))
        if self.cursor.fetchone():
            return
        self.cursor.execute("""
            INSERT INTO chat_messages (bubble_id, sender_nickname, receiver_nickname, content, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (bubble_id, sender, receiver, content, created_at))
        self.conn.commit()

    def get_chat_messages(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM chat_messages WHERE bubble_id = ? ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def has_replied(self, bubble_id, nickname):
        self.cursor.execute("""
            SELECT COUNT(*) FROM replies WHERE bubble_id = ? AND replier_nickname = ?
        """, (bubble_id, nickname))
        count = self.cursor.fetchone()[0]
        self.cursor.execute("""
            SELECT COUNT(*) FROM chat_messages WHERE bubble_id = ? AND sender_nickname = ?
        """, (bubble_id, nickname))
        count += self.cursor.fetchone()[0]
        return count > 0

    def close(self):
        self.conn.close()
