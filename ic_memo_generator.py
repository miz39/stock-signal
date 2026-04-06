"""
Investment Committee (IC) Memo Generator.

Generates detailed IC memos for top BUY candidates using LLM.
Includes DCF estimate, comparable analysis, scenario analysis,
risk assessment, and position sizing recommendation.
"""

import json
import logging
import os
from datetime import date, datetime

from nikkei225 import NIKKEI_225, get_sector

logger = logging.getLogger("signal")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEMOS_DIR = os.path.join(_BASE_DIR, "docs", "memos")


IC_MEMO_SYSTEM_PROMPT = """あなたは日経225銘柄の投資委員会（IC）メモを作成するシニアアナリストです。
テクニカル指標、ファンダメンタル情報、マルチエージェント分析結果、過去のトレード履歴を総合的に評価し、
投資判断のための包括的なメモを作成してください。

以下の正確なJSON形式で回答してください（他のテキストは一切含めないでください）:
{
  "executive_summary": {
    "recommendation": "BUY" or "HOLD" or "PASS",
    "conviction": 1-10,
    "target_price": <number>,
    "upside_pct": <number>,
    "key_catalysts": ["catalyst1", "catalyst2", "catalyst3"]
  },
  "valuation": {
    "dcf_estimate": {"fair_value": <number>, "method": "簡易DCF or PER基準", "notes": "..."},
    "comps": {"sector_avg_per": <number or null>, "stock_per": <number or null>, "discount_to_sector": "..."},
    "summary": "バリュエーション総括（2-3文）"
  },
  "fundamental": {
    "revenue_trend": "売上トレンドの分析",
    "profitability": "収益性の分析",
    "financial_health": "財務健全性",
    "dividend": "配当に関する情報",
    "next_earnings": "次の決算に関する注意点"
  },
  "technical": {
    "trend": "トレンド判定と根拠",
    "momentum": "モメンタム分析",
    "support_resistance": "サポート・レジスタンス水準",
    "volume": "出来高分析"
  },
  "risk_analysis": {
    "market_risk": "市場リスク",
    "stock_risk": "個別リスク",
    "earnings_risk": "決算リスク",
    "stop_loss_rationale": "ストップロス設定の根拠"
  },
  "scenarios": {
    "bull": {"trigger": "...", "target": <number>, "probability": "30%"},
    "base": {"trigger": "...", "target": <number>, "probability": "50%"},
    "bear": {"trigger": "...", "target": <number>, "probability": "20%"}
  },
  "position_sizing": {
    "recommended_shares": <number>,
    "stop_loss": <number>,
    "risk_amount": <number>
  },
  "agent_scores": {
    "technical": <0.0-1.0>,
    "fundamental": <0.0-1.0>,
    "sentiment": <0.0-1.0>,
    "risk": <0.0-1.0>
  }
}

分析の注意点:
- バリュエーション分析（実データ）セクションが提供されている場合、DCF理論株価やComps比較は実計算値をそのまま使用すること。値を推測しないこと
- ファンダメンタルデータが限定的な場合はその旨を明記し、convictionを下げる
- target_priceはDCF理論株価が提供されていればそれをベースに設定、なければ現在株価から合理的な範囲内で設定
- scenariosの各probability合計は100%にする
- position_sizingは投資資金30万円、リスク許容2%を前提とする
- agent_scoresはマルチエージェント分析結果があればそれを反映、なければテクニカルデータから推定"""


