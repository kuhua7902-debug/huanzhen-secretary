"""H. 用量计费与临时数据库 —— ``core/database/db.py``。

风险：
- ``estimate_tool_cost`` 是给用户看的账单依据。算成负数、非确定性、
  或对未知模型直接崩，都会让计费/展示页面出错。
- 所有用例一律使用 ``tmp_path`` 下的临时 SQLite 文件，
  绝不触碰真实 ``data/keji.db``（里面有真人对话与账号）。
"""

from __future__ import annotations

import pytest

from core.database.db import MODEL_PRICING, Database, estimate_tool_cost


# ─────────────────────────── estimate_tool_cost ───────────────────────────


@pytest.mark.parametrize("model", sorted(MODEL_PRICING) + ["unknown-model-xyz", ""])
def test_estimate_tool_cost_returns_non_negative_float(model):
    """风险：负数/None 费用会让账单页面显示异常甚至崩溃；未知模型必须回退而不能抛错。"""
    cost = estimate_tool_cost(model, prompt_tokens=1000, completion_tokens=200)
    assert isinstance(cost, float)
    assert cost >= 0.0


def test_estimate_tool_cost_is_deterministic():
    """风险：非确定性（例如读了时间/随机数）会让"同一请求两次账单不一致"。"""
    args = ("deepseek-chat", 1234, 567, 89)
    assert estimate_tool_cost(*args) == estimate_tool_cost(*args)


def test_estimate_tool_cost_zero_tokens_is_zero():
    """风险：没有 token 却产生费用（计费虚高）。"""
    for model in MODEL_PRICING:
        assert estimate_tool_cost(model) == 0.0
        assert estimate_tool_cost(model, 0, 0, 0) == 0.0


def test_estimate_tool_cost_matches_pricing_table():
    """风险：计价公式与价格表脱节（读数错误导致账单偏差）。"""
    p = MODEL_PRICING["deepseek-chat"]
    cost = estimate_tool_cost("deepseek-chat", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(p["input"] + p["output"], abs=1e-6)


def test_estimate_tool_cost_unknown_model_falls_back_to_default():
    """风险：未知模型按 0 计费会让成本统计失真（用户以为免费用）。"""
    unknown = estimate_tool_cost("brand-new-model", 1000, 500, 0)
    fallback = estimate_tool_cost("deepseek-v4-flash", 1000, 500, 0)
    assert unknown == fallback


def test_cached_tokens_are_cheaper_than_fresh_input():
    """风险：缓存命中价算成原价 → 账单虚高；算成免费 → 低估成本。"""
    fresh = estimate_tool_cost("deepseek-chat", prompt_tokens=1000, cached_tokens=0)
    cached = estimate_tool_cost("deepseek-chat", prompt_tokens=1000, cached_tokens=1000)
    assert 0 < cached < fresh


def test_output_tokens_cost_more_than_input_for_chat_models():
    """风险：输入/输出单价写反（输出通常贵 4 倍以上，账单会量级出错）。"""
    inp = estimate_tool_cost("deepseek-chat", prompt_tokens=1000)
    out = estimate_tool_cost("deepseek-chat", completion_tokens=1000)
    assert out > inp


def test_estimate_tool_cost_never_negative_with_inconsistent_tokens():
    """风险：``cached_tokens > prompt_tokens``（上游统计错误）时出现负费用。"""
    cost = estimate_tool_cost("deepseek-chat", prompt_tokens=100, cached_tokens=5000)
    assert cost >= 0.0


def test_estimate_tool_cost_scales_linearly():
    """风险：公式里出现非线性（例如误用整数除法）会让大额请求算错。"""
    one = estimate_tool_cost("deepseek-chat", prompt_tokens=1000)
    ten = estimate_tool_cost("deepseek-chat", prompt_tokens=10_000)
    assert ten == pytest.approx(one * 10, rel=1e-6)


def test_pricing_table_entries_are_consistent():
    """风险：价格表缺字段/出现负价，会让任何模型的计算 KeyError 或算成负数。"""
    for model, price in MODEL_PRICING.items():
        assert set(price) >= {"input", "output", "cached_input"}, model
        for k, v in price.items():
            assert isinstance(v, (int, float)) and v >= 0, f"{model}.{k}={v}"


# ─────────────────────────── 临时数据库 ───────────────────────────


def test_database_uses_given_path_and_not_the_real_one(tmp_path):
    """风险（核心）：测试若落到真实 ``data/keji.db`` 会污染真人数据。"""
    path = tmp_path / "isolated.db"
    db = Database(str(path))
    assert db.db_path == str(path)
    assert path.exists()


def test_database_creates_user_and_reads_it_back(tmp_db):
    """风险：账号读写的列映射错位会直接导致登录用错角色/密码哈希。"""
    uid = tmp_db.create_user(username="alice", password_hash="hash-x", role="member",
                            display_name="爱丽丝")
    assert uid

    row = tmp_db.get_user_by_id(uid)
    assert row is not None
    assert row["username"] == "alice" and row["role"] == "member"
    assert row["is_active"] in (1, True)

    by_name = tmp_db.get_user_by_username("alice")
    assert by_name is not None and by_name["id"] == uid
    assert tmp_db.count_users() == 1


def test_database_rejects_duplicate_username(tmp_db):
    """风险：重名账号会让登录解析到错误的人（认证串号）。"""
    tmp_db.create_user(username="bob", password_hash="h", role="member")
    with pytest.raises(Exception):
        tmp_db.create_user(username="bob", password_hash="h", role="member")


def test_user_to_public_never_exposes_password_hash(tmp_db):
    """风险（核心）：把 password_hash 返回给前端 = 哈希泄露（可离线爆破）。"""
    uid = tmp_db.create_user(username="carol", password_hash="super-secret-hash",
                            role="admin", display_name="卡罗")
    public = tmp_db.user_to_public(tmp_db.get_user_by_id(uid))
    assert "password_hash" not in public
    assert public["username"] == "carol" and public["role"] == "admin"


def test_messages_are_counted_per_conversation(tmp_db):
    """风险：消息计数错乱会让会话列表显示错误条数（用户以为消息丢了）。"""
    tmp_db.ensure_conversation_owned("c1", "u1")
    tmp_db.add_message("c1", "user", "hi")
    tmp_db.add_message("c1", "assistant", "hello")

    conv = tmp_db.get_conversation("c1")
    assert conv is not None and conv["message_count"] == 2
    messages = tmp_db.get_messages("c1")
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_delete_conversation_removes_messages(tmp_db):
    """风险：删会话后残留消息（隐私数据未清理，且会被"重新打开同名会话"带出来）。"""
    tmp_db.ensure_conversation_owned("c1", "u1")
    tmp_db.add_message("c1", "user", "secret")
    tmp_db.delete_conversation("c1")
    assert tmp_db.get_conversation("c1") is None
    assert tmp_db.get_messages("c1") == []


def test_setting_round_trip(tmp_db):
    """风险：配置读写错位会让前端改设置后不生效（或读到别人的设置）。"""
    tmp_db.set_setting("theme", "dark")
    assert tmp_db.get_setting("theme") == "dark"
    assert tmp_db.get_setting("missing-key", default="fallback") == "fallback"
