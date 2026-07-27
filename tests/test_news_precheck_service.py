#!/usr/bin/env python3
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))

from market_data.news_precheck import (  # noqa: E402
    NewsPrecheckConfig,
    build_candidate_news_prompt,
    fetch_candidate_news_records,
    format_cached_news_records,
    parse_chat_completion_content,
    parse_candidate_news_record,
    repair_cached_news_record,
    request_candidate_news,
)
import market_data.news_precheck as news_precheck  # noqa: E402


class NewsPrecheckServiceTests(unittest.TestCase):
    def test_config_is_optional_but_rejects_partial_values(self):
        self.assertIsNone(NewsPrecheckConfig.from_mapping({}))
        with self.assertRaisesRegex(ValueError, "incomplete_news_precheck_config"):
            NewsPrecheckConfig.from_mapping({"DASHBOARD_NEWS_MODEL": "test-model"})

    def test_parser_requires_an_explicit_sentiment_label(self):
        positive = parse_candidate_news_record(
            {"code": "600000", "name": "测试"},
            "- 600000 测试：订单增长（利好）",
            fetched_at="2026-07-17T10:00:00+08:00",
        )
        ambiguous = parse_candidate_news_record(
            {"code": "600001", "name": "测试二"},
            "既有利好也有利空，需要继续核验",
            fetched_at="2026-07-17T10:00:00+08:00",
        )

        self.assertTrue(positive["available"])
        self.assertEqual(positive["tone"], "positive")
        self.assertFalse(ambiguous["available"])
        self.assertEqual(ambiguous["error"], "unclassified_response")

    def test_parser_understands_conclusion_and_ignores_negated_tone_terms(self):
        content = (
            "**代码 名称：** - 000595.SZ 新能股份：控股股东累计增持475万股，"
            "构成近期重大利好。"
            "（此为最近3天明确重大公告及市场反应，无其他重大利空或中性消息。）"
        )

        record = parse_candidate_news_record(
            {"code": "000595.SZ", "name": "新能股份"},
            content,
            fetched_at="2026-07-26T18:35:49+08:00",
        )

        self.assertTrue(record["available"])
        self.assertEqual(record["tone"], "positive")
        self.assertEqual(record["tone_label"], "利好")
        self.assertEqual(
            record["summary"],
            "000595.SZ 新能股份：控股股东累计增持475万股，构成近期重大利好。",
        )

    def test_cached_unclassified_summary_is_repaired_without_changing_fetch_time(self):
        cached = {
            "checked": True,
            "available": False,
            "tone": "neutral",
            "tone_label": "未识别",
            "summary": "增持构成重大利好，无其他利空或中性消息。",
            "fetched_at": "2026-07-26T18:35:49+08:00",
            "error": "unclassified_response",
            "provider": "消息面预检模型",
        }

        repaired = repair_cached_news_record(cached)

        self.assertTrue(repaired["available"])
        self.assertEqual(repaired["tone_label"], "利好")
        self.assertEqual(repaired["fetched_at"], cached["fetched_at"])
        self.assertEqual(repaired["provider"], "消息面预检模型")
        self.assertTrue(repaired["repaired_locally"])
        self.assertEqual(repaired["error"], "")
        self.assertNotIn("source_scope", repaired)

    def test_parser_removes_markdown_decoration_from_summary(self):
        record = parse_candidate_news_record(
            {"code": "600000.SH", "name": "测试"},
            "**600000.SH 测试：最近3天无明确重大消息（中性）**",
            fetched_at="2026-07-17T10:00:00+08:00",
        )

        self.assertEqual(
            record["summary"],
            "600000.SH 测试：最近3天无明确重大消息（中性）",
        )

    def test_parser_removes_citations_and_process_notes_after_tone(self):
        record = parse_candidate_news_record(
            {"code": "301234.SZ", "name": "测试"},
            "拟收购目标公司100%股权，切入芯片领域（利好） "
            "[[1]](https://example.com/a) [2](https://example.com/b) "
            "（公告发布后3天无其他重大消息）",
            fetched_at="2026-07-17T10:00:00+08:00",
        )

        self.assertEqual(record["summary"], "拟收购目标公司100%股权，切入芯片领域（利好）")

    def test_cached_formatter_adds_stock_identity_to_compact_summary(self):
        formatted = format_cached_news_records([{
            "code": "600000.SH",
            "name": "测试",
            "available": True,
            "summary": "未发现明确重大消息（中性）",
        }])

        self.assertIn("600000.SH 测试：未发现明确重大消息（中性）", formatted)

    def test_chat_parser_accepts_json_and_sse_responses(self):
        json_content = parse_chat_completion_content(
            '{"choices":[{"message":{"content":"消息稳定（中性）"}}]}'
        )
        sse_content = parse_chat_completion_content(
            'data: {"choices":[{"delta":{"content":"订单"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"增长（利好）"}}]}\n\n'
            "data: [DONE]\n\n"
        )

        self.assertEqual(json_content, "消息稳定（中性）")
        self.assertEqual(sse_content, "订单增长（利好）")

    def test_fetch_preserves_order_and_degrades_individual_failures(self):
        config = NewsPrecheckConfig(
            base_url="https://news.example/v1",
            api_key="secret",
            model="test-model",
            concurrency=2,
        )

        def requester(candidate, _config):
            if candidate["code"] == "600001":
                raise TimeoutError("timeout")
            return f"- {candidate['code']} {candidate['name']}：最近3天无明确重大消息（中性）"

        records = fetch_candidate_news_records(
            [
                {"code": "600000", "name": "甲"},
                {"code": "600001", "name": "乙"},
                {"code": "600002", "name": "丙"},
            ],
            config,
            requester=requester,
            now=datetime(2026, 7, 17, 10, 0, 0),
        )

        self.assertEqual([record["code"] for record in records], ["600000", "600001", "600002"])
        self.assertTrue(records[0]["available"])
        self.assertFalse(records[1]["available"])
        self.assertEqual(records[1]["error"], "request_TimeoutError")
        self.assertEqual(records[2]["tone"], "neutral")
        self.assertIn("扫描阶段缓存", format_cached_news_records(records))

    def test_request_uses_central_model_api_with_search_mode(self):
        config = NewsPrecheckConfig(
            base_url="https://news.example/v1",
            api_key="secret",
            model="gpt-5.6-sol",
            api_mode="auto",
            max_requests=1,
        )
        captured = {}
        original_request_model = news_precheck.request_model
        try:
            def fake_request(model_request, api_key, **kwargs):
                captured["request"] = model_request
                captured["api_key"] = api_key
                captured["kwargs"] = kwargs
                return types.SimpleNamespace(content="- 600000 测试：订单增长（利好）")

            news_precheck.request_model = fake_request
            content = request_candidate_news({"code": "600000", "name": "测试"}, config)
        finally:
            news_precheck.request_model = original_request_model

        self.assertIn("订单增长", content)
        self.assertEqual(captured["request"].api_mode, "responses")
        self.assertEqual(captured["request"].payload["tools"], [{"type": "web_search"}])
        self.assertNotIn("max_output_tokens", captured["request"].payload)
        self.assertEqual(captured["kwargs"]["timeout"], 45)

    def test_prompt_separates_verified_news_from_xueqiu_and_x_sentiment(self):
        prompt = build_candidate_news_prompt({"code": "600000", "name": "测试"})

        self.assertIn("雪球与X/Twitter公开内容", prompt)
        self.assertIn("不得把未经证实的帖子当作公司事实", prompt)
        self.assertIn("事件：核心事实；影响：对公司的直接影响；舆情：", prompt)

        record = parse_candidate_news_record(
            {"code": "600000", "name": "测试"},
            "事件：订单增长；影响：提升收入；舆情：雪球偏多，X讨论有限（利好）",
            fetched_at="2026-07-26T16:00:00+08:00",
        )
        self.assertEqual(
            record["source_scope"],
            ["disclosures", "financial_media", "xueqiu", "x"],
        )
        self.assertIn("舆情：雪球偏多，X讨论有限", record["summary"])

    def test_grok_responses_model_adds_direct_x_search(self):
        config = NewsPrecheckConfig(
            base_url="https://news.example/v1",
            api_key="secret",
            model="grok-4.5",
            api_mode="auto",
            max_requests=1,
        )
        captured = {}
        original_request_model = news_precheck.request_model
        try:
            def fake_request(model_request, _api_key, **_kwargs):
                captured["request"] = model_request
                return types.SimpleNamespace(
                    content="事件：订单增长；影响：提升收入；舆情：雪球偏多，X讨论有限（利好）"
                )

            news_precheck.request_model = fake_request
            request_candidate_news({"code": "600000", "name": "测试"}, config)
        finally:
            news_precheck.request_model = original_request_model

        self.assertEqual(captured["request"].api_mode, "responses")
        self.assertEqual(
            captured["request"].payload["tools"],
            [{"type": "web_search"}, {"type": "x_search"}],
        )


if __name__ == "__main__":
    unittest.main()
