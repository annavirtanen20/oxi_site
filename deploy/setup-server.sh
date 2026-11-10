#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (Hetzner Cloud).
# Run as root:  bash setup-server.sh
#
# - installs Docker and the compose plugin
# - turns on automatic security updates
# - firewall: only SSH, HTTP and HTTPS are open
# - creates user "oxi" (member of the docker group) with your SSH key
# - disables password logins over SSH (only if a key is present)
# - adds 2 GB of swap if there is none
set -euo pipefail

APP_USER=oxi

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

echo "==> Updating packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq docker.io docker-compose-v2 git ufw fail2ban unattended-upgrades

echo "==> Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Firewall"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Docker"
systemctl enable --now docker

echo "==> User $APP_USER"
if ! id "$APP_USER" &>/dev/null; then
  adduser --disabled-password --gecos "" "$APP_USER"
fi
usermod -aG docker "$APP_USER"
if [[ -s /root/.ssh/authorized_keys ]]; then
  install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
  install -m 600 -o "$APP_USER" -g "$APP_USER" /root/.ssh/authorized_keys "/home/$APP_USER/.ssh/authorized_keys"
fi

echo "==> SSH hardening"
if [[ -s /root/.ssh/authorized_keys ]]; then
  cat > /etc/ssh/sshd_config.d/99-oxi.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  systemctl reload ssh
else
  echo "!! No SSH key in /root/.ssh/authorized_keys: password login left ON."
  echo "!! Add your key and run this script again."
fi

echo "==> Swap"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "==> Backup folder"
install -d -m 750 -o "$APP_USER" -g "$APP_USER" /var/backups/oxi

echo
echo "Done. Log in as:  ssh $APP_USER@$(hostname -I | awk '{print $1}')"
