"""Risk tools — get_risk_report."""

import json

from mcp.server.fastmcp import FastMCP


def register_tools(mcp: FastMCP):

    @mcp.tool()
    def get_risk_report(quick: bool = False) -> str:
        """Get portfolio risk analysis report.

        Includes sector concentration, drawdown check, VaR/CVaR, volatility, and anomaly detection.

        Args:
            quick: If True, skip correlation analysis (faster). Default False.
        """
        from portfolio import get_open_positions, get_cash_balance, set_profile
        from data import fetch_stock_data
        from main import load_config
        from portfolio_risk import (
            format_risk_report,
            check_correlation,
            calculate_portfolio_var,
            calculate_portfolio_volatility,
        )
        from nikkei225 import NIKKEI_225

        config = load_config()
        balance = config["account"]["balance"]
        set_profile("default")

        positions = get_open_positions()

        # Attach current prices
        for pos in positions:
            try:
                df = fetch_stock_data(pos["ticker"], period="5d")
                pos["current_price"] = float(df["Close"].iloc[-1])
            except Exception:
                pos["current_price"] = pos["entry_price"]

        cash = get_cash_balance(balance)
        stock_value = sum(p["current_price"] * p["shares"] for p in positions)
        total_assets = cash + stock_value

        report = format_risk_report(positions, total_assets, config)
        report["cash"] = round(cash, 1)

        # Add VaR/CVaR
        risk_cfg = config.get("risk", {})
        var_data = calculate_portfolio_var(positions, total_assets)
        report["var"] = var_data

        # Add volatility
        vol_data = calculate_portfolio_volatility(positions)
        report["volatility"] = vol_data

        # Add correlation if not quick
        if not quick:
            threshold = risk_cfg.get("correlation_threshold", 0.70)
            correlations = check_correlation(positions, threshold=threshold)
            # Convert tuples to lists for JSON serialization
            for c in correlations:
                c["pair"] = list(c["pair"])
                c["names"] = list(c["names"])
            report["correlations"] = correlations
        else:
            report["correlations"] = "skipped (quick mode)"

        return json.dumps(report, ensure_ascii=False, default=str)
