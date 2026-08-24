#!/bin/sh
# Bootstrap a disposable child overlay with the signed WineHQ stable packages.

set -eu

payload=${1:-/mnt/ice}
sudo install -d -m 0755 /etc/apt/keyrings
sudo install -m 0644 "$payload/winehq-archive.key" /etc/apt/keyrings/winehq-archive.key
sudo install -m 0644 "$payload/winehq-trixie.sources" /etc/apt/sources.list.d/winehq-trixie.sources
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends winehq-stable
wine --version
sudo poweroff
