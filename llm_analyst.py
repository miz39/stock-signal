"""
LLM review and deep analysis for BUY candidates.
Supports OpenAI and Anthropic Claude as LLM providers.

Two analysis modes:
- "filter": Quick approve/reject (lightweight, all candidates)
- "deep": Detailed analysis report (heavier, top candidates only)
"""

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from nikkei225 import NIKKEI_225, get_sector

logger = logging.getLogger("signal")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# --- LLM Provider abstraction ---

class LLMProvider:
    """Base class for LLM providers."""

    def chat(self, system_prompt: str, user_prompt: str, model: str,
             temperature: float = 0.3, max_tokens: int = 200, json_mode: bool = False) -> str:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    def chat(self, system_prompt, user_prompt, model="gpt-4o-mini",
             temperature=0.3, max_tokens=200, json_mode=False):
        from openai import OpenAI

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")

        client = OpenAI(api_key=api_key)
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()


class AnthropicProvider(LLMProvider):
    def chat(self, system_prompt, user_prompt, model="claude-sonnet-4-5-20250929",
             temperature=0.3, max_tokens=200, json_mode=False):
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)

        user_content = user_prompt
        if json_mode:
            user_content += "\n\nRespond with valid JSON only."

        response = client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.content[0].text.strip()


def _get_llm_provider(config: dict) -> tuple:
    """Get the LLM provider and model based on config."""
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "openai")
    strat = config.get("strategy", {})

    if provider_name == "anthropic":
        provider = AnthropicProvider()
        model = strat.get("anthropic_model", "claude-sonnet-4-5-20250929")
    else:
        provider = OpenAIProvider()
        model = strat.get("openai_model", "gpt-4o-mini")

    return provider, model


# --- Context building (shared) ---

def _load_trade_history() -> list:
    """Load closed trades from trades.json for reflection memory."""
    trades_file = os.path.join(_BASE_DIR, "trades.json")
    if not os.path.exists(trades_file):
        return []
    try:
        with open(trades_file, "r") as f:
            trades = json.load(f)
        return [t for t in trades if t.get("status") == "closed"]
    except (json.JSONDecodeError, IOError):
        return []


