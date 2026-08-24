#!/bin/sh
# Run only inside the QEMU guest after it has been booted with `-nic none`.

set -u

payload=${1:-/mnt/ice}
export DISPLAY=:99
export WINEARCH=win64
export WINEPREFIX="$HOME/.wine-ice"
export WINEDEBUG=-all

echo "PROBE_BEGIN"
echo "PAYLOAD=$payload"
wine --version
ip -brief link

Xvfb :99 -screen 0 1280x720x24 >"$HOME/xvfb.out" 2>"$HOME/xvfb.err" &
xvfb_pid=$!
sleep 2

timeout 90s wineboot -u >"$HOME/wineboot.out" 2>"$HOME/wineboot.err"
echo "WINEBOOT_RC=$?"
wineserver -p120

for exe in IceDisplay.exe IceTunnel.exe; do
    base=$(basename "$exe" .exe)
    rm -f "$HOME/$base.strace"*
    echo "COMPONENT_BEGIN=$base"
    timeout 20s strace -ff -e trace=network,process,file \
        -o "$HOME/$base.strace" wine "$payload/$exe" \
        >"$HOME/$base.out" 2>"$HOME/$base.err" &
    probe_pid=$!
    sleep 8
    echo "PROCESS_SNAPSHOT"
    ps -eo pid,ppid,comm,args | grep -E "($base|wine|wineserver)" | grep -v grep
    echo "TCP_LISTEN_SNAPSHOT"
    ss -lntp || true
    wait "$probe_pid"
    echo "COMPONENT_RC=$?"
    echo "STDERR_BEGIN"
    sed -n '1,100p' "$HOME/$base.err"
    echo "STDERR_END"
    echo "STRACE_NETWORK_SUMMARY"
    grep -hE 'socket\(|connect\(|bind\(|listen\(|accept' "$HOME/$base.strace"* 2>/dev/null | head -100
    echo "COMPONENT_END=$base"
    sleep 3
done

kill "$xvfb_pid" 2>/dev/null || true
wait "$xvfb_pid" 2>/dev/null || true
echo "PROBE_END"
