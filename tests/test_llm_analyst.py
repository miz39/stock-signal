"""Tests for llm_analyst.py — context building and review flow."""
from unittest.mock import patch, MagicMock
import json

import pandas as pd
import numpy as np
import pytest

from llm_analyst import (
    _build_price_summary,
    _find_similar_trades,
    _build_prompt,
    review_buy_candidate,
    review_candidates,
    deep_analyze_candidate,
    OpenAIProvider,
    AnthropicProvider,
    _get_llm_provider,
)


def _make_df(n=250):
    prices = (1000 + np.arange(n) * 0.5).tolist()
    volumes = [100000.0] * n
    return pd.DataFrame({"Close": prices, "Volume": volumes})


def _make_signal():
    return {
        "price": 1124.5,
        "rsi": 55.0,
        "sma_short": 1120.0,
        "sma_long": 1080.0,
        "sma_trend": 1050.0,
        "sma_slope": 0.15,
        "composite_score": 0.72,
        "signal": "BUY",
        "reason": "test signal",
    }


def _make_config():
    return {
        "strategy": {
            "openai_model": "gpt-4o-mini",
            "llm_review_enabled": True,
            "llm_max_review": 5,
        },
        "llm": {
            "provider": "openai",
        },
    }


class TestBuildPriceSummary:
    def test_basic_summary(self):
        df = _make_df(250)
        summary = _build_price_summary(df)
        assert "return_5d" in summary
        assert "return_20d" in summary
        assert "volatility_20d" in summary

    def test_short_df(self):
        df = _make_df(10)
        summary = _build_price_summary(df)
        assert "return_5d" in summary
        assert "return_60d" not in summary


class TestFindSimilarTrades:
    def test_same_ticker_highest_relevance(self):
        sig = _make_signal()
        closed = [
            {"ticker": "7203.T", "entry_price": 1100, "exit_price": 1200, "pnl": 100, "entry_date": "2026-01-01", "exit_date": "2026-01-15"},
            {"ticker": "9984.T", "entry_price": 5000, "exit_price": 4800, "pnl": -200, "entry_date": "2026-02-01", "exit_date": "2026-02-10"},
        ]
        with patch("llm_analyst.get_sector", return_value="自動車"):
            results = _find_similar_trades("7203.T", sig, closed)
        assert len(results) > 0
        assert results[0]["ticker"] == "7203.T"

    def test_empty_trades(self):
        sig = _make_signal()
        results = _find_similar_trades("7203.T", sig, [])
        assert results == []


class TestBuildPrompt:
    def test_contains_ticker_info(self):
        sig = _make_signal()
        with patch("llm_analyst.NIKKEI_225", {"7203.T": "トヨタ自動車"}), \
             patch("llm_analyst.get_sector", return_value="自動車"):
            prompt = _build_prompt("7203.T", sig, {"return_5d": 1.5}, [])
        assert "トヨタ" in prompt
        assert "7203.T" in prompt
        assert "RSI" in prompt

    def test_includes_past_trades(self):
        sig = _make_signal()
        trades = [{"ticker": "7203.T", "pnl": 500, "pnl_pct": 5.0, "days_held": 10, "relevance": 3.0}]
        with patch("llm_analyst.NIKKEI_225", {"7203.T": "トヨタ"}), \
             patch("llm_analyst.get_sector", return_value="自動車"):
            prompt = _build_prompt("7203.T", sig, {}, trades)
        assert "Reflection" in prompt
        assert "WIN" in prompt

    def test_includes_agent_analysis(self):
        sig = _make_signal()
        sig["agent_analysis"] = {
            "signal": "BUY",
            "total_score": 0.85,
            "confidence": 72,
            "reasons_summary": ["【テクニカル】SMAゴールデンクロス"],
        }
        with patch("llm_analyst.NIKKEI_225", {"7203.T": "トヨタ"}), \
             patch("llm_analyst.get_sector", return_value="自動車"):
            prompt = _build_prompt("7203.T", sig, {}, [])
        assert "Multi-Agent Analysis" in prompt
        assert "BUY" in prompt


