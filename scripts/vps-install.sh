#!/bin/sh
set -eu
APP_DIR=/opt/koridor
if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите на VPS от root: sh scripts/vps-install.sh"
  exit 1
fi
apt-get update
apt-get install -y ca-certificates curl
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
mkdir -p "$APP_DIR"
cp -a . "$APP_DIR/"
cd "$APP_DIR"
npm install
cp deploy/koridor.service /etc/systemd/system/koridor.service
systemctl daemon-reload
systemctl enable --now koridor
echo "Сервис koridor запущен. В ключе MEXC список IP оставьте пустым."
