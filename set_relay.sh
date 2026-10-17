#!/usr/bin/env bash
# Route all outbound mail from the laptop mailbridge through Gmail SMTP (port 587),
# because the ISP blocks outbound port 25.   usage: set_relay.sh you@gmail.com 'xxxx xxxx xxxx xxxx'
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <gmail address> <app password>"; exit 1; }
PW=$(echo "$2" | tr -d ' ')
echo "[smtp.gmail.com]:587 $1:$PW" | sudo tee /etc/postfix/sasl_passwd >/dev/null
sudo chmod 600 /etc/postfix/sasl_passwd && sudo postmap /etc/postfix/sasl_passwd
sudo postconf -e 'relayhost=[smtp.gmail.com]:587' 'smtp_sasl_auth_enable=yes' \
  'smtp_sasl_password_maps=hash:/etc/postfix/sasl_passwd' 'smtp_sasl_security_options=noanonymous' \
  'smtp_tls_security_level=encrypt' 'smtp_tls_CAfile=/etc/ssl/certs/ca-certificates.crt' \
  'sender_canonical_maps=' 
sudo systemctl reload postfix && sudo postqueue -f
echo "relay set to smtp.gmail.com as $1 — queued mail flushed. Note: Gmail rewrites From: to $1 unless that"
echo "address is added under Gmail > Settings > Accounts > 'Send mail as'."
