"""
Report generator for daily and weekly trading reports.
Produces Markdown files and structured data for dashboard integration.
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from nikkei225 import NIKKEI_225

logger = logging.getLogger("signal")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(_BASE_DIR, "docs", "reports")

JST = timezone(timedelta(hours=9))


def _ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def _format_currency(val):
    if val is None:
        return "N/A"
    return f"¥{val:,.0f}"


def _format_pct(val):
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def generate_daily_report(
    scan_results: dict,
    agent_analyses: list,
    llm_reviews: list,
    portfolio_snapshot: dict,
    market_regime: dict,
    executed_entries: list,
    exit_records: list,
    open_positions: list,
    config: dict,
) -> dict:
    """
    Generate a daily report from scan results.

    Args:
        scan_results: {"total": int, "buy_count": int, "sell_count": int, "error_count": int}
        agent_analyses: List of agent analysis results for top candidates
        llm_reviews: List of LLM review results
        portfolio_snapshot: {"open_count", "stock_value", "cash", "total_assets"}
        market_regime: {"regime": str, "price": float, "sma50": float, "sma200": float}
        executed_entries: List of executed entry records
        exit_records: List of exit records
        open_positions: List of current open positions
        config: Full config dict

    Returns:
        dict with "markdown", "data" keys
    """
    today = datetime.now(JST).strftime("%Y-%m-%d")
    regime = market_regime.get("regime", "neutral")
    regime_label = {"bull": "Bull（強気）", "bear": "Bear（弱気）", "neutral": "Neutral（中立）"}.get(regime, regime)

    lines = [
        f"# デイリーレポート {today}",
        "",
        "## 市場概況",
        "",
        f"- **市場レジーム**: {regime_label}",
    ]

    if market_regime.get("price"):
        lines.append(f"- **日経225**: {_format_currency(market_regime['price'])}")
    if market_regime.get("sma50"):
        lines.append(f"- **SMA50**: {_format_currency(market_regime['sma50'])}")
    if market_regime.get("sma200"):
        lines.append(f"- **SMA200**: {_format_currency(market_regime['sma200'])}")

    lines.extend([
        "",
        f"- スキャン銘柄数: {scan_results.get('total', 0)}",
        f"- 買いシグナル: {scan_results.get('buy_count', 0)}銘柄",
        f"- 売りシグナル: {scan_results.get('sell_count', 0)}銘柄",
        "",
    ])

    # Top candidates with agent analysis
    if agent_analyses:
        lines.append("## 注目銘柄 TOP5")
        lines.append("")

        for i, analysis in enumerate(agent_analyses[:5], 1):
            ticker = analysis.get("ticker", "")
            name = NIKKEI_225.get(ticker, ticker)
            signal = analysis.get("signal", "N/A")
            score = analysis.get("total_score", 0)
            confidence = analysis.get("confidence", 0)

            lines.append(f"### {i}. {name}（{ticker.replace('.T', '')}）")
            lines.append(f"- **判断**: {signal}（スコア: {score:.2f} / 信頼度: {confidence}%）")

            # Agent reasons
            for reason in analysis.get("reasons_summary", []):
                lines.append(f"- {reason}")

            # Deep analysis if available
            sig = next((s for s in llm_reviews if s.get("ticker") == ticker), None)
            deep = sig.get("deep_analysis") if sig else None
            if deep and not deep.get("skipped"):
                lines.append(f"- **AI判断**: {deep.get('judgment', 'N/A')}（確信度: {deep.get('conviction', 0)}/10）")
                if deep.get("summary"):
                    lines.append(f"- **サマリー**: {deep['summary']}")
                if deep.get("buy_reasons"):
                    lines.append("- **買い理由**:")
                    for r in deep["buy_reasons"][:3]:
                        lines.append(f"  - {r}")
                if deep.get("risk_factors"):
                    lines.append("- **リスク**:")
                    for r in deep["risk_factors"][:3]:
                        lines.append(f"  - {r}")
                if deep.get("scenarios"):
                    scenarios = deep["scenarios"]
                    lines.append("- **シナリオ**:")
                    if scenarios.get("bull"):
                        lines.append(f"  - 強気: {scenarios['bull']}")
                    if scenarios.get("base"):
                        lines.append(f"  - ベース: {scenarios['base']}")
                    if scenarios.get("bear"):
                        lines.append(f"  - 弱気: {scenarios['bear']}")

            lines.append("")

    # Executed entries
    if executed_entries:
        lines.append("## 本日のエントリー")
        lines.append("")
        for entry in executed_entries:
            name = entry.get("name", entry.get("ticker", ""))
            lines.append(
                f"- **{name}** {_format_currency(entry.get('price'))} × {entry.get('shares', 0)}株"
                f" | RSI {entry.get('rsi', 0):.1f}"
            )
        lines.append("")

    # Exits
    if exit_records:
        lines.append("## 本日のイグジット")
        lines.append("")
        for ex in exit_records:
            name = ex.get("name", ex.get("ticker", ""))
            pnl = ex.get("pnl", 0)
            pnl_pct = ex.get("pnl_pct", 0)
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"- **{name}** {_format_currency(ex.get('price'))} | {ex.get('reason', '')}"
                f" | {sign}{_format_currency(pnl)}（{_format_pct(pnl_pct)}）"
            )
        lines.append("")

    # Portfolio snapshot
    lines.append("## ポートフォリオ状況")
    lines.append("")
    lines.append(f"- **保有銘柄数**: {portfolio_snapshot.get('open_count', 0)}")
    lines.append(f"- **株式時価**: {_format_currency(portfolio_snapshot.get('stock_value', 0))}")
    lines.append(f"- **現金**: {_format_currency(portfolio_snapshot.get('cash', 0))}")
    lines.append(f"- **総資産**: {_format_currency(portfolio_snapshot.get('total_assets', 0))}")
    lines.append("")

    # Open positions detail
    if open_positions:
        lines.append("### 保有ポジション")
        lines.append("")
        lines.append("| 銘柄 | 取得価格 | 現在価格 | 含み損益 | 保有日数 |")
        lines.append("|------|---------|---------|---------|---------|")
        for pos in open_positions:
            name = NIKKEI_225.get(pos["ticker"], pos["ticker"])
            entry = pos["entry_price"]
            current = pos.get("current_price", entry)
            pnl_pct = (current / entry - 1) * 100
            days = 0
            if pos.get("entry_date"):
                try:
                    d = date.fromisoformat(pos["entry_date"])
                    days = (date.today() - d).days
                except (ValueError, TypeError):
                    pass
            lines.append(
                f"| {name} | {_format_currency(entry)} | {_format_currency(current)} "
                f"| {_format_pct(pnl_pct)} | {days}日 |"
            )
        lines.append("")

    # Risk alerts
    lines.append("## リスクアラート")
    lines.append("")
    if regime == "bear":
        lines.append("- 市場レジームがBear → 新規エントリーを抑制")
    if portfolio_snapshot.get("open_count", 0) >= config.get("account", {}).get("max_positions", 10):
        lines.append("- ポジション数が上限に達しています")
    if not any([regime == "bear", portfolio_snapshot.get("open_count", 0) >= config.get("account", {}).get("max_positions", 10)]):
        lines.append("- 特になし")
    lines.append("")

    markdown = "\n".join(lines)

    # Save to file
    _ensure_reports_dir()
    filepath = os.path.join(REPORTS_DIR, f"{today}_daily.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info(f"Daily report saved: {filepath}")

    return {
        "markdown": markdown,
        "filepath": filepath,
        "data": {
            "date": today,
            "market_regime": regime,
            "scan": scan_results,
            "top_candidates": [
                {
                    "ticker": a.get("ticker"),
                    "signal": a.get("signal"),
                    "score": a.get("total_score"),
                    "confidence": a.get("confidence"),
                }
                for a in agent_analyses[:5]
            ] if agent_analyses else [],
            "entries": len(executed_entries),
            "exits": len(exit_records),
            "portfolio": portfolio_snapshot,
        },
    }


def generate_weekly_report(
    weekly_history: list,
    portfolio_snapshot: dict,
    balance: float,
    config: dict,
) -> dict:
    """
    Generate a weekly report from execution history.

    Args:
        weekly_history: List of execution history records for the week
        portfolio_snapshot: Current portfolio state
        balance: Initial balance
        config: Full config dict

    Returns:
        dict with "markdown", "data" keys
    """
    today = datetime.now(JST).strftime("%Y-%m-%d")
    week_start = (datetime.now(JST) - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = [
        f"# 週次レポート {week_start} 〜 {today}",
        "",
    ]

    # Performance summary
    all_entries = []
    all_exits = []
    for record in weekly_history:
        execs = record.get("executions", {})
        all_entries.extend(execs.get("entries", []))
        all_exits.extend(execs.get("exits", []))
        all_exits.extend(execs.get("partial_exits", []))

    total_pnl = sum(e.get("pnl", 0) for e in all_exits)
    wins = [e for e in all_exits if e.get("pnl", 0) > 0]
    losses = [e for e in all_exits if e.get("pnl", 0) <= 0]
    win_rate = len(wins) / len(all_exits) * 100 if all_exits else 0

    lines.extend([
        "## 週間パフォーマンス",
        "",
        f"- **新規エントリー**: {len(all_entries)}件",
        f"- **イグジット**: {len(all_exits)}件",
        f"- **損益合計**: {'+' if total_pnl >= 0 else ''}{_format_currency(total_pnl)}",
        f"- **勝率**: {win_rate:.0f}%（{len(wins)}W / {len(losses)}L）",
        "",
    ])

    # Successful trades
    if wins:
        lines.append("## 成功トレード")
        lines.append("")
        for w in sorted(wins, key=lambda x: x.get("pnl", 0), reverse=True)[:5]:
            name = w.get("name", w.get("ticker", ""))
            lines.append(f"- **{name}** +{_format_currency(w.get('pnl', 0))}（{_format_pct(w.get('pnl_pct', 0))}）| {w.get('reason', '')}")
        lines.append("")

    # Failed trades
    if losses:
        lines.append("## 失敗トレード")
        lines.append("")
        for l in sorted(losses, key=lambda x: x.get("pnl", 0))[:5]:
            name = l.get("name", l.get("ticker", ""))
            lines.append(f"- **{name}** {_format_currency(l.get('pnl', 0))}（{_format_pct(l.get('pnl_pct', 0))}）| {l.get('reason', '')}")
        lines.append("")

    # Strategy effectiveness
    lines.extend([
        "## 戦略の有効性",
        "",
    ])

    # Market regime tracking
    regimes = [r.get("market_regime", {}).get("regime", "neutral") for r in weekly_history]
    if regimes:
        regime_counts = {}
        for r in regimes:
            regime_counts[r] = regime_counts.get(r, 0) + 1
        regime_summary = ", ".join(f"{k}: {v}回" for k, v in regime_counts.items())
        lines.append(f"- **市場レジーム**: {regime_summary}")

    # Agent analysis accuracy (if available)
    agent_signals = []
    for record in weekly_history:
        for sig in record.get("buy_signals", []):
            if sig.get("agent_analysis"):
                agent_signals.append(sig)

    if agent_signals:
        lines.append(f"- **エージェント分析対象**: {len(agent_signals)}銘柄")

    lines.append("")

    # Portfolio status
    lines.extend([
        "## ポートフォリオ状況",
        "",
        f"- **総資産**: {_format_currency(portfolio_snapshot.get('total_assets', 0))}",
        f"- **初期残高からの変動**: {_format_pct((portfolio_snapshot.get('total_assets', balance) / balance - 1) * 100)}",
        f"- **保有銘柄数**: {portfolio_snapshot.get('open_count', 0)}",
        "",
    ])

    markdown = "\n".join(lines)

    # Save to file
    _ensure_reports_dir()
    filepath = os.path.join(REPORTS_DIR, f"{today}_weekly.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info(f"Weekly report saved: {filepath}")

    return {
        "markdown": markdown,
        "filepath": filepath,
        "data": {
            "week_start": week_start,
            "week_end": today,
            "entries": len(all_entries),
            "exits": len(all_exits),
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "portfolio": portfolio_snapshot,
        },
    }


def generate_report_html(reports_dir: str = None) -> str:
    """
    Generate an HTML page listing all reports.

    Returns:
        str: HTML content
    """
    if reports_dir is None:
        reports_dir = REPORTS_DIR

    if not os.path.exists(reports_dir):
        return "<html><body><h1>No reports yet</h1></body></html>"

    reports = []
    for f in sorted(os.listdir(reports_dir), reverse=True):
        if f.endswith(".md"):
            filepath = os.path.join(reports_dir, f)
            with open(filepath, "r", encoding="utf-8") as fh:
                content = fh.read()
            # Extract title from first line
            title = content.split("\n")[0].replace("# ", "") if content else f
            report_type = "daily" if "_daily.md" in f else "weekly" if "_weekly.md" in f else "other"
            reports.append({
                "filename": f,
                "title": title,
                "type": report_type,
                "content": content,
            })

    # Simple HTML rendering
    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="ja">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "<title>Stock Signal Reports</title>",
        "<style>",
        "body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; background: #0a0a0a; color: #e0e0e0; }",
        "h1 { color: #FF4D00; border-bottom: 2px solid #FF4D00; padding-bottom: 8px; }",
        "h2 { color: #00D4FF; }",
        "h3 { color: #ccc; }",
        ".report { border: 1px solid #333; padding: 20px; margin: 20px 0; border-left: 4px solid #FF4D00; background: #111; }",
        ".daily { border-left-color: #FF4D00; }",
        ".weekly { border-left-color: #00D4FF; }",
        "table { border-collapse: collapse; width: 100%; }",
        "th, td { border: 1px solid #333; padding: 8px; text-align: left; }",
        "th { background: #1a1a1a; }",
        "a { color: #00D4FF; }",
        ".badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }",
        ".badge-daily { background: #FF4D00; color: #fff; }",
        ".badge-weekly { background: #00D4FF; color: #000; }",
        "pre { background: #1a1a1a; padding: 12px; overflow-x: auto; border: 1px solid #333; }",
        "</style>",
        "</head>",
        "<body>",
        '<h1>Stock Signal Reports</h1>',
        '<p><a href="index.html">← ダッシュボードに戻る</a></p>',
    ]

    if not reports:
        html_parts.append("<p>レポートはまだありません。</p>")
    else:
        for report in reports[:30]:  # Show latest 30
            badge_class = f"badge-{report['type']}"
            badge_label = "日次" if report["type"] == "daily" else "週次" if report["type"] == "weekly" else report["type"]
            html_parts.append(f'<div class="report {report["type"]}">')
            html_parts.append(f'<span class="badge {badge_class}">{badge_label}</span>')

            # Convert markdown to simple HTML
            md_html = _markdown_to_html(report["content"])
            html_parts.append(md_html)

            html_parts.append("</div>")

    html_parts.extend(["</body>", "</html>"])
    return "\n".join(html_parts)


def _markdown_to_html(md: str) -> str:
    """Very simple markdown to HTML conversion."""
    html_lines = []
    in_table = False
    in_list = False

    for line in md.split("\n"):
        stripped = line.strip()

        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{stripped[2:]}</h2>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{stripped[3:]}</h3>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h4>{stripped[4:]}</h4>")
        elif stripped.startswith("|"):
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            if stripped.startswith("|---") or stripped.startswith("| ---"):
                continue
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            row = "".join(f"<td>{c}</td>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
        elif stripped.startswith("- "):
            if in_table:
                html_lines.append("</table>")
                in_table = False
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = stripped[2:]
            content = content.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
            html_lines.append(f"<li>{content}</li>")
        elif stripped.startswith("  - "):
            content = stripped[4:]
            html_lines.append(f"<li style='margin-left:20px'>{content}</li>")
        elif stripped == "":
            if in_table:
                html_lines.append("</table>")
                in_table = False
            if in_list:
                html_lines.append("</ul>")
                in_list = False
        else:
            html_lines.append(f"<p>{stripped}</p>")

    if in_table:
        html_lines.append("</table>")
    if in_list:
        html_lines.append("</ul>")

    return "\n".join(html_lines)
