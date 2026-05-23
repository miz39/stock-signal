#!/usr/bin/env python3
"""
週次トレードジャーナル生成スクリプト
trades.json からその週の全トレードをまとめた markdown を生成する。
出力先: docs/trade_journal/YYYY-WXX.md
"""
import json
import os
import sys
from datetime import date, timedelta

TRADES_FILE = os.path.join(os.path.dirname(__file__), "trades.json")
OUTPUT_DIR  = os.path.join(os.path.dirname(__file__), "docs", "trade_journal")

try:
    from nikkei225 import NIKKEI_225, get_sector
except ImportError:
    NIKKEI_225 = {}
    def get_sector(t): return "不明"


def week_range(target: date):
    """月曜〜日曜を返す"""
    mon = target - timedelta(days=target.weekday())
    sun = mon + timedelta(days=6)
    return mon, sun


def iso_week_label(target: date) -> str:
    y, w, _ = target.isocalendar()
    return f"{y}-W{w:02d}"


def load_trades():
    with open(TRADES_FILE) as f:
        return json.load(f)


def generate_journal(target: date = None) -> str:
    """指定週のジャーナル markdown を返す"""
    if target is None:
        target = date.today()
    mon, sun = week_range(target)
    label = iso_week_label(mon)
    trades = load_trades()

    # 今週クローズ
    closed = [
        t for t in trades
        if t.get("status") == "closed"
        and mon.isoformat() <= (t.get("exit_date") or "") <= sun.isoformat()
    ]

    # 今週エントリー（まだオープン含む）
    entered = [
        t for t in trades
        if mon.isoformat() <= (t.get("entry_date") or "") <= sun.isoformat()
    ]

    # 今週時点のオープン（日曜時点）
    open_pos = [t for t in trades if t.get("status") == "open"]

    # 週間損益
    total_pnl = sum((t.get("exit_price", 0) - t["entry_price"]) * t["shares"] for t in closed)
    wins = [t for t in closed if (t.get("exit_price", 0) - t["entry_price"]) * t["shares"] > 0]
    losses = [t for t in closed if (t.get("exit_price", 0) - t["entry_price"]) * t["shares"] <= 0]
    gp = sum((t.get("exit_price", 0) - t["entry_price"]) * t["shares"] for t in wins)
    gl = abs(sum((t.get("exit_price", 0) - t["entry_price"]) * t["shares"] for t in losses))
    pf = f"{gp/gl:.2f}" if gl > 0 else "∞"

    lines = []
    lines.append(f"# トレードジャーナル {label}（{mon} 〜 {sun}）")
    lines.append("")
    lines.append("## 週間サマリー")
    lines.append("")
    lines.append(f"| 項目 | 値 |")
    lines.append(f"|------|-----|")
    lines.append(f"| クローズ | {len(closed)}件 |")
    lines.append(f"| 勝率 | {len(wins)}/{len(closed)} ({len(wins)/len(closed)*100:.0f}%) |" if closed else "| 勝率 | - |")
    lines.append(f"| 週間PnL | {total_pnl:+,.0f}円 |")
    lines.append(f"| PF（週） | {pf} |")
    lines.append(f"| 新規エントリー | {len(entered)}件 |")
    lines.append(f"| オープン残 | {len(open_pos)}件 |")
    lines.append("")

    # クローズトレード詳細
    if closed:
        lines.append("## クローズトレード")
        lines.append("")
        for t in sorted(closed, key=lambda x: x.get("exit_date", "")):
            ep = t["entry_price"]
            xp = t.get("exit_price", 0)
            pnl = (xp - ep) * t["shares"]
            pct = (xp / ep - 1) * 100
            name = NIKKEI_225.get(t["ticker"], t["ticker"])
            sec = get_sector(t["ticker"])
            d1 = t.get("entry_date", "")
            d2 = t.get("exit_date", "")
            days = (date.fromisoformat(d2) - date.fromisoformat(d1)).days if d1 and d2 else "?"
            exit_reason = t.get("exit_reason", "不明")
            sign = "🟢" if pnl > 0 else "🔴"

            lines.append(f"### {sign} {name}（{t['ticker']}）")
            lines.append("")
            lines.append(f"| | |")
            lines.append(f"|--|--|")
            lines.append(f"| セクター | {sec} |")
            lines.append(f"| 保有期間 | {d1} 〜 {d2}（{days}日）|")
            lines.append(f"| 株数 | {t['shares']}株 |")
            lines.append(f"| エントリー価格 | ¥{ep:,.0f} |")
            lines.append(f"| イグジット価格 | ¥{xp:,.0f} |")
            lines.append(f"| PnL | **{pnl:+,.0f}円（{pct:+.1f}%）** |")
            lines.append(f"| イグジット理由 | {exit_reason} |")

            # エントリー理由
            meta = t.get("entry_meta") or {}
            if meta:
                lines.append("")
                lines.append("**エントリー根拠:**")
                if meta.get("entry_reason"):
                    lines.append(f"- シグナル: {meta['entry_reason']}")
                if meta.get("composite_score") is not None:
                    lines.append(f"- 複合スコア: {meta['composite_score']:.3f}")
                if meta.get("rsi"):
                    lines.append(f"- RSI: {meta['rsi']}")
                if meta.get("adx"):
                    lines.append(f"- ADX: {meta['adx']:.1f}")
                if meta.get("market_regime"):
                    lines.append(f"- 市場レジーム: {meta['market_regime']}")
                if meta.get("valuation_signal"):
                    fv = f" 理論株価¥{meta['fair_value']:,.0f}" if meta.get("fair_value") else ""
                    up = f" (+{meta['upside_pct']:.1f}%)" if meta.get("upside_pct") else ""
                    lines.append(f"- バリュエーション: {meta['valuation_signal']}{fv}{up}")
            lines.append("")

    # 新規エントリー詳細（まだオープン）
    new_open = [t for t in entered if t.get("status") == "open"]
    if new_open:
        lines.append("## 今週エントリー（保有継続中）")
        lines.append("")
        for t in sorted(new_open, key=lambda x: x.get("entry_date", "")):
            name = NIKKEI_225.get(t["ticker"], t["ticker"])
            sec = get_sector(t["ticker"])
            meta = t.get("entry_meta") or {}
            days = (date.today() - date.fromisoformat(t["entry_date"])).days
            lines.append(f"### {name}（{t['ticker']}）")
            lines.append("")
            lines.append(f"| | |")
            lines.append(f"|--|--|")
            lines.append(f"| セクター | {sec} |")
            lines.append(f"| エントリー日 | {t['entry_date']}（{days}日経過）|")
            lines.append(f"| 株数 | {t['shares']}株 |")
            lines.append(f"| エントリー価格 | ¥{t['entry_price']:,.0f} |")
            lines.append(f"| ストップ価格 | ¥{t.get('stop_price', 0):,.0f} |")
            if meta:
                if meta.get("entry_reason"):
                    lines.append(f"| シグナル | {meta['entry_reason']} |")
                if meta.get("composite_score") is not None:
                    lines.append(f"| 複合スコア | {meta['composite_score']:.3f} |")
                if meta.get("valuation_signal"):
                    fv = f" ¥{meta['fair_value']:,.0f}" if meta.get("fair_value") else ""
                    lines.append(f"| バリュエーション | {meta['valuation_signal']}{fv} |")
            lines.append("")

    # オープンポジション一覧
    if open_pos:
        lines.append("## 保有ポジション一覧（週末時点）")
        lines.append("")
        lines.append("| 銘柄 | エントリー | 株数 | 取得値 | ストップ | 保有日数 |")
        lines.append("|------|-----------|------|--------|---------|---------|")
        for t in sorted(open_pos, key=lambda x: x.get("entry_date", "")):
            name = NIKKEI_225.get(t["ticker"], t["ticker"])
            days = (date.today() - date.fromisoformat(t["entry_date"])).days
            lines.append(
                f"| {name}（{t['ticker']}） "
                f"| {t['entry_date']} "
                f"| {t['shares']}株 "
                f"| ¥{t['entry_price']:,.0f} "
                f"| ¥{t.get('stop_price', 0):,.0f} "
                f"| {days}日 |"
            )
        lines.append("")

    return "\n".join(lines)


def save_journal(target: date = None):
    if target is None:
        target = date.today()
    mon, _ = week_range(target)
    label = iso_week_label(mon)
    content = generate_journal(target)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{label}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"ジャーナル生成: {path}")
    return path


if __name__ == "__main__":
    # 引数で日付指定可能（例: python generate_trade_journal.py 2026-05-19）
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    else:
        target = date.today()
    save_journal(target)
