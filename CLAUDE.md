# Stock Signal — S株スイングトレード自動シグナル

## 概要
日経225全銘柄をスキャンし、ゴールデンクロス + RSIフィルターで買いシグナルを生成するペーパートレードシステム。

## 運用スケジュール（cron）
```
8:50  寄り前  — main.py 実行
12:35 昼      — main.py 実行
15:10 引け後  — main.py 実行
```
平日のみ（月〜金）。通知はDiscord Webhook。

## 現在の戦略パラメータ
- エントリー: SMA25 > SMA75（ゴールデンクロス）+ RSI 50-65 + 終値 > SMA200
- 損切り: 初期-5%トレーリングストップ → +3%到達で-4%に引き締め
- 利確: +10%で50%売却（半分利確）
- 1日最大3エントリー / 同一セクター最大2銘柄
- 損切り後7日間は同一銘柄再エントリー禁止

## 主要ファイル
| ファイル | 役割 |
|----------|------|
| `main.py` | エントリポイント。スキャン→シグナル→売買→通知→履歴保存 |
| `strategy.py` | シグナル生成ロジック（SMA, RSI計算） |
| `portfolio.py` | トレード記録、ポジション管理、残高計算 |
| `risk.py` | ストップロス計算、ポジションサイジング |
| `nikkei225.py` | 日経225銘柄名マッピング + セクター分類 |
| `data.py` | yfinance経由の株価データ取得 |
| `notifier.py` | Discord通知フォーマット |
| `generate_dashboard.py` | HTML ダッシュボード + 週次レビュー生成 |
| `config.yaml` | 全パラメータ設定 |

## データファイル（git管理対象）
| ファイル | 内容 |
|----------|------|
| `trades.json` | 全トレード記録（open/closed） |
| `execution_history.json` | セッション毎の実行ログ（30日ローテーション） |

## HTML出力（`docs/`）
- `docs/index.html` — メインダッシュボード
- `docs/weekly-review.html` — 週次振り返りレポート
- `docs/history.html` — 実行履歴
- `docs/stock/*.html` — 個別銘柄チャート

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
DISCORD_WEBHOOK_URL       — シグナル通知用
DISCORD_BOT_TOKEN         — Bot用（未使用）
DISCORD_POLICY_WEBHOOK_URL — ポリシー通知用（未使用）
```

## コマンド
```bash
# 手動スキャン実行
python3 main.py

# ダッシュボード再生成
python3 generate_dashboard.py

# バックテスト（旧 vs 新戦略 + インデックス比較）
python3 backtest_improved.py
```
