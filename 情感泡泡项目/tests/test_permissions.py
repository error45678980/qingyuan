"""T-05 验收：4 角色 × 7 操作权限矩阵 + 旧数据作者回退。"""

from __future__ import annotations

import unittest

from src.core.constants import (
    DENY_REASON_NOT_PARTICIPANT,
    DENY_REASON_NOT_VISIBLE,
    DENY_REASON_OWN_BUBBLE,
    DENY_REASON_RESOLVED,
    DENY_REASON_SINGLE_REPLIED,
)
from src.core.models import Bubble
from src.core.permissions import Identity, Permissions, evaluate, is_legacy_owner


def _bubble(**kwargs) -> Bubble:
    base = dict(
        id="b1",
        author_nickname="Owner",
        author_client_id="cid-owner",
        content="内容",
        emotion_tag="开心",
        reply_mode="dialog",
        status="active",
        version=1,
        created_at="2026-07-21T17:00:00",
    )
    base.update(kwargs)
    return Bubble(**base)


OWNER = Identity("cid-owner", "Owner")
REPLIER = Identity("cid-replier", "Replier")
OTHER_PARTICIPANT = Identity("cid-other", "Other")
STRANGER = Identity("cid-stranger", "Stranger")

#: 各角色对应的 (my_reply_count, has_chatted)
ROLE_INPUTS: dict[str, tuple[Identity, int, bool]] = {
    "owner": (OWNER, 0, False),
    "replier": (REPLIER, 1, True),
    "other_participant": (OTHER_PARTICIPANT, 0, True),
    "stranger": (STRANGER, 0, False),
}


class MatrixTest(unittest.TestCase):
    """dialog + active 场景的完整矩阵（4 角色 × 7 操作）。"""

    #: 角色 → (is_owner, is_replier, is_participant, can_read, can_reply, can_chat,
    #:          can_manage, can_read_all_chats, status_code)
    EXPECTED: dict[str, tuple] = {
        "owner": (True, False, True, True, False, True, True, True, 403),
        "replier": (False, True, True, True, True, True, False, False, 200),
        "other_participant": (False, False, True, True, True, True, False, False, 200),
        "stranger": (False, False, False, True, True, False, False, False, 200),
    }

    def test_matrix(self) -> None:
        bubble = _bubble()
        for role, (identity, reply_count, chatted) in ROLE_INPUTS.items():
            with self.subTest(role=role):
                perms = evaluate(
                    bubble, identity, my_reply_count=reply_count, has_chatted=chatted
                )
                expected = self.EXPECTED[role]
                actual = (
                    perms.is_owner,
                    perms.is_replier,
                    perms.is_participant,
                    perms.can_read,
                    perms.can_reply,
                    perms.can_chat,
                    perms.can_manage,
                    perms.can_read_all_chats,
                    perms.status_code,
                )
                self.assertEqual(actual, expected)

    def test_owner_deny_reason(self) -> None:
        perms = evaluate(_bubble(), OWNER, my_reply_count=0, has_chatted=False)
        self.assertEqual(perms.deny_reason, DENY_REASON_OWN_BUBBLE)

    def test_stranger_chat_deny_reason(self) -> None:
        perms = evaluate(_bubble(), STRANGER, my_reply_count=0, has_chatted=False)
        self.assertEqual(perms.deny_reason, DENY_REASON_NOT_PARTICIPANT)

    def test_no_deny_reason_when_allowed(self) -> None:
        perms = evaluate(_bubble(), REPLIER, my_reply_count=1, has_chatted=True)
        self.assertEqual(perms.deny_reason, "")


class SingleModeTest(unittest.TestCase):
    """`single` 模式：同一设备二次回复必须 409。"""

    def test_second_reply_returns_409(self) -> None:
        perms = evaluate(
            _bubble(reply_mode="single"), REPLIER, my_reply_count=1, has_chatted=True
        )
        self.assertFalse(perms.can_reply)
        self.assertEqual(perms.status_code, 409)
        self.assertEqual(perms.deny_reason, DENY_REASON_SINGLE_REPLIED)

    def test_first_reply_allowed(self) -> None:
        perms = evaluate(
            _bubble(reply_mode="single"), REPLIER, my_reply_count=0, has_chatted=False
        )
        self.assertTrue(perms.can_reply)
        self.assertEqual(perms.status_code, 200)

    def test_dialog_allows_many_replies(self) -> None:
        perms = evaluate(
            _bubble(reply_mode="dialog"), REPLIER, my_reply_count=5, has_chatted=True
        )
        self.assertTrue(perms.can_reply)
        self.assertEqual(perms.status_code, 200)