def _build_ic_prompt(ticker: str, signal_result: dict, portfolio_context: dict = None,
                     similar_trades: list = None) -> str:
    """Build the IC memo prompt from signal data."""
    name = NIKKEI_225.get(ticker, ticker)
    sector = get_sector(ticker)

    lines = [
        f"# IC Memo Request: {name} ({ticker})",
        f"セクター: {sector}",
        f"日付: {date.today().isoformat()}",
        "",
        "## テクニカル指標",
        f"- 株価: ¥{signal_result.get('price', 0):,.0f}",
        f"- RSI(14): {signal_result.get('rsi', 0):.1f}",
        f"- SMA25: ¥{signal_result.get('sma_short', 0):,.0f}",
        f"- SMA100: ¥{signal_result.get('sma_long', 0):,.0f}",
        f"- SMA200: ¥{signal_result.get('sma_trend', 0):,.0f}",
        f"- SMA25傾き(5日): {signal_result.get('sma_slope', 0):+.2f}%",
        f"- 複合スコア: {signal_result.get('composite_score', 0):.3f}",
        f"- シグナル理由: {signal_result.get('reason', '')}",
    ]

    if signal_result.get("tv_score") is not None:
        tv = signal_result["tv_score"]
        tv_label = "BUY" if tv >= 15 / 26 else ("NEUTRAL" if tv >= 11 / 26 else "SELL")
        lines.append(f"- TradingView TA: {tv_label} ({tv:.3f})")

    # ADX
    if signal_result.get("adx") is not None:
        lines.append(f"- ADX: {signal_result['adx']:.1f}")

    # Agent analysis
    agent = signal_result.get("agent_analysis")
    if agent:
        lines.append("")
        lines.append("## マルチエージェント分析")
        lines.append(f"- 総合シグナル: {agent.get('signal', 'N/A')} (スコア: {agent.get('total_score', 0):.2f})")
        lines.append(f"- 信頼度: {agent.get('confidence', 0)}%")
        for r in agent.get("reasons_summary", []):
            lines.append(f"  - {r}")

        # Individual agent scores
        agents_detail = agent.get("agents", {})
        if agents_detail:
            lines.append("")
            lines.append("## 個別エージェントスコア")
            for agent_name, agent_data in agents_detail.items():
                if isinstance(agent_data, dict):
                    lines.append(f"- {agent_name}: {agent_data.get('score', 0):.2f} ({agent_data.get('signal', 'N/A')})")

    # Valuation analysis (real data from valuation agents)
    valuation = signal_result.get("valuation_analysis")
    if valuation:
        lines.append("")
        lines.append("## バリュエーション分析（実データ）")
        lines.append(f"- 総合判定: {valuation.get('signal_label', 'N/A')} (スコア: {valuation.get('total_score', 0):.2f})")
        if valuation.get("fair_value"):
            lines.append(f"- DCF 理論株価: ¥{valuation['fair_value']:,.0f}")
        if valuation.get("upside_pct") is not None:
            lines.append(f"- アップサイド: {valuation['upside_pct']:+.1f}%")
        for r in valuation.get("reasons_summary", []):
            lines.append(f"  - {r}")

        # Detailed valuation agent metrics
        for agent_detail in valuation.get("agents", []):
            if isinstance(agent_detail, dict):
                agent_name = agent_detail.get("agent", "")
                agent_metrics = agent_detail.get("metrics", {})
                if agent_metrics:
                    lines.append(f"")
                    lines.append(f"### {agent_name}")
                    lines.append(f"- スコア: {agent_detail.get('score', 0):.2f}")
                    for key, val in agent_metrics.items():
                        if key not in ("peers", "fcf_projected", "fcf_history",
                                       "sensitivity_table", "scenarios"):
                            lines.append(f"- {key}: {val}")

    # Deep analysis
    deep = signal_result.get("deep_analysis")
    if deep and not deep.get("skipped"):
        lines.append("")
        lines.append("## ディープ分析結果")
        lines.append(f"- 判定: {deep.get('judgment', 'N/A')}")
        lines.append(f"- 確信度: {deep.get('conviction', 0)}")
        for reason in deep.get("buy_reasons", []):
            lines.append(f"- 買い理由: {reason}")
        for risk in deep.get("risk_factors", []):
            lines.append(f"- リスク: {risk}")

    # LLM review
    llm_review = signal_result.get("llm_review")
    if llm_review and not llm_review.get("skipped"):
        lines.append("")
        lines.append("## LLMレビュー")
        status = "承認" if llm_review.get("approved") else "却下"
        lines.append(f"- 判定: {status} (信頼度: {llm_review.get('confidence', 0):.2f})")
        lines.append(f"- 理由: {llm_review.get('reason', '')}")

    # Portfolio context
    if portfolio_context:
        lines.append("")
        lines.append("## ポートフォリオ状況")
        lines.append(f"- 保有銘柄数: {portfolio_context.get('open_count', 0)}")
        lines.append(f"- 同セクター保有: {portfolio_context.get('same_sector_count', 0)}")
        lines.append(f"- 現金残高: ¥{portfolio_context.get('cash', 0):,.0f}")
        lines.append(f"- 市場レジーム: {portfolio_context.get('market_regime', 'neutral')}")

    # Similar trades
    if similar_trades:
        lines.append("")
        lines.append("## 類似過去トレード")
        wins = sum(1 for t in similar_trades if t.get("pnl", 0) > 0)
        losses = len(similar_trades) - wins
        lines.append(f"類似トレード: {wins}勝 / {losses}敗")
        for t in similar_trades:
            outcome = "勝" if t.get("pnl", 0) > 0 else "負"
            lines.append(
                f"- {t['ticker']}: {outcome} {t.get('pnl_pct', 0):+.1f}% "
                f"(保有{t.get('days_held', 0)}日, P&L ¥{t.get('pnl', 0):,.0f})"
            )

    return "\n".join(lines)


