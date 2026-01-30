#!/bin/sh

script_path=/mnt/sshbox
pcap_path=/mnt/pcap
ssh_password=$1

cd "$script_path"
echo "=== script start ===" $(date -Iseconds) >>"$pcap_path/server.log"

tail -n 1 -f "$pcap_path/server.log" &

./tcpdump -U -B 4096 --immediate-mode -p -i any -w "$pcap_path/all.pcap" >>"$pcap_path/server.log" 2>&1 &
tcpdump_pid=$!

mkdir -p /etc/dropbear
SSHKEYLOGFILE="$pcap_path/ssh_keylog.txt" SSLKEYLOGFILE="$pcap_path/ssl_keylog.txt" DROPBEAR_CLEARML_FIXED_PASSWORD="$ssh_password" SFTPSERVER_PATH="$script_path/sftp-server" \
    ./dropbear -F -R -e >>"$pcap_path/server.log" 2>&1 &
dropbear_pid=$!

stop_gracefully() {
    echo "=== script interrupted ===" $(date -Iseconds) >>"$pcap_path/server.log"
    kill -TERM $tcpdump_pid $dropbear_pid
    exit 130
}
trap 'stop_gracefully' TERM INT

wait $tcpdump_pid $dropbear_pid
echo "=== script exit ===" $(date -Iseconds) >>"$pcap_path/server.log"