class TestReviewBuyCandidate:
    def test_no_api_key_skips(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
            result = review_buy_candidate("7203.T", sig, df, config)
        assert result["skipped"] is True
        assert result["approved"] is True

    def test_api_call_approved(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.chat.return_value = json.dumps({
            "approved": True, "confidence": 0.8, "reason": "Good setup"
        })

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False), \
             patch("llm_analyst._get_llm_provider", return_value=(mock_provider, "gpt-4o-mini")):
            result = review_buy_candidate("7203.T", sig, df, config)

        assert result["approved"] is True
        assert result["confidence"] == 0.8
        assert result["skipped"] is False

    def test_api_call_rejected(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.chat.return_value = json.dumps({
            "approved": False, "confidence": 0.7, "reason": "Overextended"
        })

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False), \
             patch("llm_analyst._get_llm_provider", return_value=(mock_provider, "gpt-4o-mini")):
            result = review_buy_candidate("7203.T", sig, df, config)

        assert result["approved"] is False
        assert result["skipped"] is False

    def test_api_error_falls_back(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.chat.side_effect = Exception("Connection error")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False), \
             patch("llm_analyst._get_llm_provider", return_value=(mock_provider, "gpt-4o-mini")):
            result = review_buy_candidate("7203.T", sig, df, config)

        assert result["approved"] is True
        assert result["skipped"] is True


class TestReviewCandidates:
    def test_filters_rejected(self):
        signals = [
            {**_make_signal(), "ticker": "7203.T"},
            {**_make_signal(), "ticker": "9984.T"},
        ]
        dfs = {"7203.T": _make_df(), "9984.T": _make_df()}
        config = _make_config()

        responses = iter([
            json.dumps({"approved": True, "confidence": 0.8, "reason": "Good"}),
            json.dumps({"approved": False, "confidence": 0.7, "reason": "Bad"}),
        ])

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.chat.side_effect = lambda **kwargs: next(responses)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False), \
             patch("llm_analyst._get_llm_provider", return_value=(mock_provider, "gpt-4o-mini")):
            result = review_candidates(signals, dfs, config, max_review=5)

        # Only approved one should remain
        assert len(result) == 1
        assert result[0]["ticker"] == "7203.T"
        assert result[0]["llm_review"]["approved"] is True


class TestDeepAnalysis:
    def test_deep_analysis_returns_structured(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()

        deep_result = {
            "judgment": "BUY",
            "conviction": 7,
            "buy_reasons": ["理由1", "理由2", "理由3"],
            "risk_factors": ["リスク1"],
            "scenarios": {"bull": "上昇", "base": "横ばい", "bear": "下落"},
            "summary": "買い推奨",
        }

        mock_provider = MagicMock(spec=OpenAIProvider)
        mock_provider.chat.return_value = json.dumps(deep_result)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False), \
             patch("llm_analyst._get_llm_provider", return_value=(mock_provider, "gpt-4o-mini")):
            result = deep_analyze_candidate("7203.T", sig, df, config)

        assert result["judgment"] == "BUY"
        assert result["conviction"] == 7
        assert result["skipped"] is False

    def test_deep_analysis_no_api_key(self):
        sig = _make_signal()
        df = _make_df()
        config = _make_config()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
            result = deep_analyze_candidate("7203.T", sig, df, config)

        assert result["skipped"] is True


class TestGetLLMProvider:
    def test_default_openai(self):
        config = _make_config()
        provider, model = _get_llm_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert model == "gpt-4o-mini"

    def test_anthropic_config(self):
        config = _make_config()
        config["llm"]["provider"] = "anthropic"
        provider, model = _get_llm_provider(config)
        assert isinstance(provider, AnthropicProvider)