def _find_similar_trades(ticker: str, signal_result: dict, closed_trades: list, max_results: int = 5) -> list:
    """Find similar past trades for reflection (same ticker or similar RSI/price pattern)."""

    sector = get_sector(ticker)
    rsi = signal_result.get("rsi", 55)

    scored = []
    for t in closed_trades:
        score = 0.0
        if t["ticker"] == ticker:
            score += 3.0
        if get_sector(t["ticker"]) == sector:
            score += 1.0
        entry = t.get("entry_price", 0)
        price = signal_result.get("price", 0)
        if entry > 0 and price > 0:
            ratio = min(entry, price) / max(entry, price)
            if ratio > 0.8:
                score += 0.5

        if score > 0:
            pnl_pct = 0
            if t.get("entry_price") and t.get("exit_price"):
                pnl_pct = round((t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100, 1)
            days_held = 0
            if t.get("entry_date") and t.get("exit_date"):
                try:
                    d1 = date.fromisoformat(t["entry_date"])
                    d2 = date.fromisoformat(t["exit_date"])
                    days_held = (d2 - d1).days
                except (ValueError, TypeError):
                    pass
            scored.append({
                "ticker": t["ticker"],
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "pnl": t.get("pnl", 0),
                "pnl_pct": pnl_pct,
                "days_held": days_held,
                "relevance": score,
            })

    scored.sort(key=lambda x: x["relevance"], reverse=True)
    return scored[:max_results]


def _build_price_summary(df: pd.DataFrame) -> dict:
    """Build recent price action summary from DataFrame."""
    close = df["Close"]
    n = len(close)

    summary = {}
    if n >= 5:
        summary["return_5d"] = round((close.iloc[-1] / close.iloc[-5] - 1) * 100, 2)
    if n >= 20:
        summary["return_20d"] = round((close.iloc[-1] / close.iloc[-20] - 1) * 100, 2)
    if n >= 60:
        summary["return_60d"] = round((close.iloc[-1] / close.iloc[-60] - 1) * 100, 2)

    if n >= 20:
        daily_returns = close.pct_change().dropna()
        summary["volatility_20d"] = round(float(daily_returns.iloc[-20:].std() * 100), 2)

    if "Volume" in df.columns and n >= 20:
        vol = df["Volume"]
        avg_vol_20 = float(vol.iloc[-20:].mean())
        avg_vol_5 = float(vol.iloc[-5:].mean())
        if avg_vol_20 > 0:
            summary["volume_trend"] = round(avg_vol_5 / avg_vol_20, 2)

    if n >= 200:
        high_52w = float(close.iloc[-250:].max()) if n >= 250 else float(close.max())
        low_52w = float(close.iloc[-250:].min()) if n >= 250 else float(close.min())
        current = float(close.iloc[-1])
        if high_52w > 0:
            summary["pct_from_52w_high"] = round((current / high_52w - 1) * 100, 1)
        if low_52w > 0:
            summary["pct_from_52w_low"] = round((current / low_52w - 1) * 100, 1)

    return summary


def _build_prompt(ticker: str, signal_result: dict, price_summary: dict,
                  similar_trades: list, portfolio_context: Optional[dict] = None) -> str:
    """Build the LLM review prompt."""
    name = NIKKEI_225.get(ticker, ticker)
    sector = get_sector(ticker)

    lines = [
        f"# BUY Candidate Review: {name} ({ticker})",
        f"Sector: {sector}",
        "",
        "## Technical Indicators",
        f"- Price: ¥{signal_result.get('price', 0):,.0f}",
        f"- RSI(14): {signal_result.get('rsi', 0):.1f}",
        f"- SMA25: ¥{signal_result.get('sma_short', 0):,.0f}",
        f"- SMA100: ¥{signal_result.get('sma_long', 0):,.0f}",
        f"- SMA200: ¥{signal_result.get('sma_trend', 0):,.0f}",
        f"- SMA25 Slope (5d): {signal_result.get('sma_slope', 0):+.2f}%",
        f"- Composite Score: {signal_result.get('composite_score', 0):.3f}",
        f"- Signal Reason: {signal_result.get('reason', '')}",
    ]

    if signal_result.get("tv_score") is not None:
        tv = signal_result["tv_score"]
        tv_label = "BUY" if tv >= 15/26 else ("NEUTRAL" if tv >= 11/26 else "SELL")
        lines.append(f"- TradingView TA: {tv_label} ({tv:.3f})")

    # Agent analysis summary (if available)
    agent_analysis = signal_result.get("agent_analysis")
    if agent_analysis:
        lines.append("")
        lines.append("## Multi-Agent Analysis")
        lines.append(f"- Overall Signal: {agent_analysis.get('signal', 'N/A')} (score: {agent_analysis.get('total_score', 0):.2f})")
        lines.append(f"- Confidence: {agent_analysis.get('confidence', 0)}%")
        for reason in agent_analysis.get("reasons_summary", []):
            lines.append(f"- {reason}")

    lines.append("")
    lines.append("## Recent Price Action")
    for k, v in price_summary.items():
        label = k.replace("_", " ").title()
        lines.append(f"- {label}: {v}")

    if portfolio_context:
        lines.append("")
        lines.append("## Portfolio Context")
        lines.append(f"- Open Positions: {portfolio_context.get('open_count', 0)}")
        lines.append(f"- Same Sector Positions: {portfolio_context.get('same_sector_count', 0)}")
        lines.append(f"- Cash Available: ¥{portfolio_context.get('cash', 0):,.0f}")
        regime = portfolio_context.get("market_regime", "neutral")
        lines.append(f"- Market Regime: {regime}")

    if similar_trades:
        lines.append("")
        lines.append("## Past Trade History (Reflection)")
        wins = sum(1 for t in similar_trades if t["pnl"] > 0)
        losses = len(similar_trades) - wins
        lines.append(f"Similar past trades: {wins}W / {losses}L")
        for t in similar_trades:
            outcome = "WIN" if t["pnl"] > 0 else "LOSS"
            lines.append(
                f"- {t['ticker']}: {outcome} {t['pnl_pct']:+.1f}% "
                f"(held {t['days_held']}d, PnL ¥{t['pnl']:,.0f})"
            )

    prompt = "\n".join(lines)
    return prompt


# --- Filter mode (lightweight approve/reject) ---

SYSTEM_PROMPT = """You are a disciplined swing trade analyst reviewing BUY candidates for a Nikkei 225 automated trading system.

Your role: Given technical indicators, price action, portfolio context, and past trade outcomes, decide whether to APPROVE or REJECT this BUY candidate.

Decision criteria:
- APPROVE: Strong technical setup + favorable context. The trade has a good risk/reward setup.
- REJECT: Red flags that the technical filter missed (e.g., overextended price, weak volume, poor past outcomes in similar setups, sector overexposure, bearish market regime).

Respond in this exact JSON format:
{"approved": true/false, "confidence": 0.0-1.0, "reason": "Brief explanation in Japanese (1-2 sentences)"}

Be conservative. When in doubt, REJECT. A missed opportunity costs nothing; a bad entry costs real money."""


def review_buy_candidate(
    ticker: str,
    signal_result: dict,
    df: pd.DataFrame,
    config: dict,
    portfolio_context: Optional[dict] = None,
) -> dict:
    """
    Review a BUY candidate using LLM (filter mode).

    Returns:
        dict: {"approved": bool, "confidence": float, "reason": str, "skipped": bool}
    """
    try:
        provider, model = _get_llm_provider(config)
    except Exception:
        provider, model = OpenAIProvider(), config.get("strategy", {}).get("openai_model", "gpt-4o-mini")

    # Check API key availability
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "openai")
    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        logger.warning(f"{provider_name.upper()} API key not set, skipping LLM review")
        return {"approved": True, "confidence": 0.0, "reason": "LLM review skipped (no API key)", "skipped": True}

    # Gather context
    closed_trades = _load_trade_history()
    similar_trades = _find_similar_trades(ticker, signal_result, closed_trades)
    price_summary = _build_price_summary(df)

    user_prompt = _build_prompt(ticker, signal_result, price_summary, similar_trades, portfolio_context)

    try:
        content = provider.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=model,
            temperature=0.3,
            max_tokens=200,
            json_mode=True,
        )

        result = json.loads(content)
        approved = bool(result.get("approved", True))
        confidence = float(result.get("confidence", 0.5))
        reason = str(result.get("reason", ""))

        logger.info(f"  LLM Review {ticker}: {'APPROVED' if approved else 'REJECTED'} "
                    f"(confidence={confidence:.2f}) - {reason}")

        return {
            "approved": approved,
            "confidence": min(max(confidence, 0.0), 1.0),
            "reason": reason,
            "skipped": False,
        }

    except Exception as e:
        logger.warning(f"LLM review failed for {ticker}: {e}")
        return {"approved": True, "confidence": 0.0, "reason": f"LLM review error: {e}", "skipped": True}


