"""
server_app.py - 中央服务器应用
提供全球泡泡存储与交流的 REST API。
"""

import os
from flask import Flask, request, jsonify
from .database import Database


def _get_server_db_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    server_dir = os.path.join(base_dir, "..", "server_data")
    os.makedirs(server_dir, exist_ok=True)
    return os.path.join(server_dir, "emotion_bubble_server.db")


app = Flask(__name__)
server_db = Database(db_path=_get_server_db_path())


def _serialize_bubble(row):
    if not row:
        return None
    return {
        "id": row[0],
        "author": row[1],
        "author_node_id": row[2],
        "content": row[3],
        "emotion_tag": row[4],
        "reply_mode": row[5],
        "status": row[6],
        "created_at": row[7],
        "resolved_at": row[8],
    }


def _serialize_reply(row):
    return {
        "id": row[0],
        "bubble_id": row[1],
        "replier": row[2],
        "content": row[3],
        "created_at": row[4],
    }


def _serialize_chat(row):
    return {
        "id": row[0],
        "bubble_id": row[1],
        "sender": row[2],
        "receiver": row[3],
        "content": row[4],
        "created_at": row[5],
    }


@app.route("/api/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})


@app.route("/api/bubbles", methods=["GET"])
def list_bubbles():
    emotion = request.args.get("emotion")
    if emotion:
        rows = server_db.get_bubbles_by_emotion(emotion)
    else:
        rows = server_db.get_all_active_bubbles()
    return jsonify([_serialize_bubble(r) for r in rows])


@app.route("/api/bubbles", methods=["POST"])
def create_bubble():
    data = request.get_json() or {}
    bubble_id = data.get("id")
    author = data.get("author")
    content = data.get("content")
    emotion_tag = data.get("emotion_tag")
    reply_mode = data.get("reply_mode")
    created_at = data.get("created_at")

    if not bubble_id or not author or not content or not emotion_tag or not reply_mode:
        return jsonify({"error": "missing bubble fields"}), 400

    server_db.save_bubble(bubble_id, author, content, emotion_tag, reply_mode, status="active", created_at=created_at)
    bubble = server_db.get_bubble(bubble_id)
    return jsonify(_serialize_bubble(bubble))


@app.route("/api/bubbles/<bubble_id>", methods=["GET"])
def get_bubble(bubble_id):
    viewer = request.args.get("viewer")
    bubble = server_db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "bubble not found"}), 404
    replies = [ _serialize_reply(r) for r in server_db.get_replies(bubble_id) ]
    chats = []
    if viewer:
        for chat in server_db.get_chat_messages(bubble_id):
            if chat[2] == viewer or chat[3] == viewer or bubble[1] == viewer:
                chats.append(_serialize_chat(chat))
    bubble_data = _serialize_bubble(bubble)
    bubble_data["replies"] = replies
    bubble_data["chats"] = chats
    return jsonify(bubble_data)


@app.route("/api/bubbles/<bubble_id>/reply", methods=["POST"])
def post_reply(bubble_id):
    data = request.get_json() or {}
    replier = data.get("replier")
    content = data.get("content")
    created_at = data.get("created_at")

    if not replier or not content:
        return jsonify({"error": "missing reply fields"}), 400

    bubble = server_db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "bubble not found"}), 404

    server_db.save_reply(bubble_id, replier, content, created_at)
    replies = [ _serialize_reply(r) for r in server_db.get_replies(bubble_id) ]
    return jsonify({"bubble_id": bubble_id, "replies": replies})


@app.route("/api/bubbles/<bubble_id>/chat", methods=["POST"])
def post_chat(bubble_id):
    data = request.get_json() or {}
    sender = data.get("sender")
    receiver = data.get("receiver")
    content = data.get("content")
    created_at = data.get("created_at")

    if not sender or not receiver or not content:
        return jsonify({"error": "missing chat fields"}), 400

    bubble = server_db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "bubble not found"}), 404

    server_db.save_chat_message(bubble_id, sender, receiver, content, created_at)
    chats = [ _serialize_chat(r) for r in server_db.get_chat_messages(bubble_id) ]
    return jsonify({"bubble_id": bubble_id, "chats": chats})


@app.route("/api/bubbles/<bubble_id>/resolve", methods=["POST"])
def resolve_bubble(bubble_id):
    bubble = server_db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "bubble not found"}), 404
    server_db.resolve_bubble(bubble_id)
    return jsonify({"bubble_id": bubble_id, "status": "resolved"})


@app.route("/api/bubbles/<bubble_id>/save", methods=["POST"])
def save_bubble(bubble_id):
    data = request.get_json() or {}
    is_public = data.get("is_public")
    if is_public is None:
        return jsonify({"error": "missing is_public"}), 400
    bubble = server_db.get_bubble(bubble_id)
    if not bubble:
        return jsonify({"error": "bubble not found"}), 404
    server_db.save_bubble_as(bubble_id, is_public)
    return jsonify({"bubble_id": bubble_id, "status": "saved_public" if is_public else "saved_private"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
