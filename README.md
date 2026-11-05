<div align="center">

  <img src="icon/Unofficial_SSH_Logo.svg.ico" alt="SSH Tunnel Manager Logo" width="96" height="96" />

  # SSH Tunnel Manager

  **A modern, dark-themed Windows SSH proxy and full TUN-mode VPN client with leak protection.**

  [![Build Windows Executable](https://github.com/sajjadabd/ssh-tunnel-manager/actions/workflows/build.yml/badge.svg)](https://github.com/sajjadabd/ssh-tunnel-manager/actions/workflows/build.yml)
  [![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?logo=windows&logoColor=white)](#)
  [![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](#)
  [![License](https://img.shields.io/badge/License-MIT-green.svg)](#)

  [Download Latest Release](https://github.com/sajjadabd/ssh-tunnel-manager/releases) • [Features](#key-features) • [Quick Start](#quick-start) • [Building](#building-from-source)

</div>

---

## 📖 Overview

**SSH Tunnel Manager** turns any standard Linux SSH server into a high-performance Windows proxy or system-wide VPN. 

Built with a custom dark-mode Tkinter engine, it features a card-style UI with fluid animations, dynamic tab management for multiple VPS profiles, and a specialized **Reliability Engine** designed to eliminate connection stalls on heavy multiplexed platforms (like X.com/Twitter).

---

## ⚡ Mode Comparison

| Feature | 🌐 System Proxy Mode | 🛡️ TUN Mode (System VPN) |
| :--- | :---: | :---: |
| **Privileges Needed** | Standard User (No Admin) | Administrator (UAC Auto-Prompt) |
| **Scope** | Web browsers & proxy-aware apps | **100% of Windows System Traffic** |
| **Driver / Tech** | Windows WinINet Settings | `Wintun` + `tun2socks` |
| **IPv6 Leak Shield** | ❌ (Relies on app) |  (Outbound IPv6 dropped) |
| **DNS Leak Shield** | Partial |  (Adapter queries blocked & flushed) |
| **QUIC / UDP 443 Block**| ❌ |  (Forces reliable TCP fallback) |

---

## ✨ Key Features

### 🎨 Handcrafted UI & Fluid Canvas Animations
- **Custom TabBar:** Fixed-width VPS tabs with a compact `+` handle, live connection indicators, and middle-click tab closure.
- **Micro-Interactions:** Morphing play/stop circle buttons, hover glows, radar-pulse ping, click ripples, and error shakes.
- **Smart Copy Buttons:** One-click copy embedded directly inside entry fields that smoothly morph into a confirmation checkmark.
- **One-Line Status Pill:** Never breaks or stretches the layout; automatically ellipsizes, reveals tooltips on hover, and copies status on click.

### 🛡️ Full TUN Mode with Anti-Leak Protection
- Auto-fetches architecture-matching `wintun.dll` and `tun2socks.exe` binaries on first use.
- Automatically creates `/1` sub-default routes (`0.0.0.0/1` and `128.0.0.0/1`) to capture all global traffic without dropping physical gateway access.
- **Anti-Leak Firewall Rules:** Drops physical adapter DNS queries, disables IPv6 bindings, and blocks UDP 443 (QUIC) to guarantee leak-free operation.

### 🚀 Built-in Web Reliability Engine
- **Full-Duplex Multithreaded Relay:** Completely eliminates Windows `select.select` pipe stalls under heavy parallel HTTP/2 concurrency.
- **Persistent Upstream DNS Channel:** Multiplexes DNS resolution over a single persistent SSH channel to prevent OpenSSH `MaxSessions` channel exhaustion.
- **DNS Filtering:** Intercepts and filters Type 28 (`AAAA`) and Type 65 (`HTTPS`/SVCB) queries, preventing browser delays caused by unreachable IPv6 routes.
- **In-Memory DNS Cache:** Sub-millisecond response for concurrent asset loading.
- **Clamped MTU (1380):** Eliminates TLS handshake packet drops and fragmentation.

### 🔄 Resilient Auto-Reconnect
- Detects dropped connections in real time via continuous transport heartbeats.
- Displays a live countdown timer and gives you the option to cancel at any moment.
- Automatically cleans up routes, proxies, and firewall rules on disconnect or exit.

---

## 🚀 Quick Start

### Option 1: Pre-built Executable (Recommended)
1. Head to the **[Releases](https://github.com/sajjadabd/ssh-tunnel-manager/releases)** page.
2. Download `SSHTunnelManager.exe`.
3. Run the executable. *(If using TUN mode, run as Administrator or accept the in-app UAC elevation prompt).*

### Option 2: Run from Source
Ensure you have **Python 3.10+** installed on Windows.

1. Clone the repository:
   ```bash
   git clone https://github.com/sajjadabd/ssh-tunnel-manager.git
   cd ssh-tunnel-manager
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Launch the application (without terminal window):
   ```powershell
   pythonw sshvpn.pyw
   ```

---

## 🛠️ Building from Source

You can compile a standalone, single-file Windows executable manually using **PyInstaller**:

```powershell
python -m PyInstaller `
  --onefile `
  --noconsole `
  --uac-admin `
  --icon="./icon/Unofficial_SSH_Logo.svg.ico" `
  --add-data "./icon/Unofficial_SSH_Logo.svg.ico;icon" `
  --name "SSHTunnelManager" `
  sshvpn.pyw
```
The output file will be created at `dist/SSHTunnelManager.exe`.

---

## 🤖 Continuous Integration (GitHub Actions)

This repository includes automated builds via `.github/workflows/build.yml`:
* **On every push:** Automatically runs PyInstaller on a Windows runner and uploads `SSHTunnelManager.exe` as a build artifact.
* **On version tags (`git tag v1.0.0`):** Automatically creates a GitHub Release and attaches the compiled `.exe`.

---

## 🔒 Security & Privacy Notice

* **Local Configuration:** Server credentials are stored locally in `ssh-info.ini`.
* **Git Safe:** `ssh-info.ini` is strictly excluded in `.gitignore` to prevent accidental credential leaks.
* **Open Source:** Contains zero telemetry and makes direct connections strictly to your designated SSH server.

---

## 📄 License

Distributed under the [MIT License](LICENSE). Free for personal and commercial use.