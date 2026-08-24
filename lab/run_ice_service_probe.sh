#!/bin/sh
# Offline Wine SCM probe. Run only in the QEMU guest booted with `-nic none`.

set -u

payload=${1:-/mnt/ice}
export DISPLAY=:99
export WINEARCH=win64
: "${WINEPREFIX:=$HOME/.wine-ice}"
export WINEPREFIX
export WINEDEBUG=-all

echo "SERVICE_PROBE_BEGIN"
ip -brief link
Xvfb :99 -screen 0 1280x720x24 >"$HOME/service-xvfb.out" 2>&1 &
xvfb_pid=$!
sleep 2
timeout 180s wineboot -u
echo "WINEBOOT_RC=$?"

create_service() {
    name=$1
    exe=$2
    wine sc.exe delete "$name" >/dev/null 2>&1 || true
    wine sc.exe create "$name" binPath= "Z:\\mnt\\ice\\$exe" start= demand
}

create_service IceMainService IceMainService.exe
create_service IceDisplayService IceDisplay.exe
create_service IceTunnelService IceTunnel.exe
create_service IceInputService IceInputService.exe
create_service IceSoundService IceSound.exe

for name in IceMainService IceDisplayService IceTunnelService IceInputService IceSoundService; do
    echo "SERVICE_START=$name"
    wine sc.exe start "$name"
    sleep 8
    wine sc.exe query "$name"
    ps -eo pid,ppid,comm,args | grep -E '(Ice(Main|Display|Tunnel)|wine|services)' | grep -v grep
    echo "TCP_LISTEN_SNAPSHOT=$name"
    ss -lntp || true
done

echo "CAPTURE_START"
wine "$payload/IceVGPUCapture.exe" >"$HOME/IceVGPUCapture.out" 2>"$HOME/IceVGPUCapture.err" &
capture_pid=$!
sleep 10
ps -eo pid,ppid,comm,args | grep -E '(Ice(Input|Sound|VGPUCapture)|wine|services)' | grep -v grep
echo "TCP_CONNECTION_SNAPSHOT"
ss -ntp || true
if kill -0 "$capture_pid" 2>/dev/null; then
    echo "CAPTURE_RUNNING=yes"
else
    wait "$capture_pid"
    echo "CAPTURE_RUNNING=no RC=$?"
    sed -n '1,100p' "$HOME/IceVGPUCapture.err"
fi

sleep 12
for name in IceSoundService IceInputService IceTunnelService IceDisplayService IceMainService; do
    wine sc.exe query "$name" || true
    wine sc.exe stop "$name" >/dev/null 2>&1 || true
    wine sc.exe delete "$name" >/dev/null 2>&1 || true
done
kill "$xvfb_pid" 2>/dev/null || true
wait "$xvfb_pid" 2>/dev/null || true
echo "SERVICE_PROBE_END"
