#!/bin/bash
# stock-signal VM 初期セットアップスクリプト
# Oracle Cloud Ubuntu 22.04 で実行
set -euo pipefail

echo "=== stock-signal VM セットアップ ==="

# パッケージ更新
echo "[1/4] パッケージ更新..."
sudo apt-get update -y && sudo apt-get upgrade -y

# Python 3 + venv + pip
echo "[2/4] Python 3 インストール..."
sudo apt-get install -y python3 python3-pip python3-venv

# タイムゾーン設定
echo "[3/4] タイムゾーンを Asia/Tokyo に設定..."
sudo timedatectl set-timezone Asia/Tokyo

# プロジェクトディレクトリ作成
echo "[4/4] プロジェクトディレクトリ作成..."
mkdir -p ~/stock-signal

echo ""
echo "=== セットアップ完了 ==="
echo "Python: $(python3 --version)"
echo "TZ:     $(timedatectl show -p Timezone --value)"
echo ""
echo "次のステップ: Mac から deploy.sh を実行してファイルを転送してください"
