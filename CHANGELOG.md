# Stock Signal — Changelog

## 運用目標
- **PF 1.5**（1年以内）← 5年バックテストPF1.22が現状
- 段階目標: PF1.3（3ヶ月）→ PF1.4（6ヶ月）→ PF1.5（1年）

---

## 2026-05-23

### バグ修正
- **max_daily_entries=0 で全エントリーが止まるバグ修正** (`main.py`, `backtest_improved.py`)
  - `0 >= 0 = True` で即break していた。`max_daily > 0 and daily_entries >= max_daily` に変更
  - `max_daily_entries: 0` に変更した際から発生していた既存バグ
- **recommended_shares が常に None でエントリーが実行されないバグ修正** (`main.py`)
  - シグナルのフィールド名は `shares`。`recommended_shares or shares` にフォールバック

### 戦略パラメータ変更
- **RSI上限: 65 → 62** (`config.yaml`)
  - 根拠: RSI63〜65でのエントリーが急落を食らうケースが集中
  - 8ヶ月BT: PF 1.94→2.30 / 勝率 55%→60% / リターン +16%→+27%
  - 5年BT: 差異ほぼなし（直近bull相場限定の改善）
- **ADX閾値: 25 → 28** (`config.yaml`)
  - 根拠: ADX25〜27ギリギリ通過銘柄が短期損切りになるケース多数
  - 8ヶ月BTでRSI変更と合わせてSharpe 1.89→2.49

### 機能追加
- **exit_reason を trades.json に記録** (`portfolio.py`, `main.py`)
  - 全イグジット種別（利確/トレーリングストップ/タイムストップ/CoCh/売りシグナル）に対応
  - 過去68件は未記録、2026-05-23以降のクローズから蓄積

### 分析実施
- 5年バックテスト（2021-05〜2026-05）: PF1.22、Sharpe0.60、MaxDD-13.2%
  - 2022年のみ年間PF0.94（マイナス）
- 曜日別分析: 水曜エントリーが勝率12%・-15,474円（要継続観察）
- セクター別: 機械・電機 勝率25%・PF0.23（RSI上限引き下げで改善見込み）
- 損益分布: 3月分の小負けは当時の狭いストップ設定が原因、現在は解消済み

---

## 2026-05-19

### バグ修正
- **タイムストップ実装** (`main.py`)
  - 30日超保有 & ストップ建値未到達 → 手じまい
  - 初適用: 2503.T キリン（49日保有）→ +5.7%でクローズ

### 機能改善
- **バリュエーション評価件数: TOP5 → TOP10** (`config.yaml`, `main.py`)
  - 買い候補のうちTOP10まで評価することで、割高銘柄のエントリーを早期に弾く
  - `valuation.max_candidates: 10` でconfig管理

---

## 2026-05-14 以前（累積）

### インフラ・運用
- ローカルcron廃止 → GitHub Actions に一本化（trades.json競合解消）
- `push.sh` 作成（docs/競合を自動解決して push）
- 昼（12:35）スキャン廃止（GitHub Actions無料枠の遅延が大きいため）
- `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true` 追加（Node.js 20 deprecation対応）
- `SLACK_WEBHOOK_URL` を GitHub Actions secrets に追加

### 戦略パラメータ変更
- `max_daily_entries: 3 → 0`（撤廃、上限はmax_positions・資金量・max_sector_positionsで自然制限）
- `bull_only_entry: true`（neutral/bear局面の新規エントリー全停止）

### 機能改善
- 直近30件PFをリアル移行準備度メトリクスに採用（黒字月率から変更）
- GitHub Actions に `git pull --rebase` 追加（push競合防止）
- `ValueError` を `Exception` と分離してINFOログ扱いに（exit code 1防止）

---

## 週次レビュー記録

| 週 | 件数 | 勝率 | PnL | 主なトレード |
|----|------|------|-----|------------|
| 2026-W21（05-18〜24） | 10件 | 50% | -3,138円 | 古河電工+3,240 / 富士電機+1,845 / SUMCO+1,652 / 東京エレ-5,200 / 三井金属-5,260 / 信越化学-2,224 |
| 2026-W22（05-25〜31） | 0件 | — | ±0円 | クローズなし、10ポジションホールド継続（含み益+22,678円） |