def _get_cache_path(ticker: str, memo_date: str = None) -> str:
    """Get cache file path for a memo."""
    if memo_date is None:
        memo_date = date.today().isoformat()
    os.makedirs(MEMOS_DIR, exist_ok=True)
    return os.path.join(MEMOS_DIR, f"{memo_date}_{ticker.replace('.', '_')}.json")


def _load_cached_memo(ticker: str, memo_date: str = None) -> dict:
    """Load cached IC memo if available."""
    path = _get_cache_path(ticker, memo_date)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return None


def _save_memo(ticker: str, memo: dict, memo_date: str = None) -> str:
    """Save IC memo to cache. Returns the file path."""
    path = _get_cache_path(ticker, memo_date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(memo, f, ensure_ascii=False, indent=2)
    return path


def _cleanup_old_memos(cache_days: int = 7):
    """Remove memo cache files older than cache_days."""
    if not os.path.exists(MEMOS_DIR):
        return
    cutoff = date.today().toordinal() - cache_days
    for fname in os.listdir(MEMOS_DIR):
        if not fname.endswith(".json"):
            continue
        # Parse date from filename: YYYY-MM-DD_TICKER.json
        try:
            date_str = fname[:10]
            file_date = date.fromisoformat(date_str)
            if file_date.toordinal() < cutoff:
                os.remove(os.path.join(MEMOS_DIR, fname))
                logger.debug(f"Removed old memo cache: {fname}")
        except (ValueError, IndexError):
            pass


def generate_ic_memo(
    ticker: str,
    signal_result: dict,
    config: dict,
    portfolio_context: dict = None,
    similar_trades: list = None,
) -> dict:
    """
    Generate an IC memo for a single ticker.

    Returns:
        dict: IC memo data, or {"error": ..., "skipped": True} on failure
    """
    from llm_analyst import _get_llm_provider

    memo_date = date.today().isoformat()

    # Check cache
    cached = _load_cached_memo(ticker, memo_date)
    if cached:
        logger.info(f"  IC Memo {ticker}: loaded from cache")
        return cached

    # Get LLM provider
    try:
        provider, model = _get_llm_provider(config)
    except Exception as e:
        return {"error": f"LLM provider not available: {e}", "skipped": True}

    # Check API key
    llm_cfg = config.get("llm", {})
    provider_name = llm_cfg.get("provider", "openai")
    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    if not api_key:
        return {"error": f"{provider_name} API key not set", "skipped": True}

    user_prompt = _build_ic_prompt(ticker, signal_result, portfolio_context, similar_trades)

    try:
        content = provider.chat(
            system_prompt=IC_MEMO_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=model,
            temperature=0.3,
            max_tokens=2000,
            json_mode=True,
        )

        memo = json.loads(content)

        # Add metadata
        name = NIKKEI_225.get(ticker, ticker)
        memo["ticker"] = ticker
        memo["name"] = name
        memo["date"] = memo_date
        memo["sector"] = get_sector(ticker)
        memo["current_price"] = signal_result.get("price", 0)
        memo["composite_score"] = signal_result.get("composite_score", 0)

        # Save cache
        path = _save_memo(ticker, memo, memo_date)
        logger.info(f"  IC Memo {ticker}: {memo.get('executive_summary', {}).get('recommendation', 'N/A')} "
                     f"(conviction={memo.get('executive_summary', {}).get('conviction', 0)}) -> {path}")

        return memo

    except json.JSONDecodeError as e:
        logger.warning(f"IC Memo JSON parse failed for {ticker}: {e}")
        return {"error": f"JSON parse error: {e}", "skipped": True}
    except Exception as e:
        logger.warning(f"IC Memo generation failed for {ticker}: {e}")
        return {"error": str(e), "skipped": True}


def generate_ic_memos(
    buy_signals: list,
    config: dict,
    portfolio_context: dict = None,
) -> list:
    """
    Generate IC memos for top N approved BUY candidates.

    Args:
        buy_signals: List of signal dicts (with llm_review attached)
        config: Full config dict
        portfolio_context: Portfolio state

    Returns:
        list: buy_signals with 'ic_memo' field added to top candidates
    """
    from llm_analyst import _load_trade_history, _find_similar_trades

    ic_cfg = config.get("ic_memo", {})
    if not ic_cfg.get("enabled", False):
        logger.info("IC Memo generation disabled")
        return buy_signals

    top_n = ic_cfg.get("top_n", 5)
    cache_days = ic_cfg.get("cache_days", 7)

    # Cleanup old caches
    _cleanup_old_memos(cache_days)

    # Filter: only LLM-approved candidates
    candidates = []
    for sig in buy_signals:
        review = sig.get("llm_review", {})
        if review.get("approved", True) and not review.get("skipped", True):
            candidates.append(sig)
        elif review.get("skipped", True) and review.get("approved", True):
            # Skipped reviews are also OK (no API key = auto-approve)
            candidates.append(sig)

    candidates = candidates[:top_n]

    if not candidates:
        logger.info("IC Memo: No approved candidates to process")
        return buy_signals

    logger.info(f"Generating IC Memos for top {len(candidates)} candidates...")

    closed_trades = _load_trade_history()

    for sig in candidates:
        ticker = sig["ticker"]
        similar = _find_similar_trades(ticker, sig, closed_trades)
        memo = generate_ic_memo(ticker, sig, config, portfolio_context, similar)
        sig["ic_memo"] = memo

    memo_count = sum(1 for s in candidates if s.get("ic_memo") and not s["ic_memo"].get("skipped"))
    logger.info(f"IC Memos generated: {memo_count}/{len(candidates)}")

    return buy_signals


def load_all_memos(memo_date: str = None) -> list:
    """Load all IC memos for a given date (for dashboard use)."""
    if memo_date is None:
        memo_date = date.today().isoformat()

    if not os.path.exists(MEMOS_DIR):
        return []

    memos = []
    prefix = memo_date
    for fname in sorted(os.listdir(MEMOS_DIR)):
        if fname.startswith(prefix) and fname.endswith(".json"):
            path = os.path.join(MEMOS_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    memo = json.load(f)
                    if not memo.get("skipped"):
                        memos.append(memo)
            except (json.JSONDecodeError, IOError):
                pass

    return memos
