#!/usr/bin/env bash
set -euo pipefail

readonly deploy_user="aria-deploy"
readonly deploy_home="/home/$deploy_user"
readonly command_path="/usr/local/sbin/aria-staging-deploy"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this bootstrap as root." >&2
  exit 1
fi
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 PUBLIC_KEY_FILE HOST_COMMAND_FILE" >&2
  exit 64
fi

public_key_file="$1"
host_command_file="$2"
test -s "$public_key_file"
test -s "$host_command_file"

read -r key_type key_body key_comment extra < "$public_key_file"
if [[ "$key_type" != "ssh-ed25519" || ! "$key_body" =~ ^[A-Za-z0-9+/]+={0,2}$ || -n "${extra:-}" ]]; then
  echo "Expected one OpenSSH Ed25519 public key." >&2
  exit 65
fi

if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
passwd --lock "$deploy_user" >/dev/null
usermod --append --groups docker "$deploy_user"

install -o root -g root -m 0755 "$host_command_file" "$command_path"
install -o "$deploy_user" -g "$deploy_user" -m 0700 -d "$deploy_home/.ssh"

authorized_key="restrict,command=\"$command_path\" $key_type $key_body aria-github-actions-staging"
printf '%s\n' "$authorized_key" > "$deploy_home/.ssh/authorized_keys"
chown "$deploy_user:$deploy_user" "$deploy_home/.ssh/authorized_keys"
chmod 0600 "$deploy_home/.ssh/authorized_keys"

echo "Provisioned constrained staging deploy identity: $deploy_user"
echo "Public key fingerprint: $(ssh-keygen -lf "$public_key_file" -E sha256 | awk '{print $2}')"