# --- Deep analysis mode ---

DEEP_ANALYSIS_SYSTEM_PROMPT = """あなたは日経225銘柄のスイングトレード専門アナリストです。
テクニカル指標、ファンダメンタル、マルチエージェント分析結果、過去トレード履歴を総合的に分析し、詳細な売買判断レポートを作成してください。

以下のJSON形式で回答してください:
{
  "judgment": "BUY" or "HOLD" or "SELL",
  "conviction": 1-10,
  "buy_reasons": ["理由1", "理由2", "理由3"],
  "risk_factors": ["リスク1", "リスク2", "リスク3"],
  "scenarios": {
    "bull": "強気シナリオ（想定上昇幅とトリガー）",
    "base": "ベースシナリオ（最も蓋然性の高い展開）",
    "bear": "弱気シナリオ（想定下落幅とトリガー）"
  },
  "entry_range": {"low": 0, "high": 0},
  "stop_loss_rationale": "損切りラインの根拠",
  "take_profit_rationale": "利確ラインの根拠",
  "summary": "1行サマリー（20文字以内）"
}

分析の注意点:
- 保守的に判断する。疑わしい場合はHOLD
- 過去の類似トレードの勝敗パターンを重視する
- 市場レジーム（Bull/Bear/Neutral）を考慮する
- ファンダメンタルデータが不足している場合、その旨を明記し信頼度を下げる"""


