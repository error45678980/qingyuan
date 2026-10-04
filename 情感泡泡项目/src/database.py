"""
database.py - 本地数据库模块
使用 SQLite 存储所有数据（全部在本地）
"""
import sqlite3
import os
import uuid
from datetime import datetime, timezone


def _now():
    """统一用 UTC 时间戳，毫秒精度（浏览器 new Date() 能直接解析）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, db_path=None, init_schema=True):
        if db_path is None:
            app_dir = os.path.join(os.path.expanduser("~"), ".EmotionBubble")
            os.makedirs(app_dir, exist_ok=True)
            db_path = os.path.join(app_dir, "emotion_bubble.db")
        self.db_path = db_path
        # 服务器端：每个请求都会新建一个连接（存到 Flask 的 g 里），
        # 因此并发时各请求互不干扰。check_same_thread=False 仅为兼容旧桌面端。
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        # WAL 让读写可以并发（默认的 rollback journal 会让写阻塞所有读）；
        # busy_timeout 让并发写在锁冲突时等待而不是直接抛 "database is locked"。
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA busy_timeout=5000")
        self.cursor.execute("PRAGMA foreign_keys=ON")
        if init_schema:
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
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                bubble_id TEXT NOT NULL,
                author_nickname TEXT NOT NULL,
                helper_nickname TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(bubble_id, helper_nickname)
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

        if 'author_id' not in existing_columns:
            self.cursor.execute(
                "ALTER TABLE bubbles ADD COLUMN author_id INTEGER"
            )

        self.cursor.execute("PRAGMA table_info(chat_messages)")
        chat_columns = [row[1] for row in self.cursor.fetchall()]
        if 'conversation_id' not in chat_columns:
            self.cursor.execute(
                "ALTER TABLE chat_messages ADD COLUMN conversation_id TEXT"
            )

        self.conn.commit()

    def save_bubble(self, bubble_id, author, content, emotion_tag, reply_mode, status='active', created_at=None, node_id="", author_id=None):
        if created_at is None:
            created_at = _now()
        self.cursor.execute("""
            INSERT OR REPLACE INTO bubbles
            (id, author_nickname, author_node_id, content, emotion_tag, reply_mode, status, created_at, author_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (bubble_id, author, node_id, content, emotion_tag, reply_mode, status, created_at, author_id))
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
        now = _now()
        self.cursor.execute("""
            UPDATE bubbles SET status = 'resolved', resolved_at = ? WHERE id = ?
        """, (now, bubble_id))
        self.conn.commit()

    def save_bubble_as(self, bubble_id, is_public):
        status = 'saved_public' if is_public else 'saved_private'
        now = _now()
        self.cursor.execute("""
            UPDATE bubbles SET status = ?, resolved_at = ? WHERE id = ?
        """, (status, now, bubble_id))
        self.conn.commit()

    def save_reply(self, bubble_id, replier, content, created_at=None):
        if created_at is None:
            created_at = _now()
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

    def save_chat_message(self, bubble_id, sender, receiver, content, created_at=None, conversation_id=None):
        if created_at is None:
            created_at = _now()
        self.cursor.execute("""
            SELECT 1 FROM chat_messages
            WHERE bubble_id = ? AND sender_nickname = ? AND receiver_nickname = ?
              AND content = ? AND created_at = ? AND COALESCE(conversation_id, '') = COALESCE(?, '')
        """, (bubble_id, sender, receiver, content, created_at, conversation_id))
        if self.cursor.fetchone():
            return
        self.cursor.execute("""
            INSERT INTO chat_messages (bubble_id, sender_nickname, receiver_nickname, content, created_at, conversation_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (bubble_id, sender, receiver, content, created_at, conversation_id))
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

    # ==================== 用户 ====================
    def create_user(self, username, password_hash):
        created_at = _now()
        self.cursor.execute("""
            INSERT INTO users (username, password_hash, created_at)
            VALUES (?, ?, ?)
        """, (username, password_hash, created_at))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_user_by_username(self, username):
        self.cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        return self.cursor.fetchone()

    def get_user_by_id(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return self.cursor.fetchone()

    # ==================== 会话通道 ====================
    def get_conversation(self, conversation_id):
        self.cursor.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return self.cursor.fetchone()

    def get_conversation_by_participants(self, bubble_id, helper_nickname):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = ? AND helper_nickname = ?
        """, (bubble_id, helper_nickname))
        return self.cursor.fetchone()

    def list_conversations_for_bubble(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = ? ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def get_or_create_conversation(self, bubble_id, author_nickname, helper_nickname):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = ? AND helper_nickname = ?
        """, (bubble_id, helper_nickname))
        row = self.cursor.fetchone()
        if row:
            return row
        conv_id = str(uuid.uuid4())
        created_at = _now()
        self.cursor.execute("""
            INSERT INTO conversations (id, bubble_id, author_nickname, helper_nickname, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (conv_id, bubble_id, author_nickname, helper_nickname, created_at))
        self.conn.commit()
        return self.get_conversation(conv_id)

    def list_conversations_for_user(self, nickname):
        self.cursor.execute("""
            SELECT c.*, b.content AS bubble_content, b.emotion_tag AS bubble_emotion
            FROM conversations c
            JOIN bubbles b ON b.id = c.bubble_id
            WHERE (c.author_nickname = ? OR c.helper_nickname = ?)
              AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.conversation_id = c.id)
            ORDER BY c.created_at DESC
        """, (nickname, nickname))
        return self.cursor.fetchall()

    def get_messages_for_conversation(self, conversation_id):
        self.cursor.execute("""
            SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at ASC
        """, (conversation_id,))
        return self.cursor.fetchall()

    def close(self):
        self.conn.close()
