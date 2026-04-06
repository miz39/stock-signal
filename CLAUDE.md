# Stock Signal — S株スイングトレード自動シグナル

## 概要
日経225全銘柄をスキャンし、ゴールデンクロス + RSIフィルターで買いシグナルを生成するペーパートレードシステム。

## 運用スケジュール（cron）
```
8:50  寄り前  — main.py → generate_dashboard.py → git push
12:35 昼      — main.py → generate_dashboard.py → git push
15:10 引け後  — main.py → generate_dashboard.py → git push
```
平日のみ（月〜金）。cron は default プロファイルのみ実行。通知は Slack Webhook。
スキャン後にダッシュボード再生成 + GitHub Pages へ自動 push。

## 戦略プロファイル
`--profile` でプロファイルを切り替え可能。各プロファイルは `config.yaml` の `profiles:` で定義し、トップレベルの `strategy:` を継承して差分だけ上書きする。`account:` は全プロファイル共通。

| プロファイル | ストップ | 建値移動 | 半分利確 | 全利確 | 狙い |
|-------------|---------|---------|---------|-------|------|
| default（現行） | -8% | +6% | +8% | +15% | バランス型 |
| conservative | -12% | +8% | +10% | +20% | 広いストップで振り落とし回避 |
| aggressive | -5% | +3% | +6% | +10% | 回転重視、早めの利確 |

- トレード記録: `trades.json`（default）/ `trades_{name}.json`（それ以外）
- 実行履歴: `execution_history.json`（default）/ `execution_history_{name}.json`（それ以外）
- Discord通知にプロファイル名ラベルが付く（default以外）

## エントリー条件（共通）
- SMA25 > SMA75（ゴールデンクロス）+ RSI 50-65 + 終値 > SMA200
- 1日最大3エントリー / 同一セクター最大2銘柄
- 損切り後7日間は同一銘柄再エントリー禁止

## MCP トレーディングワークフロー

Claude Code が MCP サーバー経由で直接データ取得・分析・売買記録を行う。
LLM API コスト不要（Claude Code Max プラン定額内）。

### MCP ツール一覧

| カテゴリ | ツール | 概要 |
|---------|--------|------|
| Market | `scan_market` | 日経225全銘柄スキャン（3-5分） |
| Market | `get_stock_data` | 個別銘柄の価格サマリー |
| Market | `get_market_regime` | 市場レジーム（bull/bear/neutral） |
| Market | `get_financial_data` | ファンダメンタルデータ（PER/PBR/ROE等） |
| Analysis | `get_signal` | 個別銘柄のBUY/SELL/HOLDシグナル |
| Analysis | `get_technical_summary` | テクニカル指標一括取得 |
| Portfolio | `get_positions` | オープンポジション一覧 |
| Portfolio | `get_cash` | 現金残高 |
| Portfolio | `get_performance` | 勝率/PnL/PF |
| Portfolio | `get_weekly_report` | 週次レポート |
| Trading | `execute_buy` | 買いエントリー（confirm=False でプレビュー） |
| Trading | `execute_sell` | 売りイグジット（confirm=False でプレビュー） |
| Trading | `update_stops` | 全ポジションのトレーリングストップ更新 |
| Risk | `get_risk_report` | セクター集中度/DD/VaR/相関 |
| Valuation | `run_full_analysis` | Trading + Valuation 統合分析（9エージェント） |
| Valuation | `get_dcf_valuation` | DCF 理論株価 + 前提値 |
| Valuation | `get_comps_analysis` | セクター内比較テーブル |
| Valuation | `get_financial_statements` | BS/PL/CF サマリー（三表分析） |
| Valuation | `get_sensitivity_table` | WACC×成長率の感応度テーブル |
| Valuation | `generate_ic_memo` | 実データベースの IC メモ |

### /scan（日次スキャン）
1. `scan_market` で全銘柄スキャン
2. BUY 候補 上位5銘柄に対して `get_technical_summary` で詳細取得
3. 各候補を以下の観点で分析（Claude Code 自身が判断）:
   - テクニカル: トレンド方向、RSI水準、ADX強度、一目の雲
   - バリュエーション: PER/PBR のセクター内位置
   - リスク: ボラティリティ、52週高値乖離、セクター集中
4. BUY / HOLD / PASS の判定 + 確信度 + 理由を提示
5. ユーザー承認後 `execute_buy` で約定

### /check（保有チェック）
1. `get_positions` で保有一覧
2. 各ポジションの含み損益、利確/ストップ到達状況
3. イグジット推奨があれば提示

### /risk（リスク分析）
1. `get_risk_report` でリスクレポート取得
2. 改善提案を提示

### /analyze（銘柄分析 — バリュエーション込み）
1. `run_full_analysis` で Trading + Valuation 9エージェント統合分析
2. 結果を整理して提示:
   - Trading Layer: テクニカル/ファンダ/センチメント/リスク
   - Valuation Layer: DCF理論株価/セクター比較/三表分析/収益構造/感応度
   - 総合判定: Combined Signal + Score
3. 必要に応じて `generate_ic_memo` で IC メモ生成
4. 100株現物投資向けの買い判断材料を提供

### 注意事項
- `scan_market` は225銘柄スキャンで3-5分かかる
- `run_full_analysis` はバリュエーション5エージェント分の API 呼び出しがあるため1-2分かかる
- `get_comps_analysis` はセクター全銘柄のデータ取得で2-3分かかる場合あり
- cron（`main.py`）と同時実行を避ける（trades.json 競合）
- cron は `llm_review_enabled: false` で API コストゼロ運用