def deep_analyze_candidate(
    ticker: str,
    signal_result: dict,
    df: pd.DataFrame,
    config: dict,
    portfolio_context: Optional[dict] = None,
) -> dict:
    """
    Perform deep analysis on a BUY candidate.

    Returns:
        dict with judgment, conviction, buy_reasons, risk_factors, scenarios, etc.
    """
    try:
        provider, model = _get_llm_provider(config)
    except Exception:
        return {"error": "LLM provider not available", "skipped": True}

    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "openai")
    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        return {"error": f"{provider_name} API key not set", "skipped": True}

    closed_trades = _load_trade_history()
    similar_trades = _find_similar_trades(ticker, signal_result, closed_trades)
    price_summary = _build_price_summary(df)

    user_prompt = _build_prompt(ticker, signal_result, price_summary, similar_trades, portfolio_context)

    try:
        content = provider.chat(
            system_prompt=DEEP_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=model,
            temperature=0.3,
            max_tokens=1000,
            json_mode=True,
        )

        result = json.loads(content)
        logger.info(f"  Deep Analysis {ticker}: {result.get('judgment', 'N/A')} "
                    f"(conviction={result.get('conviction', 0)}) - {result.get('summary', '')}")
        result["skipped"] = False
        return result

    except Exception as e:
        logger.warning(f"Deep analysis failed for {ticker}: {e}")
        return {"error": str(e), "skipped": True}


# --- Main review flow ---

def review_candidates(
    buy_signals: list,
    dfs: dict,
    config: dict,
    portfolio_context: Optional[dict] = None,
    max_review: int = 10,
) -> list:
    """
    Review top BUY candidates and filter by LLM approval.
    Optionally performs deep analysis on top candidates.

    Args:
        buy_signals: List of signal dicts (already sorted by composite_score)
        dfs: Dict mapping ticker -> DataFrame (cached from scan)
        config: Full config dict
        portfolio_context: Portfolio state for context
        max_review: Max number of candidates to review (to control API cost)

    Returns:
        list: buy_signals with 'llm_review' and optionally 'deep_analysis' fields added
    """
    candidates = buy_signals[:max_review]
    approved = []
    rejected = []

    for sig in candidates:
        ticker = sig["ticker"]
        df = dfs.get(ticker)
        if df is None:
            sig["llm_review"] = {"approved": True, "confidence": 0.0, "reason": "No DataFrame cached", "skipped": True}
            approved.append(sig)
            continue

        review = review_buy_candidate(ticker, sig, df, config, portfolio_context)
        sig["llm_review"] = review

        if review["approved"]:
            approved.append(sig)
        else:
            rejected.append(sig)

    # Deep analysis for top approved candidates (if enabled)
    llm_cfg = config.get("llm", {})
    if llm_cfg.get("deep_analysis_enabled", False):
        deep_n = llm_cfg.get("deep_analysis_top_n", 5)
        for sig in approved[:deep_n]:
            ticker = sig["ticker"]
            df = dfs.get(ticker)
            if df is not None:
                deep = deep_analyze_candidate(ticker, sig, df, config, portfolio_context)
                sig["deep_analysis"] = deep

    # Add remaining (not reviewed) candidates back
    remaining = buy_signals[max_review:]
    for sig in remaining:
        sig["llm_review"] = {"approved": True, "confidence": 0.0, "reason": "Not reviewed (rank too low)", "skipped": True}

    logger.info(f"LLM Review: {len(approved)} approved, {len(rejected)} rejected out of {len(candidates)} reviewed")

    return approved + remaining
