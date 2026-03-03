#!/bin/bash
# stock-signal デプロイスクリプト（Mac → Oracle Cloud VM）
# 使い方: ./deploy/deploy.sh <VM_IP> [SSH_KEY_PATH]
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "使い方: $0 <VM_IP> [SSH_KEY_PATH]"
    echo "例:     $0 123.456.789.10"
    echo "例:     $0 123.456.789.10 ~/.ssh/oci_stock_signal"
    exit 1
fi

VM_IP="$1"
VM_USER="ubuntu"
REMOTE_DIR="/home/${VM_USER}/stock-signal"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH_OPTS=""
if [ -n "${2:-}" ]; then
    SSH_OPTS="-i $2"
fi

# ssh/scp にオプションを渡すヘルパー
do_ssh() { ssh ${SSH_OPTS} "${VM_USER}@${VM_IP}" "$@"; }
do_scp() { scp ${SSH_OPTS} "$@"; }

echo "=== stock-signal デプロイ ==="
echo "VM: ${VM_USER}@${VM_IP}"
echo "ローカル: ${PROJECT_DIR}"
echo ""

# 転送するファイル一覧
FILES=(
    "main.py"
    "bot.py"
    "data.py"
    "strategy.py"
    "risk.py"
    "portfolio.py"
    "notifier.py"
    "nikkei225.py"
    "backtest.py"
    "backtest_multi.py"
    "dashboard.py"
    "config.yaml"
    "requirements.txt"
)

# リモートディレクトリ作成
echo "[1/5] リモートディレクトリ作成..."
do_ssh "mkdir -p ${REMOTE_DIR}/agents"

# ファイル転送
echo "[2/5] ファイル転送..."
for f in "${FILES[@]}"; do
    do_scp "${PROJECT_DIR}/${f}" "${VM_USER}@${VM_IP}:${REMOTE_DIR}/${f}"
done

# agents ディレクトリ転送
do_scp "${PROJECT_DIR}"/agents/*.py "${VM_USER}@${VM_IP}:${REMOTE_DIR}/agents/"

# env.conf 転送（存在する場合のみ）
if [ -f "${PROJECT_DIR}/deploy/env.conf" ]; then
    echo "[3/5] 環境変数ファイル転送..."
    do_scp "${PROJECT_DIR}/deploy/env.conf" "${VM_USER}@${VM_IP}:${REMOTE_DIR}/env.conf"
    do_ssh "chmod 600 ${REMOTE_DIR}/env.conf"
else
    echo "[3/5] env.conf が見つかりません。手動で作成してください"
fi

# venv 作成 & 依存関係インストール
echo "[4/5] venv 作成 & 依存関係インストール..."
do_ssh "cd ${REMOTE_DIR} && python3 -m venv venv && ./venv/bin/pip install --upgrade pip && ./venv/bin/pip install -r requirements.txt"

# systemd サービス設定
echo "[5/5] systemd サービス設定..."
do_scp "${PROJECT_DIR}/deploy/stocksignal-bot.service" "${VM_USER}@${VM_IP}:/tmp/stocksignal-bot.service"
do_ssh "sudo mv /tmp/stocksignal-bot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable stocksignal-bot && sudo systemctl restart stocksignal-bot"

# crontab 設定
echo ""
echo "=== crontab 設定 ==="
do_ssh bash -s <<'CRON_EOF'
# 既存のstock-signal cron を削除して再設定
crontab -l 2>/dev/null | grep -v 'stock-signal' | crontab - 2>/dev/null || true
(crontab -l 2>/dev/null; cat <<'INNER'
TZ=Asia/Tokyo
# stock-signal: 平日 8:50, 12:35, 15:10 (JST) に実行
50 8  * * 1-5  cd /home/ubuntu/stock-signal && set -a && . ./env.conf && set +a && ./venv/bin/python main.py >> log.txt 2>&1
35 12 * * 1-5  cd /home/ubuntu/stock-signal && set -a && . ./env.conf && set +a && ./venv/bin/python main.py >> log.txt 2>&1
10 15 * * 1-5  cd /home/ubuntu/stock-signal && set -a && . ./env.conf && set +a && ./venv/bin/python main.py >> log.txt 2>&1
INNER
) | crontab -
CRON_EOF

echo ""
echo "=== デプロイ完了 ==="
echo ""
echo "確認コマンド:"
echo "  ssh ${SSH_OPTS} ${VM_USER}@${VM_IP} 'systemctl status stocksignal-bot'"
echo "  ssh ${SSH_OPTS} ${VM_USER}@${VM_IP} 'crontab -l'"
echo "  ssh ${SSH_OPTS} ${VM_USER}@${VM_IP} 'cd ~/stock-signal && ./venv/bin/python main.py'  # 手動テスト"
