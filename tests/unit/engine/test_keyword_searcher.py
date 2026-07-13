"""KeywordSearcher 单元测试。"""

import logging
from unittest.mock import Mock

import jieba
import pytest

from bot.engine.keyword_searcher import KeywordSearcher
from bot.engine.metadata_store import MemeEntry
from bot.engine.types import SearchResult


class MockMetadataStore:
    """模拟 MetadataStore，返回预定义的 entries 字典。"""

    def __init__(self, entries: dict[int, MemeEntry] | None = None) -> None:
        self._entries = entries or {}

    def get_all_entries(self) -> dict[int, MemeEntry]:
        return self._entries


@pytest.fixture
def sample_entries() -> dict[int, MemeEntry]:
    """标准测试用的表情包索引数据（text 已无空格）。"""
    return {
        1: MemeEntry(id=1, image_path="cat.jpg", text="一只猫在跳起来抓蝴蝶哈哈哈"),
        2: MemeEntry(id=2, image_path="overtime.jpg", text="加班到凌晨三点的我"),
        3: MemeEntry(
            id=3,
            image_path="suspect.jpg",
            text="人家一片热忱你怎能以小人之心度君子之腹呢",
        ),
        4: MemeEntry(id=4, image_path="empty.jpg", text=""),
        5: MemeEntry(id=5, image_path="boss.jpg", text="当你的老板说今天要加班"),
        6: MemeEntry(id=6, image_path="sunday.jpg", text="周日晚上的加班通知"),
    }


@pytest.fixture
def searcher(sample_entries: dict[int, MemeEntry]) -> KeywordSearcher:
    return KeywordSearcher(MockMetadataStore(sample_entries))


class TestSearchResult:
    def test_create(self) -> None:
        r = SearchResult(
            entry_id=1, image_path="cat.jpg", text="一只猫", similarity=85.5
        )
        assert r.entry_id == 1
        assert r.image_path == "cat.jpg"
        assert r.text == "一只猫"
        assert r.similarity == 85.5


def test_search_result_carries_speaker_and_tags() -> None:
    """KeywordSearcher 应把 MemeEntry 的 speaker/tags 带到 SearchResult。"""
    entries = {
        1: MemeEntry(
            id=1,
            image_path="a.jpg",
            text="加班",
            speaker="小明",
            tags=["吐槽", "加班"],
        ),
    }
    searcher = KeywordSearcher(MockMetadataStore(entries))
    results = searcher.search("加班")
    assert len(results) == 1
    assert results[0].speaker == "小明"
    assert results[0].tags == ["吐槽", "加班"]


class TestInit:
    def test_default_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._threshold == 60.0

    def test_custom_threshold(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), threshold=80.0)._threshold == 80.0

    def test_default_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore())._limit is None

    def test_custom_limit(self) -> None:
        assert KeywordSearcher(MockMetadataStore(), limit=5)._limit == 5


