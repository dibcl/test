# Isolated Debian/Wine session-component lab

This lab is for a bounded compatibility check of the signed Windows ICE
session components.  It does not contain platform credentials, VM identity
material, socket protocol replay, or code that connects to the mobile-cloud
management plane.

The base Debian cloud image remains immutable.  `ice-wine-lab.qcow2` is a
copy-on-write overlay.  Bootstrap networking is QEMU user-mode NAT and exists
only to install Debian packages.  Proprietary component tests must be started
without any emulated network device (`-nic none`).

The cloud-init account and password are intentionally lab-only and must never
be used on a reachable or production machine.
