"""Valuation tools — DCF, comps, financial statements, sensitivity, IC memo."""

import json
import math

import numpy as np

from mcp.server.fastmcp import FastMCP


def _sanitize_for_json(obj):
    """Recursively replace NaN/Inf with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    return obj


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def run_full_analysis(ticker: str) -> str:
        """Run full Trading + Valuation analysis for a stock.

        Executes all 9 agents (4 trading + 5 valuation) and returns
        combined signal with detailed breakdown.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from agents.coordinator import full_analysis

        config = load_config()
        result = full_analysis(ticker, config)
        return json.dumps(_sanitize_for_json(result), ensure_ascii=False, default=str)

    @mcp.tool()
    def get_dcf_valuation(ticker: str) -> str:
        """Calculate DCF fair value for a stock.

        Uses Free Cash Flow projection (5 years) + terminal value.
        Returns fair value per share, upside/downside %, and key assumptions.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from data import fetch_stock_data
        from agents.dcf import analyze

        config = load_config()
        df = fetch_stock_data(ticker)
        result = analyze(df, config, ticker=ticker)
        return json.dumps(_sanitize_for_json(result), ensure_ascii=False, default=str)

    @mcp.tool()
    def get_comps_analysis(ticker: str) -> str:
        """Compare a stock's valuation to sector peers.

        Returns PER/PBR comparison vs sector median, with peer list.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from data import fetch_stock_data
        from agents.comps import analyze

        config = load_config()
        df = fetch_stock_data(ticker)
        result = analyze(df, config, ticker=ticker)
        return json.dumps(_sanitize_for_json(result), ensure_ascii=False, default=str)

    @mcp.tool()
    def get_financial_statements(ticker: str) -> str:
        """Get summarized financial statements (PL/BS/CF) for a stock.

        Returns key metrics from income statement, balance sheet,
        and cash flow statement with multi-year trends.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from data import fetch_stock_data
        from agents.three_statement import analyze

        config = load_config()
        df = fetch_stock_data(ticker)
        result = analyze(df, config, ticker=ticker)
        return json.dumps(_sanitize_for_json(result), ensure_ascii=False, default=str)

    @mcp.tool()
    def get_sensitivity_table(ticker: str) -> str:
        """Generate WACC x growth rate sensitivity table for a stock.

        Shows 3x3 matrix of fair values under different assumptions,
        plus Bull/Base/Bear scenarios.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from data import fetch_stock_data
        from agents.sensitivity import analyze

        config = load_config()
        df = fetch_stock_data(ticker)
        result = analyze(df, config, ticker=ticker)
        return json.dumps(_sanitize_for_json(result), ensure_ascii=False, default=str)

    @mcp.tool()
    def generate_ic_memo(ticker: str) -> str:
        """Generate an Investment Committee memo with real financial data.

        Uses actual DCF, comps, and financial statement analysis
        (not LLM-estimated values). LLM is used only for narrative generation.

        Args:
            ticker: Stock ticker (e.g. "7203.T" for Toyota)
        """
        from main import load_config
        from data import fetch_stock_data
        from agents.coordinator import full_analysis
        from ic_memo_generator import generate_ic_memo as _generate_ic_memo
        from strategy import generate_signal, compute_composite_score, fetch_tv_recommendation

        config = load_config()
        df = fetch_stock_data(ticker)

        # Generate signal data for IC memo
        sig = generate_signal(df, config)
        sig["ticker"] = ticker
        strat = config.get("strategy", {})
        tv_score = None
        if strat.get("tv_recommendation_enabled", False):
            tv_score = fetch_tv_recommendation(ticker)
            sig["tv_score"] = tv_score
        sig["composite_score"] = compute_composite_score(
            sig, df, strat.get("score_weights"),
            slope_days=strat.get("slope_days", 5),
            slope_blend=strat.get("slope_blend", 0.3),
            tv_score=tv_score,
        )

        # Run full analysis and attach to signal
        analysis = full_analysis(ticker, config, df=df)
        sig["agent_analysis"] = analysis.get("trading")
        sig["valuation_analysis"] = analysis.get("valuation")

        memo = _generate_ic_memo(ticker, sig, config)

        # Inject real valuation data into memo
        valuation = analysis.get("valuation", {})
        if not memo.get("skipped") and valuation:
            dcf_agent = next(
                (a for a in valuation.get("agents", []) if a["agent"] == "DCF"), None
            )
            comps_agent = next(
                (a for a in valuation.get("agents", []) if a["agent"] == "類似企業比較"), None
            )

            memo["valuation_data"] = {
                "fair_value": valuation.get("fair_value"),
                "upside_pct": valuation.get("upside_pct"),
                "valuation_signal": valuation.get("signal"),
                "valuation_score": valuation.get("total_score"),
                "dcf_metrics": dcf_agent["metrics"] if dcf_agent else {},
                "comps_metrics": comps_agent["metrics"] if comps_agent else {},
            }

        return json.dumps(_sanitize_for_json(memo), ensure_ascii=False, default=str)