class TestWarmUp:
    """warm_up：启动阶段预加载 jieba 默认词典。"""

    def test_initializes_jieba_and_logs_success(
        self,
        searcher: KeywordSearcher,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """预热应初始化 jieba 并记录完成日志。"""
        initialize = Mock()
        monkeypatch.setattr(jieba, "initialize", initialize)
        caplog.set_level(logging.INFO, logger="bot.engine.keyword_searcher")

        searcher.warm_up()

        initialize.assert_called_once_with()
        assert "关键词搜索预热完成" in caplog.text

    def test_propagates_initialization_error(
        self,
        searcher: KeywordSearcher,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """jieba 初始化失败时应原样传播异常。"""
        error = RuntimeError("词典加载失败")
        initialize = Mock(side_effect=error)
        monkeypatch.setattr(jieba, "initialize", initialize)

        with pytest.raises(RuntimeError) as exc_info:
            searcher.warm_up()

        assert exc_info.value is error


class TestSearchExactSubstring:
    def test_short_keyword_in_long_text(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("小人之心")
        assert len(results) == 1
        assert results[0].entry_id == 3
        assert results[0].image_path == "suspect.jpg"
        assert results[0].similarity == 100.0

    def test_keyword_hits_multiple(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班")
        assert len(results) == 3
        assert all(r.similarity == 100.0 for r in results)
        assert {r.entry_id for r in results} == {2, 5, 6}

    def test_full_text_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班到凌晨三点的我")
        assert len(results) == 1
        assert results[0].entry_id == 2
        assert results[0].similarity == 100.0


class TestSearchExactSubstringLayer:
    """第一层：原始去空白关键词的精确子串短路。"""

    def test_raw_substring_preserves_particles(self):
        # 含助词的原始输入，raw 恰为 text 子串即命中
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="的加班心累")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("的加班")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_internal_whitespace_stripped_in_raw(self):
        # 内部空白被去除后再做子串判定
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="加班了")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班 了")
        assert len(results) == 1
        assert results[0].similarity == 100.0

    def test_raw_miss_falls_back_to_lcs(self):
        # raw 不是任何 text 子串 → 回退 LCS（cleaned 去助词后是 text 子串 → 100）
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search(
            "了加班吗"
        )  # raw="了加班吗" 不命中；cleaned="加班" 是 text 子串 → 100
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_raw_hit_excludes_non_substring_entries(self):
        # 第一层命中即短路，非子串条目不进入结果
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班到凌晨"),
            2: MemeEntry(id=2, image_path="b.jpg", text="完全无关的文本"),
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班")  # raw 命中 entry 1；entry 2 不含"加班"子串
        assert {r.entry_id for r in results} == {1}
        assert all(r.similarity == 100.0 for r in results)

    def test_raw_hit_respects_limit(self):
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        results = s.search("加班")
        assert len(results) == 5
        assert all(r.similarity == 100.0 for r in results)

    def test_raw_hit_strictness_vs_cleaned(self):
        # raw="的鱼" 命中 entry1 的 "的鱼"；去助词后 cleaned="鱼" 同时命中 entry1 和 entry2
        # 现有实现（去助词）会返回 2 条；新实现第一层只返回 raw 命中的 1 条
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="这是的鱼"),
            2: MemeEntry(id=2, image_path="b.jpg", text="鱼在游"),
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("的鱼")
        assert {r.entry_id for r in results} == {1}
        assert all(r.similarity == 100.0 for r in results)


class TestSearchFuzzy:
    def test_partial_overlap(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_non_contiguous_match(self, searcher: KeywordSearcher) -> None:
        results = searcher.search("加班凌晨通知")
        assert len(results) >= 1
        assert all(60.0 <= r.similarity < 100.0 for r in results)


class TestSearchWithParticleRemoval:
    def test_drops_particles(self, sample_entries: dict[int, MemeEntry]) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        results = s.search("了加班吗")
        assert {r.entry_id for r in results} == {2, 5, 6}
        assert all(r.similarity == 100.0 for r in results)

    def test_all_particles_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search("的呢吗") == []

    def test_content_word_with_embedded_particle_char(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="a.jpg", text="了解详情请咨询")}
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("了解")
        assert len(results) == 1
        assert results[0].similarity == 100.0


class TestSearchEdgeCases:
    def test_empty_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("") == []

    def test_whitespace_keyword(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("   ") == []

    def test_no_match(self, searcher: KeywordSearcher) -> None:
        assert searcher.search("火星文xyz") == []

    def test_empty_entries(self) -> None:
        assert KeywordSearcher(MockMetadataStore({})).search("加班") == []

    def test_all_empty_text(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text=""),
            2: MemeEntry(id=2, image_path="b.jpg", text=""),
        }
        assert KeywordSearcher(MockMetadataStore(entries)).search("加班") == []

    def test_below_threshold_filtered(self) -> None:
        entries = {1: MemeEntry(id=1, image_path="x.jpg", text="今天天气真好")}
        s = KeywordSearcher(MockMetadataStore(entries), threshold=90.0)
        assert s.search("加班") == []


class TestSearchResultOrder:
    def test_default_limit_returns_all(self) -> None:
        """limit 默认 None 时返回全部匹配（不截断到 10）。"""
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)  # 15 条全部命中"加班"
        }
        s = KeywordSearcher(MockMetadataStore(entries))  # 默认 limit=None
        results = s.search("加班")
        assert len(results) == 15
        assert all(r.similarity == 100.0 for r in results)

    def test_limit_truncation(self) -> None:
        entries = {
            i: MemeEntry(id=i, image_path=f"meme_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        assert len(s.search("加班")) == 5

    def test_perfect_score_filters_others(self) -> None:
        entries = {
            1: MemeEntry(id=1, image_path="a.jpg", text="加班"),
            2: MemeEntry(id=2, image_path="b.jpg", text="加班到凌晨"),
            3: MemeEntry(id=3, image_path="c.jpg", text="加斑"),  # LCS=1, score=50
        }
        s = KeywordSearcher(MockMetadataStore(entries))
        results = s.search("加班")
        assert {r.entry_id for r in results} == {1, 2}
        assert all(r.similarity == 100.0 for r in results)


class TestParticleRemovalFn:
    def test_removes_structural_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("的加班") == "加班"

    def test_removes_modal_particle(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert _remove_particles("加班了吗") == "加班"

    def test_all_particles_returns_empty(self) -> None:
        from bot.engine.keyword_searcher import _remove_particles

        assert "".join(_remove_particles("的呢吗").split()) == ""


class TestStripAllWhitespace:
    """_strip_all_whitespace：去除所有空白字符，保留助词。"""

    def test_removes_internal_space(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("加班 了") == "加班了"

    def test_removes_all_kinds_of_whitespace(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace(" 加\n班\t了　") == "加班了"

    def test_preserves_particles(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("的加班吗") == "的加班吗"

    def test_empty_string(self):
        from bot.engine.keyword_searcher import _strip_all_whitespace

        assert _strip_all_whitespace("   ") == ""


class TestSearchIn:
    """search_in：在给定 entries 子集上搜索。"""

    def test_search_in_respects_subset(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        """search_in 只在传入子集上匹配，不触及全集其他条目。"""
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        subset = {5: sample_entries[5]}  # "当你的老板说今天要加班"
        results = s.search_in(subset, "加班")
        assert len(results) == 1
        assert results[0].entry_id == 5
        assert results[0].similarity == 100.0

    def test_search_in_empty_subset_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search_in({}, "加班") == []

    def test_search_in_empty_keyword_returns_empty(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        assert s.search_in(sample_entries, "") == []

    def test_search_in_fuzzy_fallback(
        self, sample_entries: dict[int, MemeEntry]
    ) -> None:
        """子集上无精确命中时走 LCS 模糊回退。"""
        s = KeywordSearcher(MockMetadataStore(sample_entries))
        subset = {1: sample_entries[1]}  # "一只猫在跳起来抓蝴蝶哈哈哈"
        results = s.search_in(subset, "猫抓蝴蝶")
        assert len(results) == 1
        assert results[0].entry_id == 1
        assert results[0].similarity == 100.0

    def test_search_in_respects_limit(self) -> None:
        """search_in 同样按 _limit 截断。"""
        entries = {
            i: MemeEntry(id=i, image_path=f"m_{i}.jpg", text=f"加班第{i}天")
            for i in range(1, 16)
        }
        s = KeywordSearcher(MockMetadataStore(entries), limit=5)
        assert len(s.search_in(entries, "加班")) == 5

    def test_search_equals_search_in_on_full_entries(
        self, sample_entries: dict[int, MemeEntry], searcher: KeywordSearcher
    ) -> None:
        """search(keyword) 在全集结果应等于 search_in(全集, keyword)。"""
        keyword = "加班"
        full = searcher._metadata_store.get_all_entries()
        assert searcher.search(keyword) == searcher.search_in(full, keyword)
