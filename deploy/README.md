# stock-signal Oracle Cloud デプロイ手順

Oracle Cloud Free Tier の Always Free VM（Ubuntu）にデプロイして24時間稼働させる手順。

---

## 1. Oracle Cloud アカウント作成

1. https://cloud.oracle.com にアクセス
2. 「Sign Up」でアカウント作成（クレジットカード必要だが Always Free は課金なし）
3. ホームリージョン: **ap-tokyo-1（Japan East - Tokyo）** を選択

## 2. VM 作成

1. OCI コンソールにログイン
2. **Compute → Instances → Create Instance**
3. 以下の設定:
   - **Name**: `stock-signal`
   - **Image**: Ubuntu 22.04（デフォルト）
   - **Shape**: `VM.Standard.E2.1.Micro`（1 OCPU, 1GB RAM）→ **Always Free 対象**
   - **Networking**: デフォルトのまま（Public subnet, Public IP 自動割り当て）
   - **SSH keys**: 下記で作成した公開鍵を貼り付け

## 3. SSH 鍵の作成（Mac）

```bash
# 鍵がまだない場合
ssh-keygen -t ed25519 -f ~/.ssh/oci_stock_signal

# 公開鍵をコピー → OCI コンソールに貼り付け
cat ~/.ssh/oci_stock_signal.pub | pbcopy
```

## 4. VM に接続

VM 作成完了後、Public IP をメモして接続:

```bash
ssh -i ~/.ssh/oci_stock_signal ubuntu@<VM_IP>
```

> 接続できない場合: OCI コンソール → VM の詳細 → Subnet → Security List で **ポート 22（SSH）が Ingress Rule に含まれている** ことを確認。

## 5. VM 初期セットアップ

Mac からセットアップスクリプトを転送して実行:

```bash
scp -i ~/.ssh/oci_stock_signal deploy/setup.sh ubuntu@<VM_IP>:~/setup.sh
ssh -i ~/.ssh/oci_stock_signal ubuntu@<VM_IP> 'bash ~/setup.sh'
```

## 6. 環境変数の設定

`deploy/env.conf` に実際のシークレットを記入:

```bash
cp deploy/env.conf deploy/env.conf.bak  # バックアップ（gitignore済み）
```

`deploy/env.conf` を編集:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/あなたのWebhook
DISCORD_BOT_TOKEN=あなたのBotトークン
DISCORD_POLICY_WEBHOOK_URL=https://discord.com/api/webhooks/あなたのPolicy用Webhook
```

## 7. デプロイ実行

```bash
./deploy/deploy.sh <VM_IP>
```

これだけで以下が自動実行されます:
- プロジェクトファイルの転送
- env.conf の転送（パーミッション 600）
- venv 作成 & 依存関係インストール
- systemd サービス登録 & 起動（bot.py 常駐）
- crontab 設定（平日 8:50, 12:35, 15:10 JST に main.py 実行）

## 8. 動作確認

```bash
# Bot の状態確認
ssh ubuntu@<VM_IP> 'systemctl status stocksignal-bot'

# cron 設定確認
ssh ubuntu@<VM_IP> 'crontab -l'

# main.py を手動実行してDiscord通知テスト
ssh ubuntu@<VM_IP> 'cd ~/stock-signal && ./venv/bin/python main.py'

# Bot ログ確認
ssh ubuntu@<VM_IP> 'journalctl -u stocksignal-bot -f'

# Discord で !status を送信 → Bot が応答するか確認
```

## 再デプロイ（コード更新時）

コードを変更したら同じコマンドで再デプロイ:

```bash
./deploy/deploy.sh <VM_IP>
```

## トラブルシューティング

### Bot が起動しない
```bash
ssh ubuntu@<VM_IP> 'journalctl -u stocksignal-bot --no-pager -n 50'
```

### cron が動かない
```bash
# cron のログ確認
ssh ubuntu@<VM_IP> 'grep CRON /var/log/syslog | tail -20'

# 手動実行で動作確認
ssh ubuntu@<VM_IP> 'cd ~/stock-signal && source env.conf && ./venv/bin/python main.py'
```

### メモリ不足（1GB RAM）
```bash
# スワップ追加（1GB）
ssh ubuntu@<VM_IP> 'sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab'
```

## ファイル構成

```
deploy/
├── README.md              ← この手順書
├── setup.sh               ← VM 初期セットアップ
├── deploy.sh              ← Mac→VM デプロイ（ワンコマンド）
├── stocksignal-bot.service ← systemd サービス定義
└── env.conf               ← 環境変数テンプレート（※要編集）
```
