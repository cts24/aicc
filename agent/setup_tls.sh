#!/bin/bash
# Sets up SIP TLS + SRTP on Asterisk for MicroSIP / Linphone direct connection

set -e

echo "=== Step 1: Generate self-signed TLS certificate ==="
sudo mkdir -p /etc/asterisk/keys
cd /etc/asterisk/keys

sudo openssl genrsa -out asterisk.key 2048
sudo openssl req -new -x509 -days 3650 \
  -key asterisk.key \
  -out asterisk.crt \
  -subj "/C=PK/O=TimesTravel/CN=44.194.44.98"
sudo cat asterisk.crt asterisk.key | sudo tee asterisk.pem > /dev/null
sudo chmod 640 /etc/asterisk/keys/*
sudo chown asterisk:asterisk /etc/asterisk/keys/*

echo "=== Step 2: Write pjsip.conf ==="
sudo tee /etc/asterisk/pjsip.conf > /dev/null << 'EOF'
; ── Transports ────────────────────────────────────────────────────────────────

[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0

[transport-tls]
type=transport
protocol=tls
bind=0.0.0.0:5061
cert_file=/etc/asterisk/keys/asterisk.pem
ca_list_file=/etc/asterisk/keys/asterisk.crt
method=tlsv1_2
verify_client=no

; ── Endpoint 1000 (MicroSIP laptop + Linphone mobile) ─────────────────────────

[1000]
type=endpoint
context=from-internal
disallow=all
allow=ulaw
allow=alaw
auth=1000
aors=1000
direct_media=no
force_rport=yes
rtp_symmetric=yes
rewrite_contact=yes
ice_support=no
media_encryption=sdes
media_encryption_optimistic=yes

[1000]
type=auth
auth_type=userpass
username=1000
password=Times2025!

[1000]
type=aor
max_contacts=2
remove_existing=no
EOF

echo "=== Step 3: Reload Asterisk ==="
sudo asterisk -rx "module reload res_pjsip.so"
sudo asterisk -rx "module reload res_pjsip_session.so"

echo "=== Step 4: Verify TLS transport ==="
sudo asterisk -rx "pjsip show transports"

echo ""
echo "=== DONE ==="
echo "Now open port 5061 TCP in your EC2 security group."
echo ""
echo "MicroSIP settings:"
echo "  Server:    44.194.44.98"
echo "  Username:  1000"
echo "  Password:  Times2025!"
echo "  Transport: TLS"
echo "  Port:      5061"
echo "  SRTP:      enabled"
echo "  Verify TLS cert: OFF (self-signed)"
