#!/bin/sh
# Core-only stability gate; run in the isolated guest with -nic none.
set -eu
payload=${1:-/mnt/ice}
: "${WINEPREFIX:=$HOME/.wine-ice}"
export WINEPREFIX
export WINEARCH=win64
export DISPLAY=:99
export WINEDEBUG=-all

echo CORE_GATE_BEGIN
Xvfb :99 -screen 0 1280x720x24 >"$HOME/core-xvfb.log" 2>&1 &
xpid=$!
sleep 2
timeout 180s wineboot -u
echo WINEBOOT_RC=$?
wine sc.exe delete IceMainService >/dev/null 2>&1 || true
wine sc.exe create IceMainService binPath= "Z:\\mnt\\ice\\IceMainService.exe" start= demand
wine sc.exe start IceMainService

ok=1
i=0
while [ "$i" -lt 6 ]; do
    sleep 10
    echo SNAPSHOT=$i
    wine sc.exe query IceMainService || ok=0
    ps -eo comm,args | grep -E '(Ice(Main|Tunnel|Display)|wineserver)' | grep -v grep || ok=0
    ss -lntp | grep -E ':(60063|5100)\b' || ok=0
    i=$((i + 1))
done
echo CORE_GATE_RESULT=$ok
wine sc.exe stop IceMainService >/dev/null 2>&1 || true
wine sc.exe delete IceMainService >/dev/null 2>&1 || true
kill "$xpid" 2>/dev/null || true
wait "$xpid" 2>/dev/null || true
echo CORE_GATE_END
