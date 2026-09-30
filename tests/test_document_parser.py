"""F. 文档解析 / 分块 —— ``core.document.parser``。

风险：
- 判断"是否支持某扩展名"出错 → 知识库索引整类文件丢失（或对二进制乱解析）。
- ``chunk_text`` 丢内容 → 索引里少了段落，检索永远查不到（静默数据丢失）。
- 分块不遵守 chunk_size → 超出 embedding 模型上下文，整批索引失败。
- 解析不存在/损坏的文件时抛未捕获异常 → 接口 500。

本模块只做纯函数 + tmp_path 文件，不触发 chromadb / Ollama。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.document.parser import (
    CATEGORY_MAP,
    SUPPORTED_EXTENSIONS,
    TEXT_EXTENSIONS,
    chunk_text,
    count_tokens,
    get_doc_category,
    get_file_metadata,
    get_file_type,
    is_supported,
    is_text_file,
    parse_document,
)


# ────────────────────────────── 类型判定 ──────────────────────────────


@pytest.mark.parametrize(
    "name", ["a.txt", "a.md", "a.py", "a.json", "a.yaml", "a.csv",
             "a.pdf", "a.docx", "a.xlsx", "a.pptx", "a.log", "a.sql", "a.eml"]
)
def test_is_supported_for_known_extensions(name):
    """风险：常用格式被漏掉 → 用户上传后知识库静默不收，检索不到。"""
    assert is_supported(name) is True


@pytest.mark.parametrize("name", ["a.exe", "a.dll", "a.bin", "a.zip", "noext", "a.txt.bak", ""])
def test_is_supported_false_for_unknown_extensions(name):
    """风险：把未知/二进制格式当文本解析会污染知识库（乱码进入向量库）。"""
    assert is_supported(name) is False


def test_get_file_type_is_lowercased_extension():
    """风险：大小写不统一会让 ``.TXT`` 走不到文本分支（用户上传即失败）。"""
    assert get_file_type("C:/x/Report.TXT") == ".txt"
    assert get_file_type("a.Tar.GZ") == ".gz"
    assert get_file_type("noext") == ""


def test_get_doc_category_maps_known_and_defaults_to_other():
    """风险：分类错位会让前端按类型渲染错误图标/预览器。"""
    assert get_doc_category("a.xlsx") == "spreadsheet"
    assert get_doc_category("a.PDF") == "document"
    assert get_doc_category("a.weird") == "other"


def test_supported_and_text_extension_sets_are_consistent():
    """风险：文本集合里混入 .pdf/.docx 会让二进制文档被当纯文本读成乱码。"""
    assert TEXT_EXTENSIONS <= SUPPORTED_EXTENSIONS
    assert ".pdf" not in TEXT_EXTENSIONS
    assert ".docx" not in TEXT_EXTENSIONS
    assert set(CATEGORY_MAP) <= SUPPORTED_EXTENSIONS


def test_is_text_file():
    """风险：文本/二进制分支判断反了会让 read_document 输出乱码。"""
    assert is_text_file("a.txt") is True
    assert is_text_file("a.PY") is True
    assert is_text_file("a.pdf") is False


# ────────────────────────────── 解析 ──────────────────────────────


def test_parse_document_reads_utf8_text(tmp_path):
    """风险（基础功能）：纯文本解析失败/乱码会让所有 .txt/.md 知识入库失败。"""
    p = tmp_path / "doc.txt"
    content = "幻帧测试文档\n第二行：中文内容 test 123\n" * 5
    p.write_text(content, encoding="utf-8")
    assert parse_document(str(p)) == content


def test_parse_document_reads_gbk_text(tmp_path):
    """风险：中文 Windows 生成的 GBK 文件解析失败（企业场景最常见）。

    编码探测走 chardet，样本太短时探测结果不可靠；这里给足长度让行为稳定。
    """
    p = tmp_path / "gbk.txt"
    content = "国标编码文件内容测试" * 20
    p.write_bytes(content.encode("gbk"))
    assert parse_document(str(p)) == content


def test_parse_document_returns_none_for_unsupported_extension(tmp_path):
    """风险：未知扩展名必须返回 None（调用方据此回"不支持"），不能抛异常。"""
    p = tmp_path / "blob.weirdext"
    p.write_text("data", encoding="utf-8")
    assert parse_document(str(p)) is None


def test_parse_document_returns_empty_string_for_empty_text_file(tmp_path):
    """记录当前行为：空文本文件返回 ``""``（而非 None）—— 调用方用 ``is None`` 判断
    "解析失败"，所以空文件会被当成"成功但没内容"（知识库会跳过）。"""
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    assert parse_document(str(p)) == ""


def test_parse_document_raises_for_missing_text_file(tmp_path):
    """记录当前行为：``parse_document`` 对不存在的 **文本** 文件会抛 FileNotFoundError。

    注意：产品路径（``read_document``）先用 ``check_path(must_exist=True)``
    拦了一道，所以不会把异常暴露给用户；但直接调用本函数（例如未来新增的
    调用点）需要自己兜住。
    """
    with pytest.raises(FileNotFoundError):
        parse_document(str(tmp_path / "ghost.txt"))


def test_parse_document_does_not_crash_on_binary_masquerading_as_text(tmp_path):
    """风险：把 exe 改名成 .txt 上传时不能抛异常（会用 latin-1 兜底解码成乱码）。"""
    p = tmp_path / "fake.txt"
    p.write_bytes(bytes(range(128, 256)) * 4)
    out = parse_document(str(p))
    assert out is None or isinstance(out, str)


def test_get_file_metadata_for_existing_file(tmp_path):
    """风险：元数据（大小/类型/分类）错位会让前端展示错误信息，
    也会让 ">20MB 不处理" 之类的护栏失效。"""
    p = tmp_path / "meta.txt"
    payload = "中文abc" * 10
    p.write_text(payload, encoding="utf-8")

    meta = get_file_metadata(str(p))
    assert meta["size"] == len(payload.encode("utf-8"))
    assert meta["ext"] == ".txt"
    assert meta["category"] == "text"
    assert meta["modified"] > 0


def test_get_file_metadata_defaults_on_missing_file(tmp_path):
    """风险：文件不存在时必须返回安全默认值（不能抛异常，否则文件浏览接口 500）。"""
    meta = get_file_metadata(str(tmp_path / "nope.docx"))
    assert meta["size"] == 0 and meta["ext"] == "" and meta["category"] == "other"


# ────────────────────────────── chunk_text ──────────────────────────────


def test_chunk_text_empty_input_returns_empty_list():
    """风险：空输入返回 ``[""]`` 会在向量库里写一条空向量（污染检索）。"""
    assert chunk_text("") == []
    assert chunk_text(None) == []  # type: ignore[arg-type]


def test_chunk_text_whitespace_only_input_returns_empty():
    """修复后行为：纯空白输入返回 []，不再返回一个"空白块"。

    空白块会被送去 embedding（写一条无意义向量、污染检索），所以返回空列表
    才是正确的。参考 core/document/parser.py::chunk_text 末尾。
    """
    for blank in ("   ", "\n\n", "\t \n"):
        out = chunk_text(blank)
        assert isinstance(out, list)
        assert out == [], f"纯空白输入不应产出块，实际: {out!r}"


def test_chunk_text_short_text_is_a_single_chunk():
    """风险：短文被切碎会让一句话的上下文被拆散（检索质量下降）。"""
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_text_keeps_all_paragraphs(tmp_path):
    """风险（核心）：分块丢内容 = 知识库静默缺页，检索永远查不到。"""
    paras = [f"段落{i}-" + "内容" * 20 for i in range(12)]
    text = "\n\n".join(paras)

    chunks = chunk_text(text, chunk_size=200, overlap=50)
    assert len(chunks) > 1
    for para in paras:
        assert any(para in c for c in chunks), f"段落丢失: {para[:20]}"


def test_chunk_text_respects_chunk_size_and_overlaps_paragraphs():
    """风险：chunk_size 失效 → 单块超出 embedding 上下文，整批索引失败。
    同时检查 overlap 生效（相邻块共享段落），否则跨块语义会被切断。
    """
    paras = [f"P{i}-" + "x" * 40 for i in range(8)]
    chunks = chunk_text("\n\n".join(paras), chunk_size=100, overlap=50)

    assert len(chunks) > 1
    # 段落被整段放入，故每块大小受 chunk_size + 单个段落长度约束
    assert max(len(c) for c in chunks) <= 100 + len(paras[0])
    # 相邻块之间必须有重叠段落
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.split("\n\n")[-1] == nxt.split("\n\n")[0]


def test_chunk_text_overlap_zero_does_not_repeat_paragraphs():
    """修复后行为：``overlap=0`` 时相邻块**不再**共享段落。

    原实现在收集 overlap 的循环里用
    ``if overlap_len + len(p) > overlap and overlap_texts``，
    首次判断因 ``overlap_texts`` 为空而失效，于是"第一个反序段落"被无条件保留，
    overlap=0 也无法真正关闭重叠。现在 overlap<=0 时直接跳过收集。
    风险：关不掉重叠会让索引里出现重复文本（检索返回重复片段）。
    """
    paras = [f"Q{i}-" + "y" * 40 for i in range(6)]
    chunks = chunk_text("\n\n".join(paras), chunk_size=100, overlap=0)

    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.split("\n\n")[-1] != nxt.split("\n\n")[0], "overlap=0 仍在重复段落"


def test_chunk_text_splits_oversized_ascii_paragraph_by_sentence():
    """风险：单段超长（无空行）时若不按句切分，会产出一个远超 chunk_size 的块。

    注意：切句用的正则是"标点 + 空白"，所以英文句子（句号后有空格）能被切开。
    """
    text = "This is sentence number one. " * 30  # 单段，无空行，含句间空格
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= 2 * 100


def test_chunk_text_splits_long_cjk_paragraph_without_spaces():
    """修复后行为：没有空格分隔的中文长段落也会按 chunk_size 切开。

    原来切句用 ``(?<=[.!?。！？])\\s+``，**要求标点后有空白**；中文句号后通常没有
    空格，于是一整段中文（哪怕几万字）被当成"一个句子"原样返回，chunk_size 完全
    失效 —— 超长块会让 embedding 报错、整批索引进失败。
    现在改为不要求尾部空白，并对无标点的超长文本按字符硬切。
    """
    text = "这是中文句子。" * 60  # 420 字，句号后无空格
    chunks = chunk_text(text, chunk_size=100, overlap=20)

    assert len(chunks) > 1, "中文长段落仍未被切分"
    assert max(len(c) for c in chunks) <= 100 + 20, "仍存在超出 chunk_size 的块"


def test_chunk_text_hard_splits_unpunctuated_long_text():
    """修复后行为：完全没有标点的超长文本按字符硬切，而不是产出一个巨型块。

    风险同上：无句读的流水账/长串数字在旧实现下会整段变成一个块。
    """
    chunks = chunk_text("A" * 1000, chunk_size=100, overlap=10)
    assert len(chunks) > 1
    assert max(len(c) for c in chunks) <= 100


def test_chunk_text_cjk_join_does_not_insert_spaces():
    """风险：中文句子拼接时若沿用西文的 ``" "`` 连接，会在句号后插入多余空格，
    污染正文与检索结果。
    """
    chunks = chunk_text("第一句。第二句。第三句。" * 20, chunk_size=40, overlap=5)
    assert chunks
    for c in chunks:
        assert "。 " not in c, f"中文句号后出现了空格: {c[:40]!r}"


def test_chunk_text_returns_list_of_str():
    """风险：返回非字符串元素（例如 None）会让下游 embedding 崩溃。"""
    out = chunk_text("a\n\nb\n\nc", chunk_size=10, overlap=2)
    assert isinstance(out, list) and out
    assert all(isinstance(c, str) and c for c in out)


# ────────────────────────────── count_tokens ──────────────────────────────


def test_count_tokens_is_monotonic_and_zero_for_empty():
    """风险：token 估算为负/为 0 会让"上下文超限"护栏失效或误伤。"""
    assert count_tokens("") == 0
    assert count_tokens(None) == 0  # type: ignore[arg-type]
    short = count_tokens("hello world")
    long = count_tokens("hello world " * 50)
    assert 0 < short < long


def test_count_tokens_counts_chinese_higher_than_ascii_words():
    """风险：中文被按"英文单词"计数（1 个/串）会严重低估上下文长度。"""
    cn = count_tokens("中文测试内容" * 5)      # 30 个汉字
    en = count_tokens("word " * 5)             # 5 个英文词
    assert cn > en
