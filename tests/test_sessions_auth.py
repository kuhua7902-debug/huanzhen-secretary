"""E. 会话键 / JWT / 密码 / 对话归属隔离。

风险集合：
- session 键拼错 → 两个用户的对话写进同一个 nanobot 会话文件（串话）。
- JWT 校验放水 → 任何人伪造 role=admin 就能拿到全部工具与全部目录。
- 密码哈希盐缺失 / 比较方式错误 → 撞库或明文可比对。
- 对话归属未记录 → 用户 A 能通过猜 conv_id 读用户 B 的聊天记录。

JWT 密钥一律用 ``monkeypatch.setenv`` 注入固定值，不读取 .env / config.yaml 的
真实密钥（``load_app_config`` 被替换为空 dict，确保走 env 分支）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.security.users import (
    _BCRYPT_SHA256_PREFIX,
    ROLES,
    CurrentUser,
    create_access_token,
    decode_access_token,
    hash_password,
    parse_session_conversation_id,
    user_session_key,
    verify_password,
)

TEST_SECRET = "unit-test-secret-not-a-real-key-0001"


@pytest.fixture
def deterministic_jwt(monkeypatch):
    """固定 JWT 密钥与过期时间，避免依赖真实配置/密钥文件（也不会写 data/security/）。"""
    import core.security.users as users

    monkeypatch.setattr(users, "load_app_config", lambda *a, **k: {})
    monkeypatch.setenv("HUANZHEN_JWT_SECRET", TEST_SECRET)
    return users


# ───────────────────────────── session key ─────────────────────────────


def test_user_session_key_format():
    """风险：格式变化会让已有会话文件全部失联（用户感觉"历史记录丢了"）。"""
    assert user_session_key("u1", "conv1") == "user:u1:conv1"


@pytest.mark.parametrize(
    "conv_id",
    [
        "abc123",
        "with-dash_and.dot",
        "含中文的会话",
        "conv:with:colons",          # 会话 ID 自身含冒号：必须原样往返
        "user:u1:looks-like-a-key",  # 会话 ID 伪装成 session key
        "a" * 200,
    ],
)
def test_session_key_round_trip(conv_id):
    """风险（核心）：会话 ID 含 ``:`` 时若用 split(":")[2] 解析，会被截断成
    另一个会话 —— 两个不同对话互相串写。因此必须用前缀剥离，本用例钉死该语义。
    """
    key = user_session_key("u1", conv_id)
    assert parse_session_conversation_id(key, "u1") == conv_id


def test_parse_session_conversation_id_rejects_other_users_key():
    """风险（核心）：用户 B 若能从用户 A 的 session key 里解析出会话 ID，
    就能直接订阅/读取别人的对话。"""
    key = user_session_key("u1", "conv1")
    assert parse_session_conversation_id(key, "u2") is None
    assert parse_session_conversation_id("user:u1:conv1", "u10") is None


def test_parse_session_conversation_id_handles_foreign_keys():
    """风险：匿名/旧格式会话键不应被误认为某人的会话（返回 None 由调用方拒绝）。"""
    assert parse_session_conversation_id("anon-session-123", "u1") is None
    assert parse_session_conversation_id("", "u1") is None


# ──────────────────────────────── JWT ────────────────────────────────


def test_jwt_round_trip_preserves_subject_and_role(deterministic_jwt):
    """风险：role 在签发/解析中丢失或串位 → 权限判定用错角色（越权/功能不可用）。"""
    token, expires_in = create_access_token("u-42", "zhangsan", "readonly")
    assert isinstance(token, str) and token.count(".") == 2
    assert expires_in > 0

    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "u-42"
    assert payload["username"] == "zhangsan"
    assert payload["role"] == "readonly"
    assert payload["exp"] > payload["iat"]


@pytest.mark.parametrize("role", sorted(ROLES))
def test_jwt_round_trip_for_every_role(deterministic_jwt, role):
    """风险：某个角色（尤其 admin）在 token 往返中丢失 → 管理功能不可用。"""
    token, _ = create_access_token("u1", "u", role)
    payload = decode_access_token(token)
    assert payload is not None and payload["role"] == role


def test_jwt_tampered_payload_decodes_to_none(deterministic_jwt):
    """风险（核心）：篡改 payload（把 role 改成 admin）必须校验失败。

    这里真实地重写 payload 段（保留原签名），避免"改 base64 尾部字符"可能只
    改到未使用的填充位、导致用例偶发失效。
    """
    import base64
    import json

    token, _ = create_access_token("u1", "u", "readonly")
    header, payload, signature = token.split(".")

    pad = "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload + pad))
    assert claims["role"] == "readonly"
    claims["role"] = "admin"
    forged_payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    assert forged_payload != payload

    assert decode_access_token(f"{header}.{forged_payload}.{signature}") is None


def test_jwt_signed_with_other_secret_decodes_to_none(deterministic_jwt, monkeypatch):
    """风险（核心）：换密钥签发的 token 必须被拒（否则任何人自签 admin 即可越权）。"""
    token, _ = create_access_token("u1", "u", "admin")
    monkeypatch.setenv("HUANZHEN_JWT_SECRET", "another-secret-entirely-0000000000")
    assert decode_access_token(token) is None


def test_garbage_token_decodes_to_none(deterministic_jwt):
    """风险：非 JWT 输入（前端传空串/随便一个字符串）不能抛异常导致 500。"""
    for bad in ("", "not-a-token", "a.b.c", "..."):
        assert decode_access_token(bad) is None


def test_expired_token_decodes_to_none(monkeypatch):
    """风险（核心）：过期 token 若仍可用，等于会话永不过期。"""
    import core.security.users as users

    monkeypatch.setattr(users, "load_app_config", lambda *a, **k: {})
    monkeypatch.setenv("HUANZHEN_JWT_SECRET", TEST_SECRET)
    # hours = -1 → exp 在过去
    monkeypatch.setattr(users, "_jwt_settings", lambda: (TEST_SECRET, -1))

    token, _ = create_access_token("u1", "u", "admin")
    assert decode_access_token(token) is None


def test_jwt_secret_comes_from_env_when_config_is_empty(monkeypatch):
    """风险：env 里的 HUANZHEN_JWT_SECRET 被忽略 → 部署时改密钥不生效（多实例互相不认）。"""
    import core.security.users as users

    monkeypatch.setattr(users, "load_app_config", lambda *a, **k: {})
    monkeypatch.setenv("HUANZHEN_JWT_SECRET", "env-secret-0000000000000000000000")
    assert users._jwt_settings()[0] == "env-secret-0000000000000000000000"


def test_jwt_hours_follow_config(monkeypatch):
    """风险：过期时长配置被忽略（用户被强制频繁重新登录，或永不过期）。"""
    import core.security.users as users

    monkeypatch.setattr(
        users, "load_app_config",
        lambda *a, **k: {"security": {"jwt_secret": "cfg-secret-0000000000000000000000",
                                      "jwt_expire_hours": 5}},
    )
    secret, hours = users._jwt_settings()
    assert (secret, hours) == ("cfg-secret-0000000000000000000000", 5)
    assert create_access_token("u", "u", "member")[1] == 5 * 3600


# ──────────────────────────────── 密码 ────────────────────────────────


def test_password_hash_verifies_correct_password():
    """风险：正确的密码登不进去（哈希方案与校验不一致）。"""
    h = hash_password("Correct-Horse-1")
    assert h != "Correct-Horse-1"
    assert verify_password("Correct-Horse-1", h) is True


def test_password_hash_rejects_wrong_password():
    """风险（核心）：错误密码通过校验 = 任意登录。"""
    h = hash_password("Correct-Horse-1")
    for wrong in ("correct-horse-1", "Correct-Horse-2", "", "Correct-Horse-1 "):
        assert verify_password(wrong, h) is False


def test_password_hash_is_salted():
    """风险：无盐哈希（或固定盐）→ 相同密码产生相同哈希，可被彩虹表/批量比对。"""
    a = hash_password("same-password")
    b = hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a) and verify_password("same-password", b)


@pytest.mark.parametrize("broken", ["", "not-a-hash", "$2b$12$tooshort", "!!!"])
def test_verify_password_returns_false_on_malformed_hash(broken):
    """风险：坏哈希不能抛异常（会把登录接口打成 500，且可能泄露堆栈）。"""
    assert verify_password("whatever", broken) is False


def test_password_hash_handles_unicode_password():
    """风险：中文密码（UTF-8 多字节）在哈希/校验往返中损坏 → 用户登不进去。"""
    for pw in ("中文密码测试", "P@ssw0rd中文"):
        h = hash_password(pw)
        assert verify_password(pw, h) is True
        assert verify_password(pw + "x", h) is False


def test_verify_password_returns_false_for_overlong_input():
    """风险：超长密码不能抛异常（登录接口靠这个返回值给 401，而不是 500）。"""
    h = hash_password("normal-password")
    assert verify_password("x" * 200, h) is False


def test_hash_password_accepts_long_passwords_used_to_raise():
    """修复后行为：>72 字节的密码也能正常哈希，不再抛 ValueError。

    原先 bcrypt 5.x 对 >72 字节直接抛 ValueError，而路由层未捕获
    （routes_admin.admin_create_user 直接调用 hash_password），
    会把"密码太长"变成 HTTP 500 而不是 400 —— 25 个汉字（75 字节）即可触发。
    现在超长密码走 SHA-256 预哈希（同 passlib 的 bcrypt_sha256），
    存储时带 ``bcrypt_sha256$`` 前缀。
    """
    pw = "x" * 73
    h = hash_password(pw)
    assert h.startswith(_BCRYPT_SHA256_PREFIX)
    assert verify_password(pw, h)


def test_hash_password_accepts_any_password_the_api_schema_allows():
    """风险：接口 schema 允许 128 字符密码，却无法哈希 → 建号/改密 500。

    files: core/security/users.py hash_password, core/routes_admin.py:20/29
    """
    pw = "汉" * 30  # 90 字节，Pydantic 的 max_length=128 会放行
    assert len(pw.encode("utf-8")) > 72
    assert hash_password(pw)
    assert verify_password(pw, hash_password(pw))


@pytest.mark.parametrize("length", [71, 72, 73, 100, 128])
def test_hash_and_verify_roundtrip_across_bcrypt_boundary(length):
    """风险：72 字节边界两侧行为不一致 → 部分用户建号成功但登录失败。"""
    pw = "a" * length
    assert verify_password(pw, hash_password(pw))
    assert not verify_password(pw + "z", hash_password(pw))


def test_legacy_unprefixed_bcrypt_hash_still_verifies():
    """风险（关键）：改造哈希格式后，历史用户全部无法登录。

    旧库里存的是无前缀的 bcrypt 哈希（当时密码必然 <=72 字节），
    必须仍然可以校验成功。
    """
    import bcrypt as _bcrypt

    legacy = _bcrypt.hashpw("legacypw".encode(), _bcrypt.gensalt()).decode("ascii")
    assert not legacy.startswith(_BCRYPT_SHA256_PREFIX)
    assert verify_password("legacypw", legacy) is True
    assert verify_password("wrong", legacy) is False


def test_malformed_password_hash_does_not_raise():
    """风险：畸形哈希（空串/垃圾/截断）必须返回 False 而不是抛异常。"""
    for bad in ("", "not-a-hash", _BCRYPT_SHA256_PREFIX + "garbage", "$2b$12$short"):
        assert verify_password("x", bad) is False


# ──────────────────────── 角色与用户对象 ────────────────────────


def test_roles_are_exactly_the_three_supported():
    """风险：新增/删除角色会静默改变权限分支（``role != "readonly"`` 是放行条件）。"""
    assert ROLES == frozenset({"admin", "member", "readonly"})


def test_current_user_is_admin_only_for_admin_role():
    """风险：is_admin 判定放宽 → member 获得管理员权限。"""
    assert CurrentUser(id="1", username="a", role="admin").is_admin is True
    for role in ("member", "readonly", "", "Admin"):
        assert CurrentUser(id="1", username="a", role=role).is_admin is False


def test_current_user_is_frozen():
    """风险：用户对象被就地改写（例如把 role 改成 admin）会绕过权限判定。"""
    user = CurrentUser(id="1", username="a", role="readonly")
    with pytest.raises(Exception):
        user.role = "admin"  # type: ignore[misc]


# ──────────────── 对话归属：跨用户读写隔离（临时 DB） ────────────────


def test_conversation_ownership_is_recorded(tmp_db):
    """风险（核心）：对话不记归属时，任何登录用户都能凭 conv_id 读到别人的聊天。"""
    tmp_db.ensure_conversation_owned("conv-a", "u1")
    conv = tmp_db.get_conversation("conv-a")
    assert conv is not None and conv["owner_user_id"] == "u1"


def test_conversation_owned_by_rejects_other_user(tmp_db):
    """风险（核心）：越权读取他人对话。"""
    tmp_db.ensure_conversation_owned("conv-a", "u1")
    assert tmp_db.conversation_owned_by("conv-a", "u1") is True
    assert tmp_db.conversation_owned_by("conv-a", "u2") is False


def test_conversation_owned_by_fail_closed_for_ownerless_conversation(tmp_db):
    """风险：历史遗留的无主对话必须视为"不属于任何人"（fail-closed），
    否则谁都能认领并读取。"""
    tmp_db.create_conversation("legacy-conv")  # 不传 owner
    assert tmp_db.conversation_owned_by("legacy-conv", "u1") is False


def test_conversation_owned_by_unknown_conversation_is_true(tmp_db):
    """风险：这是**有意**的 fail-open —— 不存在的会话由 ensure_conversation_owned
    以调用者身份创建。此处固定该契约，避免有人"顺手改成 False"打断新会话首轮。"""
    assert tmp_db.conversation_owned_by("never-created", "u1") is True


def test_list_conversations_filters_by_owner(tmp_db):
    """风险：会话列表不按 owner 过滤 → 侧边栏显示别人的对话标题。"""
    tmp_db.ensure_conversation_owned("c1", "u1")
    tmp_db.ensure_conversation_owned("c2", "u2")
    mine = [c["id"] for c in tmp_db.list_conversations(owner_user_id="u1")]
    assert mine == ["c1"]
    assert len(tmp_db.list_conversations()) == 2


def test_delete_user_removes_their_conversations(tmp_db):
    """风险：删号后残留对话与消息（隐私数据未清理）。"""
    uid = tmp_db.create_user(username="ghost", password_hash="x", role="member")
    tmp_db.ensure_conversation_owned("c-ghost", uid)
    tmp_db.add_message("c-ghost", "user", "hello")
    assert tmp_db.get_conversation("c-ghost") is not None

    tmp_db.delete_user(uid)
    assert tmp_db.get_conversation("c-ghost") is None
    assert tmp_db.get_user_by_id(uid) is None


# ──────────────── resolve_chat_ids：会话键与归属的落地点 ────────────────


def _stub_request(user):
    """最小 Request 替身：被测代码只读取 ``request.state.user``。"""
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.fixture
def chat_session_module(monkeypatch, tmp_db):
    """把 chat_session 的 ``get_db`` 指向临时库，避免写真实 data/huanzhen.db。"""
    import core.security.chat_session as cs
    import core.database.db as dbmod

    monkeypatch.setattr(dbmod, "get_db", lambda: tmp_db)
    monkeypatch.setattr(cs, "get_db", lambda: tmp_db)
    return cs


def test_resolve_chat_ids_namespaces_session_by_user(chat_session_module, tmp_db):
    """风险（核心）：未按用户加前缀 → 用户 A、B 用同一个 conv_id 时读写同一份会话历史。"""
    user = CurrentUser(id="u1", username="u1", role="member")
    sk, conv, got_user = chat_session_module.resolve_chat_ids(
        _stub_request(user), conversation_id="conv-1"
    )
    assert sk == "user:u1:conv-1"
    assert conv == "conv-1" and got_user is user
    assert tmp_db.get_conversation("conv-1")["owner_user_id"] == "u1"


def test_resolve_chat_ids_keeps_anonymous_sessions_unnamespaced(chat_session_module):
    """风险：匿名会话若被加上 ``user:anonymous:`` 前缀，会与真人 session 命名空间混淆；
    当前契约：匿名直接用 conv_id。"""
    anon = CurrentUser(id="anonymous", username="anonymous", role="member")
    sk, conv, user = chat_session_module.resolve_chat_ids(
        _stub_request(anon), conversation_id="conv-2"
    )
    assert (sk, conv) == ("conv-2", "conv-2") and user is anon


def test_resolve_chat_ids_generates_id_when_missing(chat_session_module):
    """风险：空会话 ID 落到同一个文件名 → 所有新会话互相覆盖。"""
    sk1, conv1, _ = chat_session_module.resolve_chat_ids(_stub_request(None))
    sk2, conv2, _ = chat_session_module.resolve_chat_ids(_stub_request(None))
    assert conv1 and conv2 and conv1 != conv2
    assert sk1 == conv1 and sk2 == conv2


def test_resolve_chat_ids_prefers_conversation_id_over_session_id(chat_session_module):
    """风险：两者都传时优先级写反会让前端切换对话失效（一直回到旧会话）。"""
    user = CurrentUser(id="u1", username="u1", role="member")
    sk, conv, _ = chat_session_module.resolve_chat_ids(
        _stub_request(user), session_id="sess-x", conversation_id="conv-y"
    )
    assert conv == "conv-y" and sk == "user:u1:conv-y"


def test_resolve_chat_ids_strips_whitespace(chat_session_module):
    """风险：带空格的 conv_id 会产生两个不同的会话文件（同一对话分裂）。"""
    user = CurrentUser(id="u1", username="u1", role="member")
    _, conv, _ = chat_session_module.resolve_chat_ids(
        _stub_request(user), conversation_id="  conv-z  "
    )
    assert conv == "conv-z"