class StatusTest(unittest.TestCase):
    """不同状态的可见性与可操作性。"""

    def test_resolved_hidden_from_others(self) -> None:
        bubble = _bubble(status="resolved")
        for role in ("replier", "other_participant", "stranger"):
            identity, count, chatted = ROLE_INPUTS[role]
            with self.subTest(role=role):
                perms = evaluate(bubble, identity, my_reply_count=count, has_chatted=chatted)
                self.assertFalse(perms.can_read)
                self.assertFalse(perms.can_reply)
                self.assertFalse(perms.can_chat)
                self.assertEqual(perms.status_code, 403)
                self.assertEqual(perms.deny_reason, DENY_REASON_NOT_VISIBLE)

    def test_resolved_readable_by_owner(self) -> None:
        perms = evaluate(_bubble(status="resolved"), OWNER, my_reply_count=0, has_chatted=False)
        self.assertTrue(perms.can_read)
        self.assertTrue(perms.can_manage)
        self.assertFalse(perms.can_chat)
        self.assertEqual(perms.deny_reason, DENY_REASON_RESOLVED)

    def test_saved_private_invisible_to_others(self) -> None:
        bubble = _bubble(status="saved_private")
        perms = evaluate(bubble, REPLIER, my_reply_count=1, has_chatted=True)
        self.assertFalse(perms.can_read)
        self.assertFalse(perms.can_reply)
        self.assertFalse(perms.can_chat)
        self.assertEqual(perms.status_code, 403)

    def test_saved_private_visible_to_owner(self) -> None:
        perms = evaluate(
            _bubble(status="saved_private"), OWNER, my_reply_count=0, has_chatted=False
        )
        self.assertTrue(perms.can_read)
        self.assertTrue(perms.can_manage)

    def test_saved_public_visible_and_repliable(self) -> None:
        bubble = _bubble(status="saved_public", reply_mode="single")
        perms = evaluate(bubble, STRANGER, my_reply_count=0, has_chatted=False)
        self.assertTrue(perms.can_read)
        self.assertTrue(perms.can_reply)

    def test_private_owner_can_still_manage(self) -> None:
        perms = evaluate(
            _bubble(status="saved_private", reply_mode="single"),
            OWNER,
            my_reply_count=0,
            has_chatted=False,
        )
        self.assertTrue(perms.can_manage)
        self.assertTrue(perms.can_read_all_chats)


class LegacyOwnerTest(unittest.TestCase):
    """旧数据（`author_client_id` 为空）的作者判定回退昵称。"""

    def test_legacy_owner_by_nickname(self) -> None:
        bubble = _bubble(author_client_id="", author_nickname="Tester")
        me = Identity("cid-any", "Tester")
        self.assertTrue(is_legacy_owner(bubble, me))
        perms = evaluate(bubble, me, my_reply_count=0, has_chatted=False)
        self.assertTrue(perms.is_owner)
        self.assertTrue(perms.legacy_owner)
        self.assertTrue(perms.can_manage)

    def test_same_nickname_different_device_still_legacy_owner(self) -> None:
        """Q-1 的取舍：旧数据只能回退昵称（无设备信息可用）。"""
        bubble = _bubble(author_client_id="", author_nickname="Tester")
        perms = evaluate(
            bubble, Identity("cid-new", "Tester"), my_reply_count=0, has_chatted=False
        )
        self.assertTrue(perms.legacy_owner)

    def test_nickname_mismatch_not_owner(self) -> None:
        bubble = _bubble(author_client_id="", author_nickname="Tester")
        perms = evaluate(
            bubble, Identity("cid-x", "Other"), my_reply_count=0, has_chatted=False
        )
        self.assertFalse(perms.is_owner)
        self.assertFalse(perms.legacy_owner)

    def test_device_id_beats_nickname(self) -> None:
        """有 `author_client_id` 时不得回退昵称（否则任何人冒名即得管理权）。"""
        bubble = _bubble(author_client_id="cid-owner", author_nickname="Owner")
        perms = evaluate(
            bubble, Identity("cid-intruder", "Owner"), my_reply_count=0, has_chatted=False
        )
        self.assertFalse(perms.is_owner)
        self.assertFalse(perms.legacy_owner)

    def test_explicit_legacy_owner_flag(self) -> None:
        bubble = _bubble(author_client_id="", author_nickname="Tester")
        perms = evaluate(
            bubble,
            Identity("cid-x", ""),
            my_reply_count=0,
            has_chatted=False,
            legacy_owner=True,
        )
        self.assertTrue(perms.is_owner)
        self.assertTrue(perms.legacy_owner)


class IdentityTest(unittest.TestCase):
    """身份不可变。"""

    def test_frozen(self) -> None:
        identity = Identity("cid", "n")
        with self.assertRaises(Exception):
            identity.client_id = "other"  # type: ignore[misc]

    def test_permissions_defaults(self) -> None:
        perms = Permissions(
            is_owner=False,
            is_replier=False,
            is_participant=False,
            can_read=True,
            can_reply=True,
            can_chat=False,
            can_manage=False,
            can_read_all_chats=False,
        )
        self.assertEqual(perms.deny_reason, "")
        self.assertEqual(perms.status_code, 200)
        self.assertFalse(perms.legacy_owner)


if __name__ == "__main__":
    unittest.main()
