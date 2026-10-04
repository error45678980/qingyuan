"""
database_pg.py - PostgreSQL 版本的数据库模块
跟 database.py 接口完全一致，只是底层换成 psycopg2 连 Neon。
"""
import psycopg2
import psycopg2.extras
import uuid
from datetime import datetime


class Database:
    def __init__(self, db_url, init_schema=True):
        self.db_url = db_url
        self.conn = psycopg2.connect(db_url)
        self.cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
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
                reply_mode TEXT NOT NULL DEFAULT 'single'
                    CHECK(reply_mode IN ('single', 'dialog')),
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active', 'resolved', 'saved_public', 'saved_private')),
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                author_id INTEGER
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                id SERIAL PRIMARY KEY,
                bubble_id TEXT NOT NULL REFERENCES bubbles(id),
                replier_nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY,
                bubble_id TEXT NOT NULL REFERENCES bubbles(id),
                conversation_id TEXT,
                sender_nickname TEXT NOT NULL,
                receiver_nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
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

    def save_bubble(self, bubble_id, author, content, emotion_tag, reply_mode, status='active', created_at=None, node_id="", author_id=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            INSERT INTO bubbles
            (id, author_nickname, author_node_id, content, emotion_tag, reply_mode, status, created_at, author_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                author_nickname = EXCLUDED.author_nickname,
                author_node_id = EXCLUDED.author_node_id,
                content = EXCLUDED.content,
                emotion_tag = EXCLUDED.emotion_tag,
                reply_mode = EXCLUDED.reply_mode,
                status = EXCLUDED.status,
                created_at = EXCLUDED.created_at,
                author_id = EXCLUDED.author_id
        """, (bubble_id, author, node_id, content, emotion_tag, reply_mode, status, created_at, author_id))
        self.conn.commit()

    def get_all_active_bubbles(self):
        self.cursor.execute("""
            SELECT * FROM bubbles WHERE status = 'active' OR status = 'saved_public'
            ORDER BY created_at DESC
        """)
        return self.cursor.fetchall()

    def get_bubble(self, bubble_id):
        self.cursor.execute("SELECT * FROM bubbles WHERE id = %s", (bubble_id,))
        return self.cursor.fetchone()

    def get_bubbles_by_emotion(self, emotion_tag):
        self.cursor.execute("""
            SELECT * FROM bubbles
            WHERE (status = 'active' OR status = 'saved_public') AND emotion_tag = %s
            ORDER BY created_at DESC
        """, (emotion_tag,))
        return self.cursor.fetchall()

    def resolve_bubble(self, bubble_id):
        now = datetime.now().isoformat()
        self.cursor.execute("""
            UPDATE bubbles SET status = 'resolved', resolved_at = %s WHERE id = %s
        """, (now, bubble_id))
        self.conn.commit()

    def save_bubble_as(self, bubble_id, is_public):
        status = 'saved_public' if is_public else 'saved_private'
        now = datetime.now().isoformat()
        self.cursor.execute("""
            UPDATE bubbles SET status = %s, resolved_at = %s WHERE id = %s
        """, (status, now, bubble_id))
        self.conn.commit()

    def save_reply(self, bubble_id, replier, content, created_at=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            SELECT 1 FROM replies WHERE bubble_id = %s AND replier_nickname = %s AND content = %s AND created_at = %s
        """, (bubble_id, replier, content, created_at))
        if self.cursor.fetchone():
            return
        self.cursor.execute("""
            INSERT INTO replies (bubble_id, replier_nickname, content, created_at)
            VALUES (%s, %s, %s, %s)
        """, (bubble_id, replier, content, created_at))
        self.conn.commit()

    def get_replies(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM replies WHERE bubble_id = %s ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def save_chat_message(self, bubble_id, sender, receiver, content, created_at=None, conversation_id=None):
        if created_at is None:
            created_at = datetime.now().isoformat()
        self.cursor.execute("""
            SELECT 1 FROM chat_messages
            WHERE bubble_id = %s AND sender_nickname = %s AND receiver_nickname = %s
              AND content = %s AND created_at = %s AND COALESCE(conversation_id, '') = COALESCE(%s, '')
        """, (bubble_id, sender, receiver, content, created_at, conversation_id))
        if self.cursor.fetchone():
            return
        self.cursor.execute("""
            INSERT INTO chat_messages (bubble_id, sender_nickname, receiver_nickname, content, created_at, conversation_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (bubble_id, sender, receiver, content, created_at, conversation_id))
        self.conn.commit()

    def get_chat_messages(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM chat_messages WHERE bubble_id = %s ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def has_replied(self, bubble_id, nickname):
        self.cursor.execute("""
            SELECT COUNT(*) AS c FROM replies WHERE bubble_id = %s AND replier_nickname = %s
        """, (bubble_id, nickname))
        count = self.cursor.fetchone()["c"]
        self.cursor.execute("""
            SELECT COUNT(*) AS c FROM chat_messages WHERE bubble_id = %s AND sender_nickname = %s
        """, (bubble_id, nickname))
        count += self.cursor.fetchone()["c"]
        return count > 0

    # ==================== 用户 ====================
    def create_user(self, username, password_hash):
        created_at = datetime.now().isoformat()
        self.cursor.execute("""
            INSERT INTO users (username, password_hash, created_at)
            VALUES (%s, %s, %s) RETURNING id
        """, (username, password_hash, created_at))
        new_id = self.cursor.fetchone()["id"]
        self.conn.commit()
        return new_id

    def get_user_by_username(self, username):
        self.cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        return self.cursor.fetchone()

    def get_user_by_id(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        return self.cursor.fetchone()

    # ==================== 会话通道 ====================
    def get_conversation(self, conversation_id):
        self.cursor.execute("SELECT * FROM conversations WHERE id = %s", (conversation_id,))
        return self.cursor.fetchone()

    def get_conversation_by_participants(self, bubble_id, helper_nickname):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = %s AND helper_nickname = %s
        """, (bubble_id, helper_nickname))
        return self.cursor.fetchone()

    def list_conversations_for_bubble(self, bubble_id):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = %s ORDER BY created_at ASC
        """, (bubble_id,))
        return self.cursor.fetchall()

    def get_or_create_conversation(self, bubble_id, author_nickname, helper_nickname):
        self.cursor.execute("""
            SELECT * FROM conversations WHERE bubble_id = %s AND helper_nickname = %s
        """, (bubble_id, helper_nickname))
        row = self.cursor.fetchone()
        if row:
            return row
        conv_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        self.cursor.execute("""
            INSERT INTO conversations (id, bubble_id, author_nickname, helper_nickname, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (conv_id, bubble_id, author_nickname, helper_nickname, created_at))
        self.conn.commit()
        return self.get_conversation(conv_id)

    def list_conversations_for_user(self, nickname):
        self.cursor.execute("""
            SELECT c.*, b.content AS bubble_content, b.emotion_tag AS bubble_emotion
            FROM conversations c
            JOIN bubbles b ON b.id = c.bubble_id
            WHERE (c.author_nickname = %s OR c.helper_nickname = %s)
              AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.conversation_id = c.id)
            ORDER BY c.created_at DESC
        """, (nickname, nickname))
        return self.cursor.fetchall()

    def get_messages_for_conversation(self, conversation_id):
        self.cursor.execute("""
            SELECT * FROM chat_messages WHERE conversation_id = %s ORDER BY created_at ASC
        """, (conversation_id,))
        return self.cursor.fetchall()

    def close(self):
        self.conn.close()
