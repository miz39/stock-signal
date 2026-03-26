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
| `cli.py` | CLI ラッパー（Slack Bot から subprocess 経由で呼び出し用、JSON出力） |
| `holidays.py` | 東証休場日判定（年1回JPXカレンダーを参照して更新） |
| `generate_dashboard.py` | HTML ダッシュボード + 週次レビュー生成 |
| `config.yaml` | 全パラメータ設定 |

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

# バックテスト（旧 vs 新戦略 + インデックス比較）
python3 backtest_improved.py

# CLI（Slack Bot から利用、JSON出力）
python3 cli.py rule
python3 cli.py weekly
python3 cli.py watchlist
python3 cli.py status
python3 cli.py analyze [ticker]
python3 cli.py backtest ticker [period]
python3 cli.py simulate [period]
python3 cli.py buy ticker price shares
python3 cli.py sell ticker price

# CLI プロファイル指定（--profile はサブコマンドの前に置く）
python3 cli.py --profile conservative status
python3 cli.py --profile aggressive rule
```