## 主要ファイル
| ファイル | 役割 |
|----------|------|
| `main.py` | エントリポイント。スキャン→シグナル→売買→通知→履歴保存（logging: `signal.log`） |
| `strategy.py` | シグナル生成ロジック（SMA, RSI計算） |
| `portfolio.py` | トレード記録、ポジション管理、残高計算（fcntlファイルロック付き） |
| `risk.py` | ストップロス計算、ポジションサイジング |
| `nikkei225.py` | 日経225銘柄名マッピング + セクター分類 |
| `data.py` | yfinance経由の株価データ取得（3回リトライ付き） |
| `notifier.py` | Discord/Slack通知フォーマット（3回リトライ付き） |
| `portfolio_risk.py` | ポートフォリオリスク分析（相関・VaR・セクター集中度・異常検知） |
| `cli.py` | CLI ラッパー（Slack Bot から subprocess 経由で呼び出し用、JSON出力） |
| `holidays.py` | 東証休場日判定（年1回JPXカレンダーを参照して更新） |
| `generate_dashboard.py` | HTML ダッシュボード + 週次レビュー生成 |
| `config.yaml` | 全パラメータ設定 |
| `ic_memo_generator.py` | IC メモ生成（実データ注入対応） |
| `agents/coordinator.py` | エージェント統合（Trading + Valuation 2層構成） |
| `agents/dcf.py` | DCF バリュエーションエージェント |
| `agents/three_statement.py` | 三表財務分析エージェント |
| `agents/comps.py` | 類似企業比較エージェント |
| `agents/operating_model.py` | オペレーティングモデル分析エージェント |
| `agents/sensitivity.py` | 感応度分析エージェント |
| `mcp_server/` | MCP サーバー（Claude Code 連携用、stdio） |
| `mcp_server/tools/valuation.py` | バリュエーション MCP ツール（6ツール） |

## バックテストファイル
| ファイル | 用途 |
|----------|------|
| `backtest.py` | bot.py の `!backtest` コマンド用（単一銘柄シミュレーション） |
| `backtest_multi.py` | bot.py の `!simulate` コマンド用（マルチエージェント判断） |
| `backtest_improved.py` | スタンドアロン戦略比較ツール（`python3 backtest_improved.py` で直接実行） |

## データファイル（git管理対象）
| ファイル | 内容 |
|----------|------|
| `trades.json` | default プロファイルのトレード記録 |
| `trades_{profile}.json` | 各プロファイルのトレード記録 |
| `execution_history.json` | default プロファイルの実行ログ（30日ローテーション） |
| `execution_history_{profile}.json` | 各プロファイルの実行ログ |

## HTML出力（`docs/`）
- `docs/index.html` — メインダッシュボード（default）
- `docs/weekly-review.html` — 週次振り返りレポート（default）
- `docs/history.html` — 実行履歴（default）
- `docs/stock/*.html` — 個別銘柄チャート（default）
- `docs/{profile}/` — 各プロファイルのダッシュボード（conservative, aggressive）

## レビューワークフロー
5つのペルソナで多視点レビューを実施する（詳細は `docs/agents/` 参照）:
1. テクニカルアナリスト — 指標・シグナル精度
2. リスクマネージャー — 資金管理・RR比
3. マーケットアナリスト — 市場環境・外部要因
4. クオンツトレーダー — 統計・期待値
5. 個人投資家 — 実践・心理面

レビュー結果は `docs/reviews/YYYY-MM-DD_<topic>.md` に保存する。

## 環境変数
```
DISCORD_WEBHOOK_URL       — Discord シグナル通知用
DISCORD_BOT_TOKEN         — Discord Bot用（未使用）
DISCORD_POLICY_WEBHOOK_URL — Discord ポリシー通知用（未使用）
SLACK_WEBHOOK_URL         — Slack シグナル通知用（Incoming Webhook）
```

## コマンド
```bash
# 手動スキャン実行（default プロファイル）
python3 main.py

# 特定プロファイルで実行
python3 main.py --profile conservative
python3 main.py --profile aggressive

# 全プロファイル順次実行（cron用）
python3 main.py --profile all

# ダッシュボード再生成
python3 generate_dashboard.py
python3 generate_dashboard.py --profile conservative
python3 generate_dashboard.py --profile all

# バックテスト
python3 backtest_improved.py                     # プロファイル比較（default）
python3 backtest_improved.py --mode sensitivity  # パラメータ感度分析
python3 backtest_improved.py --mode stats        # 統計分析（ブートストラップCI等）
python3 backtest_improved.py --mode walkforward  # ウォークフォワード検証

# CLI（Slack Bot から利用、JSON出力）
python3 cli.py rule
python3 cli.py weekly
python3 cli.py watchlist
python3 cli.py status
python3 cli.py risk              # リスク分析（VaR/CVaR/セクター集中度/アノマリー）
python3 cli.py risk --quick      # 相関分析をスキップ（高速）
python3 cli.py performance [1w|1m|3m|6m|1y]  # 期間指定パフォーマンスレポート
python3 cli.py compare           # プロファイル間の比較
python3 cli.py analyze [ticker]
python3 cli.py backtest ticker [period]
python3 cli.py simulate [period]
python3 cli.py buy ticker price shares
python3 cli.py sell ticker price

# CLI プロファイル指定（--profile はサブコマンドの前に置く）
python3 cli.py --profile conservative status
python3 cli.py --profile aggressive rule
```
