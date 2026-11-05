#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSH Tunnel Manager (Windows, Tkinter, dark theme)

Requirements:
    pip install paramiko

Features:
  * One tab per VPS (remarks, ip, ssh port, username, password)
  * Every input field has an animated COPY icon inside it (right end):
      - click copies the field value, icon morphs into a drawn checkmark
      - after 1 second the checkmark morphs back to the copy icon
  * Custom Canvas TabBar: every VPS tab handle has identical size,
    the "+" tab is a compact handle (no extra empty width)
  * Card-style form, activity bar (sweeping while connecting, green when connected)
  * Animated Canvas SegmentedSwitch (pill glides between Proxy and TUN mode)
  * Animated circle buttons:
      - hover glow + grow, press shrink, click ripple
      - play <-> stop icon morph
      - spinner ring while connecting / disconnecting
      - radar pulse while connected (turns red on hover = "click to disconnect")
      - shake + red flash on failure, green flash on success
  * ONE-LINE status pill (never wraps, never jumps out of frame):
      - long text is ellipsized, hover shows the full text, click copies it
      - exact details: stage, HTTP status, latencies, host:port, uptime, cleanup actions
  * AUTO-RECONNECT: when the connection is lost (or a reconnect attempt fails),
    the app waits 5 seconds and tries again, forever, until it succeeds or you
    click the button ("Cancel") to stop. A live countdown is shown in the status pill.
  * Connect via SSH (paramiko) and run a local SOCKS4/SOCKS5 server (default 1080)
  * Connection is verified with an HTTP request -> must return "Response status 200"
  * Mode 1: Set Windows system proxy (no admin needed)
  * Mode 2: TUN mode - full traffic with IPv6/DNS Leak Protection (needs Administrator)
  * Complete Reliability Engine for X.com / Twitter:
      - Full-duplex multithreaded relay (eliminates Windows select.select channel stalls)
      - Persistent upstream DNS channel (prevents OpenSSH MaxSessions channel dropping)
      - Filters DNS Type 28 (AAAA) and Type 65 (HTTPS) to enforce deterministic IPv4 TCP
      - In-memory DNS cache (<1ms response on parallel asset loading)
      - TCP_NODELAY enabled for zero-latency HTTP/2 frame delivery
      - MTU clamped to 1380 (prevents TLS handshake packet truncation)
      - Anti-leak firewall rules for IPv6, physical adapter DNS, and UDP 443 (QUIC)
  * Right-click "Run as administrator" entry for .py/.pyw files (registered automatically)
  * In-app elevation: offers to restart as Administrator and reconnects automatically
  * Clean teardown (in a background thread, UI stays smooth) and restore of routing
    tables and firewall rules on disconnect
  * Full icon support for Window title bar, Windows taskbar, and Alt+Tab
"""

import os
import sys
import io
import math
import socket
import struct
import select
import shutil
import threading
import queue
import time
import subprocess
import configparser
import ctypes
import logging
import platform
import traceback
import zipfile
import urllib.request
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

try:
    import winreg
except ImportError:
    winreg = None

try:
    import paramiko
except ImportError:
    paramiko = None

# Optional PIL support for smooth icon rendering in custom title bars
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INI_PATH = os.path.join(BASE_DIR, "ssh-info.ini")
TUN_DIR = os.path.join(BASE_DIR, "tun")
TUN_NAME = "SSHTun"
TUN_IP = "192.168.123.1"
TUN_MTU = "1380"
CREATE_NO_WINDOW = 0x08000000

CHECK_URLS = [
    ("www.msftconnecttest.com", "/connecttest.txt"),
    ("example.com", "/"),
]

# Auto-reconnect: seconds to wait between attempts after a connection loss / failed attempt
RECONNECT_DELAY = 5
# SSH keepalive interval in seconds (smaller = dead links are detected faster)
KEEPALIVE_SECONDS = 15

# Size (in pixels at 100% DPI) of every VPS tab handle
TAB_W = 118
TAB_H = 34
# Width (in pixels at 100% DPI) of the compact "+" tab handle
PLUS_TAB_W = 38

# Copy icon inside input fields (pixels at 100% DPI)
COPY_ICON_SIZE = 22      # size of the clickable icon square
COPY_ICON_MARGIN = 6     # gap between the icon and the right end of the field
COPY_RESET_MS = 1000     # checkmark is shown this long, then resets to the copy icon

# Window size
WIN_W = 580
WIN_H = 700
WIN_MIN_W = 540
WIN_MIN_H = 680

# Title bar style
TITLEBAR_STYLE = "auto"
TITLE_BG = "#252526"

# Theme
BG = "#1e1e1e"
PANEL = "#252526"
FIELD = "#2d2d30"
FG = "#e6e6e6"
MUTED = "#9a9a9a"
BORDER = "#3c3c3c"
ORANGE = "#FF8C00"
ORANGE_HOVER = "#FFA02E"
ORANGE_DOWN = "#D97500"
ORANGE_OFF = "#6e4a1c"
OK_COLOR = "#4CAF50"
ERR_COLOR = "#F44336"
GREEN_FILL = "#2f9e57"      # connect button while connected
RED_FILL = "#d94b43"        # connect button while connected + hovered (= disconnect)


# --------------------------------------------------------------------------- #
#  Small drawing / animation helpers
# --------------------------------------------------------------------------- #
def mix(c1, c2, t):
    """Linear blend between two #rrggbb colors (t=0 -> c1, t=1 -> c2)."""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def approach(cur, tgt, step):
    if cur < tgt:
        return min(tgt, cur + step)
    return max(tgt, cur - step)


def fit_text(font, text, maxw):
    """Ellipsizes `text` so it fits in `maxw` pixels (always a single line)."""
    if maxw <= 0:
        return ""
    if font.measure(text) <= maxw:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid].rstrip() + "…") <= maxw:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def fmt_dur(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %02dm %02ds" % (h, m, s)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s


def describe_error(stage, e, d):
    """Turns an exception into a short, precise, single-line reason."""
    host = "%s:%s" % (d.get("ip", "?"), d.get("port", "?"))
    raw = " ".join((str(e) or e.__class__.__name__).split())
    if paramiko is not None:
        if isinstance(e, paramiko.AuthenticationException):
            return "authentication rejected for user '%s' (wrong username or password)" % d.get("username", "?")
        if isinstance(e, paramiko.BadHostKeyException):
            return "host key mismatch for %s" % host
        if isinstance(e, paramiko.ssh_exception.NoValidConnectionsError):
            return "cannot reach %s (connection refused or port closed)" % host
        if isinstance(e, paramiko.SSHException):
            return "SSH protocol error: %s" % raw
    if isinstance(e, socket.gaierror):
        return "cannot resolve host '%s' (check the address / your DNS)" % d.get("ip", "?")
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out waiting for %s" % host
    if isinstance(e, ConnectionRefusedError):
        return "connection refused by %s" % host
    if isinstance(e, ConnectionResetError):
        return "connection reset by %s" % host
    if isinstance(e, OSError):
        we = getattr(e, "winerror", None)
        en = getattr(e, "errno", None)
        if we == 10048 or en in (98, 10048):
            return "local port %s is already in use" % d.get("proxy_port", "?")
        if we == 10013:
            return "local port %s is blocked (permission denied)" % d.get("proxy_port", "?")
        if we == 10060:
            return "timed out waiting for %s" % host
        if we == 10061:
            return "connection refused by %s" % host
        if we in (10051, 10065):
            return "network unreachable (no route to %s)" % host
    return raw


# --------------------------------------------------------------------------- #
#  Icon Helpers (Taskbar, Title bar, Alt-Tab)
# --------------------------------------------------------------------------- #
def resolve_icon_path():
    """Finds the .ico file in source directory, frozen bundle, or MEIPASS."""
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", ""), "icon", "Unofficial_SSH_Logo.svg.ico"),
        os.path.join(getattr(sys, "_MEIPASS", ""), "Unofficial_SSH_Logo.svg.ico"),
        os.path.join(BASE_DIR, "icon", "Unofficial_SSH_Logo.svg.ico"),
        os.path.join(BASE_DIR, "Unofficial_SSH_Logo.svg.ico"),
    ]
    if "__file__" in globals() and os.path.isfile(__file__):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates.append(os.path.join(script_dir, "icon", "Unofficial_SSH_Logo.svg.ico"))
        candidates.append(os.path.join(script_dir, "Unofficial_SSH_Logo.svg.ico"))

    for p in candidates:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def apply_win32_window_icons(hwnd):
    """Sets ICON_SMALL and ICON_BIG on the Win32 window handle via SendMessage."""
    try:
        user32 = ctypes.windll.user32
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010

        ico_file = resolve_icon_path()
        hicon_big = None
        hicon_small = None

        if ico_file and os.path.isfile(ico_file):
            hicon_big = user32.LoadImageW(None, ico_file, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            hicon_small = user32.LoadImageW(None, ico_file, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        elif getattr(sys, "frozen", False):
            # In compiled executable, icon is embedded as resource ID 1
            hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
            hicon_big = user32.LoadImageW(hinstance, ctypes.c_void_p(1), IMAGE_ICON, 32, 32, 0)
            hicon_small = user32.LoadImageW(hinstance, ctypes.c_void_p(1), IMAGE_ICON, 16, 16, 0)
            if not hicon_big:
                hicon_big = user32.LoadIconW(hinstance, ctypes.c_void_p(1))
            if not hicon_small:
                hicon_small = hicon_big

        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
    except Exception:
        pass


def load_titlebar_photo_image(master, target_px=18):
    """Loads a PhotoImage for the custom title bar without external dependencies."""
    ico_path = resolve_icon_path()
    if not ico_path or not os.path.isfile(ico_path):
        return None

    # Method 1: Use PIL if installed
    if Image is not None and ImageTk is not None:
        try:
            img = Image.open(ico_path)
            resample_filter = getattr(Image, "Resampling", Image).LANCZOS
            img = img.resize((target_px, target_px), resample_filter)
            return ImageTk.PhotoImage(img, master=master)
        except Exception:
            pass

    # Method 2: Extract embedded PNG payload directly from the .ico file
    try:
        with open(ico_path, "rb") as f:
            data = f.read()
        png_idx = data.find(b"\x89PNG\r\n\x1a\n")
        if png_idx != -1:
            photo = tk.PhotoImage(data=data[png_idx:], master=master)
            orig_w = photo.width()
            if orig_w > target_px:
                sub = max(1, orig_w // target_px)
                photo = photo.subsample(sub, sub)
            return photo
    except Exception:
        pass

    return None


# --------------------------------------------------------------------------- #
#  Logging
# --------------------------------------------------------------------------- #
LOG_CONNECTIONS = False

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(threadName)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("ssh-tunnel")
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)


def _excepthook(exc_type, exc, tb):
    log.critical("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))


def _thread_excepthook(args):
    log.critical(
        "Unhandled exception in thread %s:\n%s",
        args.thread.name if args.thread else "?",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


sys.excepthook = _excepthook
threading.excepthook = _thread_excepthook


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def run_cmd(cmd, check=False, quiet=False):
    if not quiet:
        log.debug("RUN: %s", " ".join(cmd))
    res = subprocess.run(
        cmd, capture_output=True, text=True, errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )
    out = (res.stdout + res.stderr).strip()
    if res.returncode != 0 and not quiet:
        log.warning("Command exited with code %s: %s\n%s", res.returncode, " ".join(cmd), out)
    elif out and not quiet:
        log.debug("OUT: %s", out[:500])
    if check and res.returncode != 0:
        raise RuntimeError("Command failed: %s\n%s" % (" ".join(cmd), out))
    return res.stdout + res.stderr


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_aaaa_or_https_query(payload):
    """
    Detects if a DNS query is asking for:
      - Type 28: IPv6 AAAA record
      - Type 65: HTTPS / SVCB record (RFC 9460)
    Filtering both prevents stalls on unreachable IPv6 routes and prevents
    browsers from aggressively attempting QUIC (HTTP/3) from DNS hints.
    """
    try:
        if len(payload) < 12:
            return False
        qdcount = struct.unpack(">H", payload[4:6])[0]
        if qdcount < 1:
            return False
        idx = 12
        while idx < len(payload):
            length = payload[idx]
            if length == 0:
                idx += 1
                break
            idx += 1 + length
        if idx + 4 <= len(payload):
            qtype, _ = struct.unpack(">HH", payload[idx:idx+4])
            return qtype in (28, 65)
    except Exception:
        pass
    return False


def make_empty_dns_response(payload):
    """Returns a valid NOERROR DNS response with 0 answers."""
    tx_id = payload[:2]
    flags = b"\x81\x80"
    counts = payload[4:6] + b"\x00\x00\x00\x00\x00\x00"
    idx = 12
    while idx < len(payload):
        length = payload[idx]
        if length == 0:
            idx += 1
            break
        idx += 1 + length
    question = payload[12:idx + 4]
    return tx_id + flags + counts + question


# --------------------------------------------------------------------------- #
#  Administrator helpers (context menu + self-elevation)
# --------------------------------------------------------------------------- #
def _gui_python():
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    return pyw if os.path.isfile(pyw) else py


def register_admin_context_menu():
    """Adds 'Run as administrator' to context menu of .py / .pyw files."""
    if winreg is None or getattr(sys, "frozen", False):
        return
    try:
        command = '"%s" "%%1" %%*' % _gui_python()
        for ext in (".py", ".pyw"):
            base = r"Software\Classes\SystemFileAssociations\%s\shell\runas" % ext
            cmd_key = base + r"\command"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, cmd_key) as k:
                    if winreg.QueryValueEx(k, "")[0] == command:
                        continue
            except OSError:
                pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Run as &administrator")
                winreg.SetValueEx(k, "HasLUAShield", 0, winreg.REG_SZ, "")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_key) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command)
            log.info("Registered 'Run as administrator' context menu for %s", ext)
    except Exception:
        log.exception("Could not register the 'Run as administrator' context menu entry")


def unregister_admin_context_menu():
    if winreg is None:
        return
    for ext in (".py", ".pyw"):
        base = r"Software\Classes\SystemFileAssociations\%s\shell\runas" % ext
        for key in (base + r"\command", base):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass
    log.info("Removed 'Run as administrator' context menu entries")


def relaunch_as_admin(extra_args=None):
    extra_args = list(extra_args or [])
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = subprocess.list2cmdline(extra_args)
        else:
            exe = _gui_python()
            params = subprocess.list2cmdline([os.path.abspath(__file__)] + extra_args)
        log.info("Relaunching as administrator: %s %s", exe, params)
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
        ret = shell_execute(None, "runas", exe, params, BASE_DIR, 1)
        return int(ret or 0) > 32
    except Exception:
        log.exception("Relaunch as administrator failed")
        return False


def _parse_autoconnect():
    if "--autoconnect" in sys.argv:
        try:
            return int(sys.argv[sys.argv.index("--autoconnect") + 1])
        except (ValueError, IndexError):
            return None
    return None


# --------------------------------------------------------------------------- #
#  SOCKS server with Persistent DNS Channel & Full-Duplex Relay
# --------------------------------------------------------------------------- #
class SocksServer:
    def __init__(self, transport, port):
        self.transport = transport
        self.port = port
        self.listener = None
        self.running = False
        self.clients = set()
        self.lock = threading.Lock()
        self.dns_cache = {}
        self.dns_lock = threading.Lock()
        self.dns_chan = None
        self.dns_chan_lock = threading.Lock()

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(128)
        self.listener = s
        self.running = True
        threading.Thread(target=self._accept_loop, name="socks-accept", daemon=True).start()
        log.info("Local SOCKS proxy listening on 127.0.0.1:%d", self.port)

    def stop(self):
        log.info("Stopping local SOCKS proxy on port %d", self.port)
        self.running = False
        try:
            if self.listener:
                self.listener.close()
        except Exception:
            pass
        with self.dns_chan_lock:
            if self.dns_chan:
                try:
                    self.dns_chan.close()
                except Exception:
                    pass
                self.dns_chan = None
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
        for c in clients:
            try:
                c.close()
            except Exception:
                pass

    def _accept_loop(self):
        while self.running:
            try:
                client, _ = self.listener.accept()
            except OSError:
                break
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            with self.lock:
                self.clients.add(client)
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        try:
            client.settimeout(10)
            ver = recv_exact(client, 1)
            if ver == b"\x05":
                self._socks5(client)
            elif ver == b"\x04":
                self._socks4(client)
        except Exception as e:
            if LOG_CONNECTIONS:
                log.debug("SOCKS client error: %r", e)
        finally:
            try:
                client.close()
            except Exception:
                pass
            with self.lock:
                self.clients.discard(client)

    def _open(self, host, port):
        try:
            chan = self.transport.open_channel(
                "direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=10
            )
            if LOG_CONNECTIONS:
                log.debug("CONNECT %s:%s via SSH", host, port)
            return chan
        except Exception as e:
            log.debug("Cannot open SSH channel to %s:%s -> %s", host, port, e)
            return None

    @staticmethod
    def _read_addr(client, atyp):
        if atyp == 1:
            host = socket.inet_ntoa(recv_exact(client, 4))
        elif atyp == 3:
            ln = recv_exact(client, 1)[0]
            host = recv_exact(client, ln).decode("utf-8", "ignore")
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, recv_exact(client, 16))
        else:
            raise ValueError("bad address type")
        port = struct.unpack(">H", recv_exact(client, 2))[0]
        return host, port

    def _socks5(self, client):
        n = recv_exact(client, 1)[0]
        recv_exact(client, n)
        client.sendall(b"\x05\x00")
        _ver, cmd, _rsv, atyp = recv_exact(client, 4)
        host, port = self._read_addr(client, atyp)

        if cmd == 1:  # CONNECT
            chan = self._open(host, port)
            if chan is None:
                client.sendall(b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            self._relay(client, chan)
        elif cmd == 3:  # UDP ASSOCIATE
            self._udp_associate(client)
        else:
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")

    def _socks4(self, client):
        cmd = recv_exact(client, 1)[0]
        port = struct.unpack(">H", recv_exact(client, 2))[0]
        ip = recv_exact(client, 4)
        while recv_exact(client, 1) != b"\x00":
            pass
        if ip[:3] == b"\x00\x00\x00" and ip[3] != 0:
            dom = b""
            while True:
                c = recv_exact(client, 1)
                if c == b"\x00":
                    break
                dom += c
            host = dom.decode("utf-8", "ignore")
        else:
            host = socket.inet_ntoa(ip)
        if cmd != 1:
            client.sendall(b"\x00\x5b" + b"\x00" * 6)
            return
        chan = self._open(host, port)
        if chan is None:
            client.sendall(b"\x00\x5b" + b"\x00" * 6)
            return
        client.sendall(b"\x00\x5a" + b"\x00" * 6)
        self._relay(client, chan)

    def _relay(self, sock, chan):
        """
        True full-duplex threaded relay without select.select.
        Avoids Windows pipe race conditions and handles HTTP/2 multiplexing reliably.
        """
        sock.settimeout(None)
        chan.settimeout(None)

        def sock_to_chan():
            try:
                while True:
                    data = sock.recv(32768)
                    if not data:
                        break
                    chan.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    chan.close()
                except Exception:
                    pass

        def chan_to_sock():
            try:
                while True:
                    data = chan.recv(32768)
                    if not data:
                        break
                    sock.sendall(data)
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass

        t1 = threading.Thread(target=sock_to_chan, daemon=True)
        t2 = threading.Thread(target=chan_to_sock, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        try:
            sock.close()
        except Exception:
            pass
        try:
            chan.close()
        except Exception:
            pass

    def _udp_associate(self, client):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(1.0)
        client.sendall(
            b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1")
            + struct.pack(">H", udp.getsockname()[1])
        )
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    data, addr = udp.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._dns_over_tcp, args=(udp, data, addr), daemon=True
                ).start()

        threading.Thread(target=loop, daemon=True).start()
        client.settimeout(None)
        try:
            while client.recv(1024):
                pass
        except Exception:
            pass
        stop.set()
        try:
            udp.close()
        except Exception:
            pass

    def _query_dns_upstream(self, host, port, payload):
        """
        Reuses a single persistent upstream DNS channel over SSH.
        Prevents MaxSessions channel exhaustion on high concurrency.
        """
        with self.dns_chan_lock:
            for attempt in range(2):
                if self.dns_chan is None or self.dns_chan.closed:
                    self.dns_chan = self._open(host, port)
                    if self.dns_chan is None:
                        return None
                    self.dns_chan.settimeout(8)

                try:
                    self.dns_chan.sendall(struct.pack(">H", len(payload)) + payload)
                    ln = struct.unpack(">H", recv_exact(self.dns_chan, 2))[0]
                    reply = recv_exact(self.dns_chan, ln)
                    return reply
                except Exception:
                    try:
                        self.dns_chan.close()
                    except Exception:
                        pass
                    self.dns_chan = None
            return None

    def _dns_over_tcp(self, udp, data, addr):
        try:
            if len(data) < 10 or data[2] != 0:
                return
            atyp = data[3]
            if atyp == 1:
                off = 8
            elif atyp == 3:
                ln = data[4]
                off = 5 + ln
            elif atyp == 4:
                off = 20
            else:
                return
            port = struct.unpack(">H", data[off:off + 2])[0]
            payload = data[off + 2:]
            header = data[:off + 2]

            # Reject non-DNS arbitrary UDP (forces browsers to immediately fall back to TCP)
            if port != 53:
                return

            # Filter IPv6 (AAAA) and HTTPS (SVCB) queries
            if is_aaaa_or_https_query(payload):
                reply = make_empty_dns_response(payload)
                udp.sendto(b"\x00\x00\x00" + header[3:] + reply, addr)
                return

            # In-memory DNS cache
            cache_key = payload[2:]
            now = time.time()
            with self.dns_lock:
                cached = self.dns_cache.get(cache_key)
                if cached and now < cached[1]:
                    reply = payload[:2] + cached[0][2:]
                    udp.sendto(b"\x00\x00\x00" + header[3:] + reply, addr)
                    return

            reply = self._query_dns_upstream("8.8.8.8", 53, payload)
            if reply is None:
                return

            with self.dns_lock:
                self.dns_cache[cache_key] = (reply, now + 300)

            udp.sendto(b"\x00\x00\x00" + header[3:] + reply, addr)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Connection check
# --------------------------------------------------------------------------- #
def http_check_via_socks(port, timeout=10):
    """Returns (status, latency_ms, host). Raises RuntimeError with per-host details."""
    errors = []
    for host, path in CHECK_URLS:
        s = None
        t0 = time.perf_counter()
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
            s.settimeout(timeout)
            s.sendall(b"\x05\x01\x00")
            if recv_exact(s, 2) != b"\x05\x00":
                raise RuntimeError("SOCKS handshake failed")
            h = host.encode()
            s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", 80))
            rep = recv_exact(s, 4)
            if rep[1] != 0:
                raise RuntimeError("remote host unreachable through the SSH tunnel")
            if rep[3] == 1:
                recv_exact(s, 6)
            elif rep[3] == 4:
                recv_exact(s, 18)
            else:
                recv_exact(s, recv_exact(s, 1)[0] + 2)
            req = (
                "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: ssh-tunnel-manager\r\n"
                "Connection: close\r\n\r\n" % (path, host)
            )
            s.sendall(req.encode())
            buf = b""
            while b"\r\n" not in buf:
                chunk = s.recv(1024)
                if not chunk:
                    break
                buf += chunk
            ms = int((time.perf_counter() - t0) * 1000)
            parts = buf.split(b"\r\n", 1)[0].decode("latin-1").split()
            status = int(parts[1]) if len(parts) >= 2 and parts[0].startswith("HTTP/") else 0
            if status == 200:
                log.info("Check OK: http://%s%s -> HTTP 200 in %d ms", host, path, ms)
                return status, ms, host
            errors.append("%s → HTTP %s" % (host, status or "invalid reply"))
        except Exception as e:
            errors.append("%s → %s" % (host, " ".join((str(e) or e.__class__.__name__).split())))
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
    summary = "; ".join(errors) or "no response"
    log.error("Connection check failed: %s", summary)
    raise RuntimeError("tunnel check failed (%s)" % summary)


# --------------------------------------------------------------------------- #
#  Windows system proxy
# --------------------------------------------------------------------------- #
INET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _refresh_inet():
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)
        wininet.InternetSetOptionW(0, 37, 0, 0)
    except Exception:
        pass


def set_system_proxy(port):
    log.info("Setting Windows system proxy to socks=127.0.0.1:%d", port)
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "socks=127.0.0.1:%d" % port)
        winreg.SetValueEx(
            key, "ProxyOverride", 0, winreg.REG_SZ,
            "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.2*;172.3*;192.168.*;<local>",
        )
    finally:
        winreg.CloseKey(key)
    _refresh_inet()


def clear_system_proxy():
    log.info("Clearing Windows system proxy")
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, INET_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        try:
            winreg.DeleteValue(key, "ProxyServer")
        except FileNotFoundError:
            pass
    finally:
        winreg.CloseKey(key)
    _refresh_inet()


# --------------------------------------------------------------------------- #
#  TUN mode
# --------------------------------------------------------------------------- #
def _find_file(name):
    for d in (TUN_DIR, BASE_DIR):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def _colocate(exe, dll):
    target = os.path.join(os.path.dirname(exe), "wintun.dll")
    if os.path.normcase(os.path.abspath(dll)) != os.path.normcase(os.path.abspath(target)):
        log.info("Copying wintun.dll next to tun2socks.exe: %s", target)
        shutil.copy2(dll, target)
    return target


def ensure_tun_files(status_cb):
    exe = _find_file("tun2socks.exe")
    dll = _find_file("wintun.dll")
    if exe and dll:
        dll = _colocate(exe, dll)
        return exe, dll

    arch = platform.machine().lower()
    if arch in ("arm64", "aarch64"):
        t2s_arch, wt_arch = "arm64", "arm64"
    elif arch in ("amd64", "x86_64"):
        t2s_arch, wt_arch = "amd64", "amd64"
    else:
        t2s_arch, wt_arch = "386", "x86"

    os.makedirs(TUN_DIR, exist_ok=True)
    try:
        if not exe:
            status_cb("Downloading tun2socks...")
            url = ("https://github.com/xjasonlyu/tun2socks/releases/latest/download/"
                   "tun2socks-windows-%s.zip" % t2s_arch)
            with zipfile.ZipFile(io.BytesIO(_download(url))) as z:
                member = [n for n in z.namelist() if n.lower().endswith(".exe")][0]
                with open(os.path.join(TUN_DIR, "tun2socks.exe"), "wb") as f:
                    f.write(z.read(member))
        if not dll:
            status_cb("Downloading wintun driver...")
            with zipfile.ZipFile(io.BytesIO(_download("https://www.wintun.net/builds/wintun-0.14.1.zip"))) as z:
                with open(os.path.join(TUN_DIR, "wintun.dll"), "wb") as f:
                    f.write(z.read("wintun/bin/%s/wintun.dll" % wt_arch))
    except Exception as e:
        raise RuntimeError("Could not download TUN files: %s" % e)
    exe = exe or os.path.join(TUN_DIR, "tun2socks.exe")
    dll = dll or os.path.join(TUN_DIR, "wintun.dll")
    return exe, _colocate(exe, dll)


def default_gateway():
    out = run_cmd(["route", "print", "-4", "0.0.0.0"], quiet=True)
    best = None
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] == "0.0.0.0" and p[1] == "0.0.0.0":
            try:
                metric = int(p[4])
                socket.inet_aton(p[2])
            except (ValueError, OSError):
                continue
            if best is None or metric < best[0]:
                best = (metric, p[2])
    log.info("Default gateway detected: %s", best[1] if best else None)
    return best[1] if best else None


def _remove_leak_rules():
    """Removes every firewall rule created by TUN mode."""
    for name in ("SSHTunBlockIPv6", "SSHTunBlockLocalDNS", "SSHTunBlockQUIC"):
        run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", "name=%s" % name], quiet=True)
    run_cmd(["powershell", "-NoProfile", "-Command",
             "Remove-NetFirewallRule -DisplayName 'SSHTunBlockPhysDNS' -ErrorAction SilentlyContinue"],
            quiet=True)


class TunManager:
    def __init__(self):
        self.proc = None
        self.bypass_ip = None
        self.bypass_routes = []
        self.output = []

    def _pump(self, proc):
        try:
            for raw in iter(proc.stdout.readline, b""):
                txt = raw.decode("utf-8", "ignore").rstrip()
                if txt:
                    self.output.append(txt)
                    log.info("[tun2socks] %s", txt)
        except Exception:
            pass

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, socks_port, vps_host, status_cb):
        user_cb = status_cb

        def status_cb(text):
            log.info("TUN: %s", text)
            user_cb(text)

        if self.running:
            self.stop()
        if not is_admin():
            raise RuntimeError("TUN mode needs Administrator rights. Run this program as Administrator.")

        _remove_leak_rules()

        exe, dll = ensure_tun_files(status_cb)
        vps_ip = socket.gethostbyname(vps_host)
        gw = default_gateway()
        if not gw:
            raise RuntimeError("Could not detect the physical default gateway.")

        status_cb("Starting TUN adapter (gateway %s, VPS %s) ..." % (gw, vps_ip))
        env = os.environ.copy()
        env["PATH"] = os.path.dirname(dll) + os.pathsep + env.get("PATH", "")
        cmd = [
            exe, "--device", "tun://%s" % TUN_NAME,
            "--proxy", "socks5://127.0.0.1:%d" % socks_port,
            "--mtu", TUN_MTU,
            "--loglevel", "warning"
        ]
        self.output = []
        self.proc = subprocess.Popen(
            cmd, cwd=os.path.dirname(exe), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._pump, args=(self.proc,), name="tun2socks-log", daemon=True).start()

        try:
            ready = False
            for _ in range(40):
                if self.proc.poll() is not None:
                    time.sleep(0.3)
                    raise RuntimeError("tun2socks exited unexpectedly (code %s)." % self.proc.poll())
                if TUN_NAME in run_cmd(["netsh", "interface", "ipv4", "show", "interfaces"], quiet=True):
                    ready = True
                    break
                time.sleep(0.25)
            if not ready:
                raise RuntimeError("TUN adapter did not appear within 10 s.")

            # 1. Route SSH VPS connection directly via local physical gateway
            run_cmd(["route", "add", vps_ip, "mask", "255.255.255.255", gw, "metric", "1"], check=True)
            self.bypass_ip = vps_ip

            # 2. Bypass local private subnets
            self.bypass_routes = [
                ("10.0.0.0", "255.0.0.0"),
                ("172.16.0.0", "255.240.0.0"),
                ("192.168.0.0", "255.255.0.0"),
            ]
            for net, mask in self.bypass_routes:
                run_cmd(["route", "add", net, "mask", mask, gw, "metric", "1"], quiet=True)

            status_cb("Configuring TUN network & MTU %s ..." % TUN_MTU)
            run_cmd(["netsh", "interface", "ipv4", "set", "address",
                     "name=%s" % TUN_NAME, "source=static", "address=%s" % TUN_IP,
                     "mask=255.255.255.0", "gateway=%s" % TUN_IP, "gwmetric=1"], check=True)
            run_cmd(["netsh", "interface", "ipv4", "set", "interface", TUN_NAME, "metric=1"], quiet=True)

            # Clamp adapter MTU to 1380
            run_cmd(["netsh", "interface", "ipv4", "set", "subinterface", TUN_NAME,
                     "mtu=%s" % TUN_MTU, "store=active"], quiet=True)

            run_cmd(["netsh", "interface", "ipv4", "set", "dnsservers", "name=%s" % TUN_NAME,
                     "source=static", "address=8.8.8.8", "register=none", "validate=no"], quiet=True)
            run_cmd(["netsh", "interface", "ipv4", "add", "dnsservers", "name=%s" % TUN_NAME,
                     "address=1.1.1.1", "index=2", "validate=no"], quiet=True)

            # 3. Add /1 default routes
            run_cmd(["route", "add", "0.0.0.0", "mask", "128.0.0.0", TUN_IP, "metric", "1"], quiet=True)
            run_cmd(["route", "add", "128.0.0.0", "mask", "128.0.0.0", TUN_IP, "metric", "1"], quiet=True)

            status_cb("Applying IPv6 / DNS / QUIC leak protections ...")
            # 4. Anti-Leak: Block external IPv6
            run_cmd(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=SSHTunBlockIPv6", "dir=out", "action=block", "remoteip=::/0"], quiet=True)
            run_cmd(["powershell", "-NoProfile", "-Command",
                     "Disable-NetAdapterBinding -Name * -ComponentID ms_tcpip6"], quiet=True)

            # 5. Anti-Leak: Block DNS queries on physical adapters & block QUIC (UDP 443)
            run_cmd(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=SSHTunBlockLocalDNS", "dir=out", "action=block", "protocol=UDP",
                     "remoteport=53", "remoteip=%s" % gw], quiet=True)
            run_cmd(["powershell", "-NoProfile", "-Command",
                     "$n=@(Get-NetAdapter | Where-Object {$_.Name -ne '%s' -and $_.Status -eq 'Up'} "
                     "| ForEach-Object {$_.Name}); "
                     "if($n.Count -gt 0){ foreach($p in 'UDP','TCP'){ "
                     "New-NetFirewallRule -DisplayName 'SSHTunBlockPhysDNS' -Direction Outbound "
                     "-Action Block -Protocol $p -RemotePort 53 -InterfaceAlias $n | Out-Null } }" % TUN_NAME],
                    quiet=True)
            run_cmd(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name=SSHTunBlockQUIC", "dir=out", "action=block", "protocol=UDP",
                     "remoteport=443"], quiet=True)

            # 6. Flush DNS resolver cache
            run_cmd(["ipconfig", "/flushdns"], quiet=True)

        except Exception:
            self.stop()
            raise

    def stop(self):
        log.info("Stopping TUN mode and restoring network...")
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

        run_cmd(["route", "delete", "0.0.0.0", "mask", "128.0.0.0", TUN_IP], quiet=True)
        run_cmd(["route", "delete", "128.0.0.0", "mask", "128.0.0.0", TUN_IP], quiet=True)

        if self.bypass_ip:
            run_cmd(["route", "delete", self.bypass_ip], quiet=True)
            self.bypass_ip = None
        for net, _ in self.bypass_routes:
            run_cmd(["route", "delete", net], quiet=True)
        self.bypass_routes.clear()

        _remove_leak_rules()

        run_cmd(["powershell", "-NoProfile", "-Command",
                 "Enable-NetAdapterBinding -Name * -ComponentID ms_tcpip6"], quiet=True)

        run_cmd(["ipconfig", "/flushdns"], quiet=True)


tun = TunManager()


# --------------------------------------------------------------------------- #
#  UI Widgets
# --------------------------------------------------------------------------- #
def round_rect(cv, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class CircleButton(tk.Canvas):
    """
    Animated circle button.
      * hover: soft glow + grow       * press: shrink          * click: ripple
      * icon "play"/"stop" morph smoothly into each other
      * set_busy(True)   -> spinner ring
      * set_active(True) -> green fill + radar pulse (red on hover = disconnect)
      * shake_now()      -> failure shake        * blink(color) -> short color flash
      * show_text(text, kind) -> text mode with persistent green/red tint
    """
    PAD = 10
    PLAY = [(0.40, 0.28), (0.40, 0.72), (0.75, 0.50), (0.75, 0.50)]
    STOP = [(0.33, 0.33), (0.33, 0.67), (0.67, 0.67), (0.67, 0.33)]

    def __init__(self, master, size, icon, command, box=None, bg=BG):
        box = box or size
        self.size = size
        self.total = box + 2 * self.PAD
        super().__init__(master, width=self.total, height=self.total, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bgc = bg
        self.icon = icon
        self.command = command
        self.text = None
        self.enabled = True
        self.hover = False
        self.pressed = False
        self.busy = False
        self.active = False

        self.morph = 1.0 if icon == "stop" else 0.0
        self.morph_target = self.morph
        self.hv = self.pv = self.bv = self.av = self.tv = 0.0
        self.ev = 1.0
        self.spin = 0.0
        self.sphase = 0.0
        self.phase = 0.0
        self.ripples = []
        self.shake = 0.0
        self.tint_color = ORANGE
        self.tint_target = 0.0

        self._job = None
        self._last = time.perf_counter()
        self._alive = True

        self.bind("<Motion>", self._motion)
        self.bind("<Enter>", self._motion)
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False))
        self.render()

    # ------------------------------------------------------------- input
    def _inside(self, x, y):
        c = self.total / 2.0
        return (x - c) ** 2 + (y - c) ** 2 <= (self.size / 2.0 + 5) ** 2

    def _motion(self, e):
        inside = self._inside(e.x, e.y)
        if inside != self.hover:
            self._set(hover=inside)
        self.configure(cursor="hand2" if (inside and self.enabled) else "arrow")

    def _release(self, e):
        was_pressed = self.pressed
        self._set(pressed=False)
        if was_pressed and self._inside(e.x, e.y) and self.enabled and self.command:
            self.ripple()
            self.command()

    # ------------------------------------------------------------- public API
    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self._kick()

    def set_icon(self, icon):
        self.icon = icon
        self.text = None
        self.tint_target = 0.0
        if icon in ("play", "stop"):
            self.morph_target = 1.0 if icon == "stop" else 0.0
        self._kick()

    def show_text(self, text, kind="info"):
        self.text = text
        self.busy = False
        self.tint_color = {"ok": OK_COLOR, "err": ERR_COLOR}.get(kind, ORANGE)
        self.tint_target = 1.0 if kind in ("ok", "err") else 0.0
        if kind == "err":
            self.shake_now()
        elif kind == "ok":
            self.ripple(OK_COLOR)
        self._kick()

    def set_enabled(self, flag):
        self.enabled = flag
        if not flag:
            self.hover = False
            self.pressed = False
        self.configure(cursor="hand2" if flag else "arrow")
        self._kick()

    def set_busy(self, flag):
        self.busy = flag
        self._kick()

    def set_active(self, flag):
        self.active = flag
        self._kick()

    def ripple(self, color=None):
        if color is None:
            color = ERR_COLOR if self.active else ORANGE_HOVER
        self.ripples.append([0.0, color])
        self._kick()

    def shake_now(self):
        self.shake = 1.0
        self._kick()

    def blink(self, color, ms=750):
        self.tint_color = color
        self.tint_target = 1.0
        try:
            self.after(ms, self._unblink)
        except tk.TclError:
            pass
        self._kick()

    def _unblink(self):
        if self.text is None:
            self.tint_target = 0.0
            self._kick()

    # ------------------------------------------------------------- animation
    def _kick(self):
        if not self._alive:
            return
        if self._job is None:
            self._last = time.perf_counter()
            try:
                self._job = self.after(1, self._tick)
            except tk.TclError:
                self._job = None

    def _targets(self):
        return (
            1.0 if (self.hover and self.enabled) else 0.0,
            1.0 if (self.pressed and self.enabled) else 0.0,
            1.0 if self.busy else 0.0,
            1.0 if self.active else 0.0,
            1.0 if self.enabled else 0.0,
        )

    def _tick(self):
        self._job = None
        if not self._alive:
            return
        now = time.perf_counter()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now

        th, tp, tb, ta, te = self._targets()
        self.hv = approach(self.hv, th, dt * 7)
        self.pv = approach(self.pv, tp, dt * 12)
        self.bv = approach(self.bv, tb, dt * 5)
        self.av = approach(self.av, ta, dt * 3)
        self.ev = approach(self.ev, te, dt * 6)
        self.tv = approach(self.tv, self.tint_target, dt * 5)
        self.morph = approach(self.morph, self.morph_target, dt * 3.5)

        if self.bv > 0:
            self.spin = (self.spin + dt * 430) % 360
            self.sphase += dt * 5
        if self.av > 0:
            self.phase = (self.phase + dt * 0.9) % 1000.0
        self.ripples = [[a + dt / 0.75, c] for a, c in self.ripples if a + dt / 0.75 < 1.0]
        if self.shake > 0:
            self.shake = max(0.0, self.shake - dt / 0.55)

        self.render()

        again = (
            self.busy or self.active or self.bv > 0 or self.av > 0 or self.ripples
            or self.shake > 0 or self.hv != th or self.pv != tp or self.ev != te
            or self.tv != self.tint_target or self.morph != self.morph_target
        )
        if again:
            try:
                self._job = self.after(16, self._tick)
            except tk.TclError:
                self._job = None

    # ------------------------------------------------------------- drawing
    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        bg = self.bgc
        T = self.total
        cx = cy = T / 2.0
        if self.shake > 0:
            cx += math.sin(self.shake * math.pi * 7) * 7 * self.shake
        r0 = self.size / 2.0 - 2
        r = r0 * (1 + 0.07 * self.hv - 0.09 * self.pv)

        # colors
        fill = mix(mix(ORANGE, ORANGE_HOVER, self.hv), ORANGE_DOWN, self.pv)
        fill = mix(fill, mix(GREEN_FILL, RED_FILL, self.hv), self.av)
        if self.tv > 0:
            fill = mix(fill, self.tint_color, self.tv)
        fill = mix(fill, mix(fill, bg, 0.55), 1 - self.ev)

        # soft glow on hover
        if self.hv > 0:
            for i in (3, 2, 1):
                g = r + i * 2.2
                self.create_oval(cx - g, cy - g, cx + g, cy + g, outline="",
                                 fill=mix(bg, fill, 0.30 * self.hv * (1 - i / 4.0)))

        # radar pulse while connected
        if self.av > 0:
            for k in (0.0, 0.5):
                t = (self.phase + k) % 1.0
                rr = r0 + 2 + t * 8
                self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill="",
                                 outline=mix(bg, GREEN_FILL, (1 - t) * 0.55 * self.av * (1 - self.hv)),
                                 width=2)

        # click / event ripples
        for age, color in self.ripples:
            rr = r0 * 0.9 + ease(age) * 14
            self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill="",
                             outline=mix(color, bg, age), width=max(1, int(3 * (1 - age))))

        # main disc
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline=fill)
        self.create_oval(cx - r + 1.5, cy - r + 1.5, cx + r - 1.5, cy + r - 1.5, fill="",
                         outline=mix(fill, "#ffffff", 0.22))

        # spinner ring
        if self.bv > 0:
            R = r0 + 8
            self.create_oval(cx - R, cy - R, cx + R, cy + R, fill="",
                             outline=mix(bg, ORANGE, 0.22 * self.bv), width=2)
            ext = 70 + 110 * (0.5 + 0.5 * math.sin(self.sphase))
            self.create_arc(cx - R, cy - R, cx + R, cy + R, start=-self.spin, extent=-ext,
                            style="arc", outline=mix(bg, ORANGE_HOVER, self.bv), width=3)

        # icon / text
        icol = mix("#c4c4c4", "#ffffff", self.ev)
        d = 2 * r
        if self.text:
            self.create_text(cx, cy, text=self.text, fill=icol,
                             font=("Segoe UI", max(9, int(self.size * 0.19)), "bold"))
        elif self.icon in ("play", "stop"):
            e = ease(self.morph)
            pts = []
            for (px0, py0), (px1, py1) in zip(self.PLAY, self.STOP):
                px = px0 + (px1 - px0) * e
                py = py0 + (py1 - py0) * e
                pts += [cx + (px - 0.5) * d, cy + (py - 0.5) * d]
            self.create_polygon(*pts, fill=icol, outline=icol, width=2, joinstyle="round")
        elif self.icon == "bolt":
            u = d / 100.0
            pts = [(58, 8), (26, 54), (46, 54), (38, 92), (76, 42), (55, 42)]
            flat = []
            for px, py in pts:
                flat += [cx + (px - 51) * u, cy + (py - 50) * u]
            self.create_polygon(*flat, fill=icol, outline=icol, width=1, joinstyle="round")


class ActivityBar(tk.Canvas):
    """Thin animated bar: sweeping while busy, green when connected, red flash on failure."""

    def __init__(self, master, bg=BG):
        self.sc = master.winfo_fpixels("1i") / 96.0
        self.h = max(3, int(4 * self.sc))
        super().__init__(master, width=10, height=self.h, bg=bg,
                         highlightthickness=0, bd=0)
        self.mode = "idle"
        self.t = 0.0
        self.grow = 0.0
        self.errv = 0.0
        self._job = None
        self._last = time.perf_counter()
        self._alive = True
        self.bind("<Configure>", lambda e: self.render())
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False))

    def set_mode(self, mode):
        if mode == self.mode and mode in ("idle", "busy", "on"):
            return
        self.mode = mode
        if mode == "on":
            self.grow = 0.0
        elif mode == "err":
            self.errv = 1.0
        self._kick()

    def _kick(self):
        if not self._alive:
            return
        if self._job is None:
            self._last = time.perf_counter()
            try:
                self._job = self.after(1, self._tick)
            except tk.TclError:
                self._job = None

    def _tick(self):
        self._job = None
        if not self._alive:
            return
        now = time.perf_counter()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        self.t += dt
        if self.mode == "on":
            self.grow = min(1.0, self.grow + dt / 0.8)
        elif self.mode == "err":
            self.errv = max(0.0, self.errv - dt / 1.6)
            if self.errv == 0.0:
                self.mode = "idle"
        self.render()
        if self.mode in ("busy", "on", "err"):
            try:
                self._job = self.after(33, self._tick)
            except tk.TclError:
                self._job = None

    def _band(self, x, width, color, base, lo, hi, strength=1.0):
        n = 14
        for i in range(n):
            x1 = x + width * i / n
            x2 = x + width * (i + 1) / n + 1
            a = math.sin(math.pi * (i + 0.5) / n) * strength
            x1c, x2c = max(lo, x1), min(hi, x2)
            if x2c > x1c:
                self.create_rectangle(x1c, 0, x2c, self.h, fill=mix(base, color, a), outline="")

    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width()
        if w <= 2:
            return
        h = self.h
        round_rect(self, 0, 0, w, h, h / 2.0, fill=FIELD, outline=FIELD)
        if self.mode == "busy":
            seg = w * 0.32
            x = ((self.t * 0.9) % 1.0) * (w + seg) - seg
            self._band(x, seg, ORANGE, FIELD, 0, w)
        elif self.mode == "on":
            fw = w * ease(self.grow)
            if fw > 0:
                self.create_rectangle(0, 0, fw, h, fill=GREEN_FILL, outline="")
                if self.grow >= 1.0:
                    sh = w * 0.20
                    sx = ((self.t * 0.40) % 1.8) * (w + sh) / 1.0 - sh
                    self._band(sx, sh, "#9af0b3", GREEN_FILL, 0, fw, 0.65)
        elif self.mode == "err":
            self.create_rectangle(0, 0, w, h, fill=mix(FIELD, ERR_COLOR, ease(self.errv)), outline="")


class StatusLine(tk.Canvas):
    """
    ONE-LINE status pill. The text can never wrap or push the layout:
    it is ellipsized to the available width. Hover shows the full text, click copies it.
    """
    COLORS = {"info": MUTED, "ok": OK_COLOR, "err": ERR_COLOR, "busy": ORANGE}

    def __init__(self, master, bg=BG):
        self.sc = master.winfo_fpixels("1i") / 96.0
        self.font = tkfont.Font(family="Segoe UI", size=9)
        self.h = int(38 * self.sc)
        super().__init__(master, width=10, height=self.h, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.text = "Not connected"
        self.kind = "info"
        self.c_from = self.c_to = self.COLORS["info"]
        self.fade = 1.0
        self.pulse = 0.0
        self.flash = 0.0
        self.truncated = False
        self._job = None
        self._last = time.perf_counter()
        self._alive = True
        self._tip = None
        self._tip_job = None
        self.bind("<Configure>", lambda e: self.render())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", lambda e: self._hide_tip())
        self.bind("<Button-1>", self._copy)
        self.bind("<Destroy>", self._destroyed)

    # ------------------------------------------------------------- API
    def current_color(self):
        return mix(self.c_from, self.c_to, ease(self.fade))

    def set(self, text, kind="info"):
        text = " ".join(str(text).split())      # force a single line
        if text == self.text and kind == self.kind:
            return
        # Same kind, only the text changed (e.g. a countdown): update in place,
        # no fade restart, so the pill does not flicker every second.
        if kind == self.kind:
            self.text = text
            self._kick()
            self.render()
            return
        self.c_from = self.current_color()
        self.c_to = self.COLORS.get(kind, MUTED)
        self.kind = kind
        self.text = text
        self.fade = 0.0
        self._hide_tip()
        self._kick()

    # ------------------------------------------------------------- tooltip / copy
    def _enter(self, e):
        self._cancel_tip_job()
        try:
            self._tip_job = self.after(450, self._show_tip)
        except tk.TclError:
            self._tip_job = None

    def _cancel_tip_job(self):
        if self._tip_job is not None:
            try:
                self.after_cancel(self._tip_job)
            except Exception:
                pass
            self._tip_job = None

    def _show_tip(self):
        self._tip_job = None
        if not self.truncated or self._tip is not None:
            return
        try:
            tip = tk.Toplevel(self)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            lbl = tk.Label(tip, text=self.text, bg="#3a3a3a", fg=FG, font=self.font,
                           justify="left", wraplength=max(260, self.winfo_width()),
                           padx=10, pady=6, bd=1, relief="solid")
            lbl.pack()
            tip.update_idletasks()
            x = self.winfo_rootx()
            y = self.winfo_rooty() - tip.winfo_reqheight() - 6
            tip.wm_geometry("+%d+%d" % (x, max(0, y)))
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide_tip(self):
        self._cancel_tip_job()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _copy(self, e):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.text)
        except tk.TclError:
            return
        self.flash = 1.0
        self._kick()

    def _destroyed(self, e):
        self._alive = False
        self._hide_tip()

    # ------------------------------------------------------------- animation
    def _kick(self):
        if not self._alive:
            return
        if self._job is None:
            self._last = time.perf_counter()
            try:
                self._job = self.after(1, self._tick)
            except tk.TclError:
                self._job = None

    def _tick(self):
        self._job = None
        if not self._alive:
            return
        now = time.perf_counter()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        self.fade = min(1.0, self.fade + dt / 0.35)
        self.pulse += dt * 4.5
        self.flash = max(0.0, self.flash - dt / 0.5)
        self.render()
        if self.fade < 1.0 or self.kind == "busy" or self.flash > 0:
            try:
                self._job = self.after(30, self._tick)
            except tk.TclError:
                self._job = None

    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width()
        if w <= 2:
            return
        h, sc = self.h, self.sc
        f = ease(self.fade)
        col = self.current_color()

        border = mix(BORDER, col, 0.45 * f) if self.kind != "info" else BORDER
        if self.flash > 0:
            border = mix(border, FG, 0.6 * self.flash)
        round_rect(self, 1, 1, w - 1, h - 1, h / 2.0, fill=FIELD, outline=border)

        cy = h / 2.0
        dx = 20 * sc
        r = 4 * sc
        if self.kind == "busy":
            r *= 1 + 0.28 * math.sin(self.pulse)
        if self.kind == "ok" and self.fade < 1.0:
            rr = r * (1 + 2.4 * f)
            self.create_oval(dx - rr, cy - rr, dx + rr, cy + rr, fill="",
                             outline=mix(col, FIELD, f), width=2)
        self.create_oval(dx - r, cy - r, dx + r, cy + r, fill=col, outline=col)

        tx = 36 * sc
        avail = w - tx - 18 * sc
        shown = fit_text(self.font, self.text, avail)
        self.truncated = shown != self.text
        target = MUTED if self.kind == "info" else FG
        tcol = mix(FIELD, target, f)
        self.create_text(tx + (1 - f) * 8 * sc, cy, text=shown, anchor="w",
                         fill=tcol, font=self.font)


class OneLineLabel(tk.Canvas):
    """Fixed-height single-line label (ellipsized, never wraps)."""

    def __init__(self, master, bg=BG, size=9, color=MUTED):
        sc = master.winfo_fpixels("1i") / 96.0
        self.font = tkfont.Font(family="Segoe UI", size=size)
        h = self.font.metrics("linespace") + int(6 * sc)
        super().__init__(master, width=10, height=h, bg=bg, highlightthickness=0, bd=0)
        self.h = h
        self.text = ""
        self.color = color
        self.bind("<Configure>", lambda e: self.render())

    def set(self, text, color=None):
        self.text = " ".join(str(text).split())
        if color:
            self.color = color
        self.render()

    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w = self.winfo_width()
        if w <= 2:
            return
        txt = fit_text(self.font, self.text, w - 6)
        self.create_text(w / 2.0, self.h / 2.0, text=txt, fill=self.color, font=self.font)


class SegmentedSwitch(tk.Canvas):
    """Pill switch whose highlight glides smoothly between options."""

    def __init__(self, master, variable, options, width=380, height=42, bg=BG):
        self.sc = master.winfo_fpixels("1i") / 96.0
        self.w = int(width * self.sc)
        self.h = int(height * self.sc)
        super().__init__(master, width=self.w, height=self.h, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self.options = options
        self.enabled = True
        self.hover = None
        self.pos = float(self._index_of(self.var.get()))
        self.target = self.pos
        self._job = None
        self._alive = True
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._click)
        self.bind("<Destroy>", lambda e: setattr(self, "_alive", False))
        self.var.trace_add("write", lambda *a: self._retarget())
        self.render()

    def _index_of(self, value):
        for i, (v, _) in enumerate(self.options):
            if v == value:
                return i
        return 0

    def _retarget(self):
        self.target = float(self._index_of(self.var.get()))
        self._kick()

    def _kick(self):
        if not self._alive or self._job is not None:
            return
        try:
            self._job = self.after(1, self._step)
        except tk.TclError:
            self._job = None

    def _step(self):
        self._job = None
        if not self._alive:
            return
        diff = self.target - self.pos
        if abs(diff) < 0.004:
            self.pos = self.target
            self.render()
            return
        self.pos += diff * 0.26
        self.render()
        try:
            self._job = self.after(16, self._step)
        except tk.TclError:
            self._job = None

    def _index_at(self, x):
        n = len(self.options)
        return max(0, min(n - 1, int(x / (self.w / float(n)))))

    def _motion(self, e):
        self._set_hover(self._index_at(e.x) if self.enabled else None)

    def _set_hover(self, idx):
        if idx != self.hover:
            self.hover = idx
            self.render()

    def _click(self, e):
        if not self.enabled:
            return
        self.var.set(self.options[self._index_at(e.x)][0])

    def set_enabled(self, flag):
        self.enabled = flag
        self.hover = None
        self.configure(cursor="hand2" if flag else "arrow")
        self.render()

    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w, h = self.w, self.h
        pad = max(3, int(3 * self.sc))
        n = len(self.options)
        seg = (w - 2 * pad) / float(n)
        round_rect(self, 1, 1, w - 1, h - 1, h / 2.0, fill=FIELD, outline=BORDER)

        x1 = pad + self.pos * seg
        x2 = x1 + seg
        fill = ORANGE if self.enabled else ORANGE_OFF
        round_rect(self, x1, pad, x2, h - pad, (h - 2 * pad) / 2.0, fill=fill, outline=fill)

        for i, (value, label) in enumerate(self.options):
            near = max(0.0, 1.0 - abs(self.pos - i))
            if self.enabled:
                base = FG if self.hover == i else MUTED
                sel = "#ffffff"
            else:
                base, sel = "#6f6f6f", "#cfcfcf"
            color = mix(base, sel, near)
            font = ("Segoe UI", 10, "bold") if near > 0.5 else ("Segoe UI", 10)
            cx = pad + i * seg + seg / 2.0
            self.create_text(cx, h / 2.0, text=label, fill=color, font=font)


# --------------------------------------------------------------------------- #
#  Copy icon inside input fields
# --------------------------------------------------------------------------- #
class CopyButton(tk.Canvas):
    """
    Small animated copy icon that lives INSIDE an entry field.
      * hover : icon brightens with a soft round highlight
      * press : icon shrinks slightly
      * click : value is copied, the copy icon shrinks/fades while a checkmark is
                drawn stroke by stroke (green) with a small pop + ripple
      * after COPY_RESET_MS the checkmark morphs back into the copy icon
    """
    CHECK = [(0.20, 0.54), (0.42, 0.74), (0.80, 0.30)]
    BACK = (0.34, 0.12, 0.86, 0.64)     # rear sheet (outline only)
    FRONT = (0.14, 0.34, 0.66, 0.88)    # front sheet (filled with the field color)

    def __init__(self, master, get_text, sc=1.0, bg=FIELD):
        self.sc = sc
        self.s = max(16, int(COPY_ICON_SIZE * sc))
        super().__init__(master, width=self.s, height=self.s, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bgc = bg
        self.get_text = get_text
        self.hover = False
        self.pressed = False
        self.hv = 0.0           # hover amount   0..1
        self.pv = 0.0           # press amount   0..1
        self.morph = 0.0        # 0 = copy icon, 1 = checkmark
        self.morph_target = 0.0
        self.rp = 1.0           # ripple progress (1.0 = inactive)
        self._job = None
        self._reset_job = None
        self._last = time.perf_counter()
        self._alive = True

        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Destroy>", self._destroyed)
        self.render()

    # ------------------------------------------------------------- API
    def set_bg(self, color):
        """Keeps the icon background identical to the entry's (enabled/disabled) field color."""
        self.bgc = color
        try:
            self.configure(bg=color)
        except tk.TclError:
            return
        self.render()

    # ------------------------------------------------------------- input
    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self._kick()

    def _release(self, e):
        was_pressed = self.pressed
        self._set(pressed=False)
        if was_pressed and 0 <= e.x <= self.s and 0 <= e.y <= self.s:
            self._do_copy()

    def _do_copy(self):
        try:
            text = self.get_text()
        except Exception:
            text = ""
        if not text:
            return      # nothing to copy -> no misleading checkmark
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            return
        self.morph_target = 1.0
        self.rp = 0.0
        # (re)start the 1-second countdown after the LAST click
        if self._reset_job is not None:
            try:
                self.after_cancel(self._reset_job)
            except Exception:
                pass
        try:
            self._reset_job = self.after(COPY_RESET_MS, self._reset)
        except tk.TclError:
            self._reset_job = None
        self._kick()

    def _reset(self):
        self._reset_job = None
        self.morph_target = 0.0
        self._kick()

    def _destroyed(self, e):
        self._alive = False
        if self._reset_job is not None:
            try:
                self.after_cancel(self._reset_job)
            except Exception:
                pass
            self._reset_job = None

    # ------------------------------------------------------------- animation
    def _kick(self):
        if not self._alive:
            return
        if self._job is None:
            self._last = time.perf_counter()
            try:
                self._job = self.after(1, self._tick)
            except tk.TclError:
                self._job = None

    def _tick(self):
        self._job = None
        if not self._alive:
            return
        now = time.perf_counter()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now

        th = 1.0 if self.hover else 0.0
        tp = 1.0 if self.pressed else 0.0
        self.hv = approach(self.hv, th, dt * 8)
        self.pv = approach(self.pv, tp, dt * 14)
        self.morph = approach(self.morph, self.morph_target, dt * 4.5)
        if self.rp < 1.0:
            self.rp = min(1.0, self.rp + dt / 0.5)

        self.render()

        again = (self.hv != th or self.pv != tp or self.morph != self.morph_target
                 or self.rp < 1.0)
        if again:
            try:
                self._job = self.after(16, self._tick)
            except tk.TclError:
                self._job = None

    # ------------------------------------------------------------- drawing
    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        s = self.s
        c = s / 2.0
        bg = self.bgc
        t = ease(self.morph)
        sc = self.sc
        k = 1.0 - 0.10 * self.pv + 0.04 * self.hv

        # soft round highlight on hover
        if self.hv > 0:
            rr = s / 2.0 - 1
            self.create_oval(c - rr, c - rr, c + rr, c + rr, outline="",
                             fill=mix(bg, "#ffffff", 0.08 * self.hv))

        # click ripple
        if self.rp < 1.0:
            rr = s * 0.28 + ease(self.rp) * s * 0.22
            self.create_oval(c - rr, c - rr, c + rr, c + rr, fill="",
                             outline=mix(OK_COLOR, bg, self.rp), width=max(1, int(2 * sc)))

        lw = max(1, int(round(1.5 * sc)))

        # --- copy icon (shrinks + fades out while morphing) ---
        if t < 1.0:
            kk = k * (1.0 - 0.45 * t)
            u = s * 0.74 * kk
            col = mix(mix(MUTED, FG, self.hv), bg, t)

            def P(x, y):
                return c + (x - 0.5) * u, c + (y - 0.5) * u

            bx1, by1 = P(self.BACK[0], self.BACK[1])
            bx2, by2 = P(self.BACK[2], self.BACK[3])
            round_rect(self, bx1, by1, bx2, by2, 2 * sc * kk, fill="", outline=col, width=lw)
            fx1, fy1 = P(self.FRONT[0], self.FRONT[1])
            fx2, fy2 = P(self.FRONT[2], self.FRONT[3])
            round_rect(self, fx1, fy1, fx2, fy2, 2 * sc * kk, fill=bg, outline=col, width=lw)

        # --- checkmark (drawn progressively with a small pop) ---
        p = max(0.0, min(1.0, (self.morph - 0.25) / 0.75))
        if p > 0:
            p = ease(p)
            kk = k * (1.0 + 0.22 * math.sin(math.pi * p))
            u = s * 0.78 * kk
            pts = [(c + (x - 0.5) * u, c + (y - 0.5) * u) for x, y in self.CHECK]
            l1 = math.dist(pts[0], pts[1])
            l2 = math.dist(pts[1], pts[2])
            goal = p * (l1 + l2)
            path = [pts[0]]
            if goal <= l1:
                f = goal / l1 if l1 else 1.0
                path.append((pts[0][0] + (pts[1][0] - pts[0][0]) * f,
                             pts[0][1] + (pts[1][1] - pts[0][1]) * f))
            else:
                path.append(pts[1])
                f = (goal - l1) / l2 if l2 else 1.0
                path.append((pts[1][0] + (pts[2][0] - pts[1][0]) * f,
                             pts[1][1] + (pts[2][1] - pts[1][1]) * f))
            flat = [v for pt in path for v in pt]
            if len(path) >= 2 and goal > 0.5:
                self.create_line(*flat, fill=OK_COLOR, width=max(2, int(round(2.2 * sc))),
                                 capstyle="round", joinstyle="round")


class CopyEntry(ttk.Entry):
    """
    ttk.Entry with an animated copy icon inside, at the right end of the field.
    The right padding of the text area is enlarged (style 'Copy.TEntry'), so typed
    text never runs underneath the icon.
    """
    DISABLED_BG = PANEL

    def __init__(self, master, textvariable, show="", **kw):
        sc = master.winfo_fpixels("1i") / 96.0
        super().__init__(master, textvariable=textvariable, show=show,
                         style="Copy.TEntry", **kw)
        self._var = textvariable
        self.copy_btn = CopyButton(self, lambda: self._var.get(), sc=sc, bg=FIELD)
        self.copy_btn.place(relx=1.0, x=-int(COPY_ICON_MARGIN * sc), rely=0.5, anchor="e")

    def set_locked(self, locked):
        self.configure(state="disabled" if locked else "normal")
        self.copy_btn.set_bg(self.DISABLED_BG if locked else FIELD)


# --------------------------------------------------------------------------- #
#  VpsTab
# --------------------------------------------------------------------------- #
class VpsTab(ttk.Frame):
    def __init__(self, app, data=None):
        super().__init__(app.content, padding=(22, 14))
        self.app = app
        data = data or {}

        self.v_remarks = tk.StringVar(value=data.get("remarks", ""))
        self.v_ip = tk.StringVar(value=data.get("ip", ""))
        self.v_port = tk.StringVar(value=data.get("port", "22"))
        self.v_user = tk.StringVar(value=data.get("username", ""))
        self.v_pass = tk.StringVar(value=data.get("password", ""))
        self.v_proxy_port = tk.StringVar(value=data.get("proxy_port", "1080"))
        mode = data.get("mode", "proxy")
        self.v_mode = tk.StringVar(value=mode if mode in ("proxy", "tun") else "proxy")
        self.v_show = tk.BooleanVar(value=False)

        self.client = None
        self.server = None
        self.connected = False
        self.busy = False
        self.mode_applied = None
        self.active_port = None
        self.monitor_stop = None
        self.connected_at = None
        self._testing = False
        self._disconnecting = False
        self._disc_thread = None

        # --- auto-reconnect state ---
        self.auto_retry = False          # True while we are in the reconnect loop
        self._cancel_requested = False   # user clicked "Cancel" during an attempt
        self._retry_job = None           # Tk after() id of the countdown
        self._retry_left = 0             # seconds left until the next attempt
        self._retry_count = 0            # number of attempts made so far
        self._retry_prefix = ""          # text shown before the countdown
        self._last_data = None           # connection data of the last successful/started connect

        self._build()
        self.v_remarks.trace_add("write", lambda *a: self.app.update_title(self))
        self.v_ip.trace_add("write", lambda *a: self.app.update_title(self))
        self.v_mode.trace_add("write", lambda *a: self._update_mode_hint())
        self._update_mode_hint()

    # ------------------------------------------------------------- layout
    def _build(self):
        self.columnconfigure(0, weight=1)

        # animated activity bar
        self.activity = ActivityBar(self)
        self.activity.grid(row=0, column=0, sticky="ew", pady=(0, 12))

        # form card
        card = tk.Frame(self, bg=PANEL, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.grid(row=1, column=0, sticky="ew")
        inner = ttk.Frame(card, style="Card.TFrame", padding=(18, 14))
        inner.pack(fill="both", expand=True)
        inner.columnconfigure(1, weight=1)

        rows = [
            ("Remarks", self.v_remarks, False),
            ("IP / Host", self.v_ip, False),
            ("SSH Port", self.v_port, False),
            ("Username", self.v_user, False),
            ("Password", self.v_pass, True),
        ]
        self.entries = []
        for i, (label, var, secret) in enumerate(rows):
            ttk.Label(inner, text=label, style="Card.TLabel").grid(
                row=i, column=0, sticky="w", pady=4, padx=(0, 14))
            e = CopyEntry(inner, var, show="•" if secret else "")
            e.grid(row=i, column=1, sticky="ew", pady=4)
            self.entries.append(e)
            if secret:
                self.pass_entry = e
                ttk.Checkbutton(inner, text="Show", variable=self.v_show,
                                style="Card.TCheckbutton",
                                command=self._toggle_pass).grid(row=i, column=2, padx=(10, 0))

        ttk.Label(inner, text="Local proxy port", style="Card.TLabel").grid(
            row=5, column=0, sticky="w", pady=(12, 4), padx=(0, 14))
        e = CopyEntry(inner, self.v_proxy_port, width=14)
        e.grid(row=5, column=1, sticky="w", pady=(12, 4))
        self.entries.append(e)

        self.mode_switch = SegmentedSwitch(
            inner, self.v_mode,
            [("proxy", "Set system proxy"), ("tun", "TUN mode (all traffic)")],
            bg=PANEL,
        )
        self.mode_switch.grid(row=6, column=0, columnspan=3, pady=(12, 4))

        self.hint1 = OneLineLabel(inner, bg=PANEL, size=9)
        self.hint1.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.hint2 = OneLineLabel(inner, bg=PANEL, size=9)
        self.hint2.grid(row=8, column=0, columnspan=3, sticky="ew")

        # animated circle buttons with captions
        btns = ttk.Frame(self)
        btns.grid(row=2, column=0, pady=(12, 0))

        col1 = ttk.Frame(btns)
        col1.pack(side="left", padx=26)
        self.btn_connect = CircleButton(col1, 72, "play", self.toggle)
        self.btn_connect.pack()
        self.cap_connect = ttk.Label(col1, text="Connect", style="Caption.TLabel")
        self.cap_connect.pack()

        col2 = ttk.Frame(btns)
        col2.pack(side="left", padx=26)
        self.btn_test = CircleButton(col2, 54, "bolt", self.test_connection, box=72)
        self.btn_test.pack()
        self.cap_test = ttk.Label(col2, text="Test tunnel", style="Caption.TLabel")
        self.cap_test.pack()

        # ONE-LINE status pill
        self.status = StatusLine(self)
        self.status.grid(row=3, column=0, sticky="ew", pady=(8, 0))

    def _update_mode_hint(self):
        if self.v_mode.get() == "tun":
            self.hint1.set("Routes ALL traffic through a virtual adapter · IPv6 / DNS leak protection", MUTED)
            if is_admin():
                self.hint2.set("Running as Administrator ✓", OK_COLOR)
            else:
                self.hint2.set("Needs Administrator · you will be offered to restart as admin", MUTED)
        else:
            self.hint1.set("Routes apps that use the Windows system proxy", MUTED)
            self.hint2.set("No Administrator rights needed", MUTED)

    def _toggle_pass(self):
        self.pass_entry.configure(show="" if self.v_show.get() else "•")

    def set_status(self, text, kind="info"):
        self.status.set(text, kind)

    def _post_status(self, text, kind="busy"):
        """Thread-safe status update (text is captured by value)."""
        self.app.post(lambda: self.set_status(text, kind))

    def _lock_fields(self, locked):
        for e in self.entries:
            e.set_locked(locked)
        self.mode_switch.set_enabled(not locked)

    def get_data(self):
        return {
            "remarks": self.v_remarks.get().strip(),
            "ip": self.v_ip.get().strip(),
            "port": self.v_port.get().strip(),
            "username": self.v_user.get().strip(),
            "password": self.v_pass.get(),
            "proxy_port": self.v_proxy_port.get().strip(),
            "mode": self.v_mode.get(),
        }

    def _validate(self, d):
        if not d["ip"]:
            return "Enter the IP / host of your VPS."
        if not d["username"]:
            return "Enter the username."
        for key, label in (("port", "SSH port"), ("proxy_port", "Local proxy port")):
            if not d[key].isdigit() or not (1 <= int(d[key]) <= 65535):
                return "%s must be a number between 1 and 65535." % label
        return None

    # ------------------------------------------------------------- connect
    def toggle(self):
        # While auto-reconnecting, the button acts as "Cancel"
        if self.auto_retry:
            self.cancel_reconnect()
            return
        if self.busy:
            return
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        if paramiko is None:
            messagebox.showerror("Missing module", "Install paramiko first:\n\npip install paramiko")
            return
        d = self.get_data()
        err = self._validate(d)
        if err:
            messagebox.showwarning("Check your input", err)
            self.btn_connect.shake_now()
            return

        if d["mode"] == "tun" and not is_admin():
            answer = messagebox.askyesno(
                "Administrator rights required",
                "TUN mode needs Administrator rights.\n\n"
                "Restart the program as Administrator now?\n"
                "Your settings are saved and this connection will start automatically.",
            )
            if answer and not self.app.restart_as_admin(self):
                self.set_status("Administrator request was cancelled or failed", "err")
                self.btn_connect.shake_now()
            return

        d["port"] = int(d["port"])
        d["proxy_port"] = int(d["proxy_port"])
        self._last_data = dict(d)       # remembered for auto-reconnect

        self.app.release_system_owner(self)
        self.busy = True
        self.btn_connect.set_enabled(False)
        self.btn_connect.set_busy(True)
        self.cap_connect.configure(text="Connecting…")
        self.activity.set_mode("busy")
        self._lock_fields(True)
        self.set_status("Connecting to %s:%d as '%s' (%s mode) ..."
                        % (d["ip"], d["port"], d["username"],
                           "TUN" if d["mode"] == "tun" else "system proxy"), "busy")
        threading.Thread(target=self._connect_worker, args=(d,), name="connect", daemon=True).start()

    def _connect_worker(self, d):
        client = server = None
        applied = None
        stage = "SSH connect"
        t_all = time.perf_counter()
        try:
            t0 = time.perf_counter()
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                d["ip"], port=d["port"], username=d["username"], password=d["password"],
                timeout=15, banner_timeout=20, auth_timeout=20,
                allow_agent=False, look_for_keys=False,
            )
            ssh_ms = int((time.perf_counter() - t0) * 1000)
            transport = client.get_transport()
            transport.set_keepalive(KEEPALIVE_SECONDS)
            try:
                if hasattr(transport, "sock") and transport.sock:
                    transport.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass

            stage = "Local SOCKS"
            self._post_status("SSH login OK (%d ms) · starting local SOCKS on 127.0.0.1:%d ..."
                              % (ssh_ms, d["proxy_port"]))
            server = SocksServer(transport, d["proxy_port"])
            server.start()

            stage = "Verification"
            self._post_status("SSH login OK (%d ms) · verifying tunnel via %s ..."
                              % (ssh_ms, CHECK_URLS[0][0]))
            status, ms, chk_host = http_check_via_socks(d["proxy_port"])

            if d["mode"] == "proxy":
                stage = "System proxy"
                self._post_status("Response status %d (%d ms) · applying Windows system proxy ..."
                                  % (status, ms))
                set_system_proxy(d["proxy_port"])
                applied = "proxy"
            else:
                stage = "TUN"
                self._post_status("Response status %d (%d ms) · preparing TUN mode ..." % (status, ms))
                tun.start(d["proxy_port"], d["ip"],
                          lambda t: self._post_status("TUN · %s" % t))
                applied = "tun"

            info = {"status": status, "ms": ms, "host": chk_host, "ssh_ms": ssh_ms,
                    "total": time.perf_counter() - t_all}
            self.app.post(lambda: self._on_connected(client, server, info, d, applied))
        except Exception as e:
            log.exception("Connection failed")
            reason = describe_error(stage, e, d)
            elapsed = time.perf_counter() - t_all
            try:
                if applied == "proxy":
                    clear_system_proxy()
                elif applied == "tun":
                    tun.stop()
            except Exception:
                pass
            if server:
                server.stop()
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            self.app.post(lambda: self._on_failed(stage, reason, elapsed))

    def _on_connected(self, client, server, info, d, applied):
        self.client, self.server = client, server
        self.connected = True
        self.busy = False
        self.mode_applied = applied
        self.active_port = d["proxy_port"]
        self.connected_at = time.time()
        self._last_data = dict(d)

        # The user pressed "Cancel" while this reconnect attempt was in flight:
        # the attempt succeeded, but we tear it down again right away.
        if self.auto_retry and self._cancel_requested:
            self.auto_retry = False
            self._cancel_requested = False
            self.disconnect("Auto-reconnect cancelled")
            return

        was_retry = self.auto_retry
        attempts = self._retry_count
        self.auto_retry = False
        self._cancel_requested = False
        self._retry_count = 0

        self.btn_connect.set_busy(False)
        self.btn_connect.set_active(True)
        self.btn_connect.set_icon("stop")
        self.btn_connect.set_enabled(True)
        self.btn_connect.ripple(OK_COLOR)
        self.cap_connect.configure(text="Disconnect")
        self.activity.set_mode("on")

        where = ("System proxy 127.0.0.1:%d" % d["proxy_port"] if applied == "proxy"
                 else "TUN mode active (leak protected)")
        head = ("Reconnected after %d attempt%s · " % (attempts, "" if attempts == 1 else "s")
                if was_retry else "")
        self.set_status(
            "%sResponse status %d · Connected to %s:%d · %d ms · %s · SSH login %d ms · total %.1f s · via %s"
            % (head, info["status"], d["ip"], d["port"], info["ms"], where,
               info["ssh_ms"], info["total"], info["host"]), "ok")
        self.app.update_title(self)
        self.app.save_ini()

        self.monitor_stop = threading.Event()
        threading.Thread(target=self._monitor, args=(client.get_transport(), self.monitor_stop),
                         name="monitor", daemon=True).start()

    def _on_failed(self, stage, reason, elapsed):
        self.busy = False
        self.client = self.server = None

        # --- auto-reconnect loop: failed attempt -> wait and try again ---
        if self.auto_retry:
            if self._cancel_requested:
                self._end_retry("Auto-reconnect cancelled · last error at %s: %s" % (stage, reason))
            else:
                self._schedule_retry("Attempt %d failed at %s · %s" % (self._retry_count, stage, reason))
            return

        self.btn_connect.set_busy(False)
        self.btn_connect.set_active(False)
        self.btn_connect.set_icon("play")
        self.btn_connect.set_enabled(True)
        self.btn_connect.shake_now()
        self.btn_connect.blink(ERR_COLOR)
        self.cap_connect.configure(text="Connect")
        self.activity.set_mode("err")
        self._lock_fields(False)
        self.set_status("Failed at %s · %s · after %.1f s" % (stage, reason, elapsed), "err")

    def _monitor(self, transport, stop):
        while not stop.is_set():
            if not transport.is_active():
                if not stop.is_set():
                    log.warning("SSH transport is no longer active - connection lost")
                    self.app.post(lambda: self._on_connection_lost("Connection lost (SSH transport dropped)"))
                return
            stop.wait(2)

    # ------------------------------------------------------------- auto-reconnect
    def _on_connection_lost(self, reason):
        """Called (UI thread) when the monitor notices the SSH transport died."""
        if not self.connected or self._disconnecting:
            return
        if not self._last_data:
            # Should not happen, but fall back to a plain disconnect
            self.disconnect(reason)
            return
        log.warning("Connection lost - auto-reconnect every %d s is enabled", RECONNECT_DELAY)
        self.auto_retry = True
        self._cancel_requested = False
        self._retry_count = 0
        # Tear down in the background; _finish_disconnect starts the countdown
        self.disconnect(reason)

    def _schedule_retry(self, prefix):
        """Shows the 'Cancel' state and starts the N-second countdown to the next attempt."""
        self.busy = False
        self.connected = False
        self._retry_prefix = prefix
        self._retry_left = RECONNECT_DELAY

        self.btn_connect.set_busy(False)
        self.btn_connect.set_active(False)
        self.btn_connect.set_icon("stop")
        self.btn_connect.set_enabled(True)
        self.cap_connect.configure(text="Cancel")
        self.activity.set_mode("busy")
        self._lock_fields(True)
        self.app.update_title(self)

        self._cancel_retry_job()
        self._retry_tick()

    def _cancel_retry_job(self):
        if self._retry_job is not None:
            try:
                self.after_cancel(self._retry_job)
            except Exception:
                pass
            self._retry_job = None

    def _retry_tick(self):
        self._retry_job = None
        if not self.auto_retry or self._cancel_requested:
            return
        if self._retry_left <= 0:
            self._start_retry_attempt()
            return
        self.set_status("%s · reconnecting in %ds (attempt %d) · click to cancel"
                        % (self._retry_prefix, self._retry_left, self._retry_count + 1), "busy")
        self._retry_left -= 1
        try:
            self._retry_job = self.after(1000, self._retry_tick)
        except tk.TclError:
            self._retry_job = None

    def _start_retry_attempt(self):
        d = dict(self._last_data or {})
        if not d:
            self._end_retry("Auto-reconnect stopped (no connection data)")
            return
        self._retry_count += 1
        log.info("Auto-reconnect attempt %d to %s:%s", self._retry_count, d.get("ip"), d.get("port"))

        self.app.release_system_owner(self)
        self.busy = True
        self.btn_connect.set_busy(True)
        self.btn_connect.set_enabled(True)       # stays clickable = Cancel
        self.cap_connect.configure(text="Cancel")
        self.activity.set_mode("busy")
        self.set_status("Reconnecting to %s:%d as '%s' (attempt %d) ..."
                        % (d["ip"], d["port"], d["username"], self._retry_count), "busy")
        threading.Thread(target=self._connect_worker, args=(d,), name="reconnect", daemon=True).start()

    def cancel_reconnect(self):
        """User pressed the button while auto-reconnect is waiting / running."""
        self._cancel_requested = True
        self._cancel_retry_job()
        if self.busy:
            # An attempt is in flight; its result handler finishes the cancel.
            self.btn_connect.set_enabled(False)
            self.set_status("Cancelling auto-reconnect ...", "busy")
            return
        self._end_retry("Auto-reconnect cancelled")

    def stop_retry_silently(self):
        """Used when closing a tab / the app: just stop the loop, no UI work."""
        self.auto_retry = False
        self._cancel_requested = True
        self._cancel_retry_job()

    def _end_retry(self, message):
        """Leaves the auto-reconnect loop and returns the tab to the idle state."""
        self._cancel_retry_job()
        self.auto_retry = False
        self._cancel_requested = False
        self._retry_count = 0
        self.busy = False
        self.connected = False
        self.btn_connect.set_busy(False)
        self.btn_connect.set_active(False)
        self.btn_connect.set_icon("play")
        self.btn_connect.set_enabled(True)
        self.cap_connect.configure(text="Connect")
        self.activity.set_mode("idle")
        self._lock_fields(False)
        self.set_status(message, "info")
        self.app.update_title(self)

    # ------------------------------------------------------------- disconnect
    def _teardown(self):
        """Blocking cleanup (no UI calls). Returns a list of what was done."""
        notes = []
        if self.monitor_stop:
            self.monitor_stop.set()
        try:
            if self.mode_applied == "proxy":
                clear_system_proxy()
                notes.append("system proxy cleared")
            elif self.mode_applied == "tun":
                tun.stop()
                notes.append("TUN stopped, routes & firewall restored")
        except Exception as e:
            log.exception("Error while clearing proxy/TUN")
            notes.append("cleanup error: %s" % (str(e) or e.__class__.__name__))
        self.mode_applied = None
        if self.server:
            try:
                self.server.stop()
                notes.append("local SOCKS stopped")
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
                notes.append("SSH closed")
            except Exception:
                pass
        self.client = self.server = None
        return notes

    def disconnect(self, reason=None, wait=False):
        """
        wait=False: teardown runs in a background thread (smooth animation).
        wait=True : blocking, used when closing tabs / the app / switching owner.
        """
        if self._disconnecting:
            if wait and self._disc_thread is not None:
                self._disc_thread.join(timeout=20)
            return
        if not self.connected:
            return

        # An explicit blocking disconnect (closing tab/app, another tab taking over)
        # must never be followed by an automatic reconnect.
        if wait:
            self.stop_retry_silently()
            self._cancel_requested = False

        uptime = fmt_dur(time.time() - self.connected_at) if self.connected_at else None
        self._disconnecting = True
        self.busy = True
        self.btn_connect.set_enabled(False)
        self.btn_connect.set_busy(True)
        self.cap_connect.configure(text="Disconnecting…")
        self.activity.set_mode("busy")
        self.set_status("%s · stopping tunnel and restoring network ..."
                        % (reason or "Disconnecting"), "busy")

        if wait:
            notes = self._teardown()
            self._finish_disconnect(reason, uptime, notes)
        else:
            def worker():
                try:
                    notes = self._teardown()
                except Exception:
                    log.exception("Teardown failed")
                    notes = ["cleanup error"]
                self.app.post(lambda: self._finish_disconnect(reason, uptime, notes))

            self._disc_thread = threading.Thread(target=worker, name="disconnect", daemon=True)
            self._disc_thread.start()

    def _finish_disconnect(self, reason, uptime, notes):
        self.connected = False
        self.busy = False
        self._disconnecting = False
        self.connected_at = None

        # --- connection was lost: keep the lock and start the reconnect countdown ---
        if self.auto_retry and not self._cancel_requested:
            prefix = reason or "Connection lost"
            if uptime:
                prefix += " (uptime %s)" % uptime
            self._schedule_retry(prefix)
            return
        if self.auto_retry:   # cancelled while tearing down
            self._end_retry("Auto-reconnect cancelled")
            return

        self.btn_connect.set_busy(False)
        self.btn_connect.set_active(False)
        self.btn_connect.set_icon("play")
        self.btn_connect.set_enabled(True)
        self.btn_connect.ripple(ORANGE_HOVER)
        self.cap_connect.configure(text="Connect")
        self.activity.set_mode("err" if reason else "idle")
        self._lock_fields(False)

        parts = [reason or "Disconnected"]
        if uptime:
            parts.append("uptime %s" % uptime)
        parts += notes
        self.set_status(" · ".join(parts), "err" if reason else "info")
        self.app.update_title(self)

    # ------------------------------------------------------------- test button
    def test_connection(self):
        if self._testing:
            return
        self._testing = True
        if not self.connected or not self.active_port or self._disconnecting:
            self.set_status("Test skipped · no active tunnel (connect first)", "info")
            self._flash("OFF", "err")
            return
        self.btn_test.set_enabled(False)
        self.btn_test.set_busy(True)
        self.set_status("Testing tunnel via %s ..." % CHECK_URLS[0][0], "busy")
        threading.Thread(target=self._test_worker, name="test", daemon=True).start()

    def _test_worker(self):
        uptime = fmt_dur(time.time() - self.connected_at) if self.connected_at else "?"
        try:
            status, ms, host = http_check_via_socks(self.active_port)
            text = "%dms" % ms
            self._post_status("Response status %d · Tunnel OK · %d ms · via %s · uptime %s"
                              % (status, ms, host, uptime), "ok")
            kind = "ok"
        except Exception as e:
            text = "ERR"
            msg = " ".join((str(e) or e.__class__.__name__).split())
            self._post_status("Test failed · %s · uptime %s" % (msg, uptime), "err")
            kind = "err"
        self.app.post(lambda: self._flash(text, kind))

    def _flash(self, text, kind="info"):
        self.btn_test.set_busy(False)
        self.btn_test.set_enabled(True)
        self.btn_test.show_text(text, kind)
        self.after(2000, self._reset_test)

    def _reset_test(self):
        try:
            self.btn_test.set_icon("bolt")
        except tk.TclError:
            pass
        self._testing = False


# --------------------------------------------------------------------------- #
#  Custom Title Bar Helpers
# --------------------------------------------------------------------------- #
def titlebar_mode():
    if TITLEBAR_STYLE != "auto":
        return TITLEBAR_STYLE
    try:
        build = sys.getwindowsversion().build
    except Exception:
        return "dark"
    return "match" if build >= 22000 else "custom"


class TitleButton(tk.Canvas):
    def __init__(self, master, kind, command, w, h):
        super().__init__(master, width=w, height=h, bg=TITLE_BG, highlightthickness=0, bd=0)
        self.kind = kind
        self.command = command
        self.w, self.h = w, h
        self.hover = False
        has_mdl2 = "Segoe MDL2 Assets" in tkfont.families()
        if has_mdl2:
            self.glyph = "\uE921" if kind == "min" else "\uE8BB"
            self.font = ("Segoe MDL2 Assets", 9)
        else:
            self.glyph = "\u2014" if kind == "min" else "\u2715"
            self.font = ("Segoe UI", 10)
        self.bind("<Enter>", lambda e: self._set(True))
        self.bind("<Leave>", lambda e: self._set(False))
        self.bind("<ButtonRelease-1>", self._release)
        self.render()

    def _set(self, flag):
        self.hover = flag
        self.render()

    def _release(self, e):
        if 0 <= e.x <= self.w and 0 <= e.y <= self.h:
            self.command()

    def render(self):
        self.delete("all")
        if self.hover:
            bg = "#E81123" if self.kind == "close" else "#3a3a3a"
        else:
            bg = TITLE_BG
        self.configure(bg=bg)
        self.create_text(self.w / 2.0, self.h / 2.0, text=self.glyph,
                         fill="white" if self.hover else FG, font=self.font)


# --------------------------------------------------------------------------- #
#  Custom Canvas TabBar
#  - every VPS tab handle has the same width
#  - the "+" handle is compact (PLUS_TAB_W) and sits right after the last tab
# --------------------------------------------------------------------------- #
class TabBar(tk.Canvas):
    def __init__(self, master, on_select, on_add, on_close):
        self.sc = master.winfo_fpixels("1i") / 96.0
        self.h = int(TAB_H * self.sc)
        super().__init__(master, height=self.h, bg=BG, highlightthickness=0, bd=0)
        self.font = tkfont.Font(family="Segoe UI", size=10)
        self.plus_font = tkfont.Font(family="Segoe UI", size=14)
        self.on_select = on_select
        self.on_add = on_add
        self.on_close = on_close
        self.labels = []
        self.selected = -1
        self.hover = None
        self.bind("<Configure>", lambda e: self.render())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._click)
        self.bind("<Button-2>", self._middle)

    def _layout(self):
        """Returns [(x1, x2), ...] for every VPS tab followed by the compact '+' tab."""
        sc = self.sc
        width = max(self.winfo_width(), 1)
        n = len(self.labels)
        margin = 2 * sc
        gap = 3 * sc
        plus_w = PLUS_TAB_W * sc

        if n > 0:
            avail = width - 2 * margin - gap * n - plus_w
            tab_w = max(44 * sc, min(TAB_W * sc, avail / float(n)))
        else:
            tab_w = TAB_W * sc

        rects = []
        x = margin
        for _ in range(n):
            rects.append((x, x + tab_w))
            x += tab_w + gap
        rects.append((x, x + plus_w))
        return rects

    def _index_at(self, x):
        for i, (x1, x2) in enumerate(self._layout()):
            if x1 <= x <= x2:
                return i
        return None

    def _motion(self, e):
        self._set_hover(self._index_at(e.x))

    def _set_hover(self, idx):
        if idx != self.hover:
            self.hover = idx
            self.render()

    def _click(self, e):
        i = self._index_at(e.x)
        if i is None:
            return
        if i == len(self.labels):
            self.on_add()
        else:
            self.on_select(i)

    def _middle(self, e):
        i = self._index_at(e.x)
        if i is not None and i < len(self.labels):
            self.on_close(i)

    def set_tabs(self, labels, selected):
        self.labels = list(labels)
        self.selected = selected
        self.render()

    def _ellipsize(self, text, maxw):
        if self.font.measure(text) <= maxw:
            return text
        while text and self.font.measure(text + "…") > maxw:
            text = text[:-1]
        return (text.rstrip() + "…") if text else "…"

    def render(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        sc = self.sc
        width = max(self.winfo_width(), 1)
        h = self.h
        radius = 6 * sc
        top = 5 * sc
        pad = 10 * sc
        n = len(self.labels)
        rects = self._layout()

        self.create_line(0, h - 1, width, h - 1, fill=BORDER)

        for i, (x1, x2) in enumerate(rects):
            is_plus = (i == n)
            selected = (not is_plus) and i == self.selected
            if selected:
                fill = "#3a3a3a"
            elif self.hover == i:
                fill = "#333333"
            else:
                fill = "#2a2a2a"

            round_rect(self, x1, top, x2, h + radius, radius, fill=fill, outline=fill)
            if selected:
                self.create_rectangle(x1 + radius, top, x2 - radius, top + 2 * sc,
                                      fill=ORANGE, outline=ORANGE)

            cy = (top + h) / 2.0 + 1
            if is_plus:
                color = FG if self.hover == i else MUTED
                self.create_text((x1 + x2) / 2.0, cy - 1, text="+", fill=color, font=self.plus_font)
                continue

            text, connected = self.labels[i]
            left = x1 + pad
            if connected:
                r = 4 * sc
                self.create_oval(left, cy - r, left + 2 * r, cy + r, fill=OK_COLOR, outline=OK_COLOR)
                left += 2 * r + 6 * sc
            right = x2 - pad
            text = self._ellipsize(text, max(10, right - left))
            if selected:
                color = "#ffffff"
            elif self.hover == i:
                color = FG
            else:
                color = MUTED
            self.create_text((left + right) / 2.0, cy, text=text, fill=color, font=self.font)


# --------------------------------------------------------------------------- #
#  Main Application Window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title("SSH Tunnel Manager" + (" [Administrator]" if is_admin() else ""))
        self._tb_mode = titlebar_mode()
        self.geometry("%dx%d" % (WIN_W, WIN_H))
        self.minsize(WIN_MIN_W, WIN_MIN_H)
        self.configure(bg=BG)

        # 1. Apply Window & Taskbar Icons
        self._title_icon_photo = None
        self._apply_all_icons()

        if self._tb_mode == "custom":
            self.overrideredirect(True)
            self.configure(highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
            x = max(0, (self.winfo_screenwidth() - WIN_W) // 2)
            y = max(0, (self.winfo_screenheight() - WIN_H) // 2 - 20)
            self.geometry("%dx%d+%d+%d" % (WIN_W, WIN_H, x, y))

        self.q = queue.Queue()
        self.tab_list = []
        self.current = None

        self._apply_theme()

        if self._tb_mode == "custom":
            self._build_titlebar()

        self.bar = TabBar(self, self._on_bar_select, self._on_bar_add, self._on_bar_close)
        self.bar.pack(fill="x", padx=8, pady=(8, 0))
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        if self._tb_mode == "custom":
            self.grip = ttk.Sizegrip(self)
            self.grip.place(relx=1.0, rely=1.0, anchor="se")

        self._load_ini()
        if not self.tab_list:
            self.add_tab()
        self.select_tab(self.tab_list[0])
        self._fit_height()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll)

        self.update_idletasks()
        if self._tb_mode == "custom":
            self._set_appwindow()
        else:
            self._dark_titlebar()

        # Reassert icons on HWND once mapped
        self._apply_hwnd_icons()
        self.deiconify()

        idx = _parse_autoconnect()
        if idx is not None:
            saved = self._saved_tabs()
            if 0 <= idx < len(saved):
                tab = saved[idx]
                self.select_tab(tab)
                self.after(700, tab.connect)

    def _fit_height(self):
        """Sizes the window height to exactly match its content."""
        try:
            self.update_idletasks()
            h = self.winfo_reqheight()
            if h < 200:
                return
            self.minsize(WIN_MIN_W, h)
            if self._tb_mode == "custom":
                x = max(0, (self.winfo_screenwidth() - WIN_W) // 2)
                y = max(0, (self.winfo_screenheight() - h) // 2 - 20)
                self.geometry("%dx%d+%d+%d" % (WIN_W, h, x, y))
            else:
                self.geometry("%dx%d" % (WIN_W, h))
        except Exception:
            log.exception("Could not fit the window height to its content")

    def _apply_all_icons(self):
        """Sets icons via Tkinter wm_iconbitmap and prepares titlebar photo."""
        ico_file = resolve_icon_path()
        if ico_file and os.path.isfile(ico_file):
            try:
                self.iconbitmap(default=ico_file)
            except Exception:
                try:
                    self.iconbitmap(ico_file)
                except Exception:
                    pass
        elif getattr(sys, "frozen", False) and os.path.isfile(sys.executable):
            try:
                self.iconbitmap(default=sys.executable)
            except Exception:
                try:
                    self.iconbitmap(sys.executable)
                except Exception:
                    pass

        # Load icon for custom title bar
        self._title_icon_photo = load_titlebar_photo_image(self, target_px=18)

    def _apply_hwnd_icons(self):
        """Ensures that Win32 window handles receive WM_SETICON for Taskbar & Alt+Tab."""
        try:
            self.update_idletasks()
            hwnd_child = self.winfo_id()
            apply_win32_window_icons(hwnd_child)
            hwnd_parent = ctypes.windll.user32.GetParent(hwnd_child)
            if hwnd_parent:
                apply_win32_window_icons(hwnd_parent)
        except Exception:
            pass

    def _apply_theme(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     troughcolor=BG, focuscolor=BG, font=("Segoe UI", 10))
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=6)
        st.map("TEntry",
               fieldbackground=[("disabled", PANEL)],
               foreground=[("disabled", "#7a7a7a")],
               bordercolor=[("focus", ORANGE)],
               lightcolor=[("focus", ORANGE)],
               darkcolor=[("focus", ORANGE)])

        # Entry with room on the right for the copy icon (text never runs under it)
        sc = self.winfo_fpixels("1i") / 96.0
        right_pad = int((COPY_ICON_SIZE + COPY_ICON_MARGIN + 6) * sc)
        st.configure("Copy.TEntry", padding=(6, 6, right_pad, 6))

        st.configure("TSizegrip", background=BG)
        st.configure("TCheckbutton", background=BG, foreground=MUTED,
                     indicatorbackground=FIELD, indicatorforeground="#e0e0e0")
        st.map("TCheckbutton", background=[("active", BG)],
               indicatorbackground=[("selected", "#e0e0e0")])
        st.configure("TButton", background="#333333", foreground=FG, padding=(12, 6),
                     borderwidth=1, relief="flat")
        st.map("TButton", background=[("active", "#444444"), ("pressed", "#2a2a2a")])

        # Card styles (form panel)
        st.configure("Card.TFrame", background=PANEL)
        st.configure("Card.TLabel", background=PANEL, foreground=FG)
        st.configure("Card.TCheckbutton", background=PANEL, foreground=MUTED,
                     indicatorbackground=FIELD, indicatorforeground="#e0e0e0")
        st.map("Card.TCheckbutton", background=[("active", PANEL)],
               indicatorbackground=[("selected", "#e0e0e0")])

        # Captions under circle buttons
        st.configure("Caption.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))

    def _build_titlebar(self):
        sc = self.winfo_fpixels("1i") / 96.0
        h = int(32 * sc)
        bar = tk.Frame(self, bg=TITLE_BG, height=h)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Title bar icon (for custom overrideredirect mode)
        if self._title_icon_photo:
            icon_lbl = tk.Label(bar, image=self._title_icon_photo, bg=TITLE_BG)
            icon_lbl.pack(side="left", padx=(int(10 * sc), 0))
            icon_lbl.bind("<ButtonPress-1>", self._drag_start)
            icon_lbl.bind("<B1-Motion>", self._drag_move)

        lbl_pad_left = int(6 * sc) if self._title_icon_photo else int(12 * sc)
        lbl = tk.Label(bar, text=self.title(), bg=TITLE_BG, fg=MUTED, font=("Segoe UI", 9))
        lbl.pack(side="left", padx=(lbl_pad_left, 0))

        btn_w = int(46 * sc)
        TitleButton(bar, "close", self._on_close, btn_w, h).pack(side="right")
        TitleButton(bar, "min", self._minimize, btn_w, h).pack(side="right")

        for w in (bar, lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
        self.titlebar = bar

    def _drag_start(self, e):
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e):
        self.geometry("+%d+%d" % (e.x_root - self._dx, e.y_root - self._dy))

    def _set_appwindow(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            style = user32.GetWindowLongW(hwnd, -20)
            style = (style & ~0x00000080) | 0x00040000
            user32.SetWindowLongW(hwnd, -20, style)
            self._apply_hwnd_icons()
        except Exception:
            log.exception("Could not set the taskbar style")

    def _minimize(self):
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            user32.ShowWindow(hwnd, 6)
        except Exception:
            pass

    def _dark_titlebar(self):
        if self._tb_mode == "default":
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id()) or self.winfo_id()
            dwm = ctypes.windll.dwmapi

            def dwm_set(attr, value):
                v = ctypes.c_int(value)
                return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

            def colorref(hex_color):
                r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
                return (b << 16) | (g << 8) | r

            dwm_set(20, 1)
            dwm_set(19, 1)
            if self._tb_mode == "match":
                dwm_set(35, colorref(BG))
                dwm_set(36, colorref(FG))
                dwm_set(34, colorref(BORDER))
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        log.critical("Tkinter callback error:\n%s", "".join(traceback.format_exception(exc, val, tb)))

    def post(self, fn):
        self.q.put(fn)

    def _poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            log.exception("Error in UI queue handler")
        self.after(50, self._poll)

    def tabs(self):
        return list(self.tab_list)

    def _saved_tabs(self):
        out = []
        for tab in self.tab_list:
            d = tab.get_data()
            if d["remarks"] or d["ip"] or d["username"]:
                out.append(tab)
        return out

    @staticmethod
    def _name(tab):
        d = tab.get_data()
        return d["remarks"] or d["ip"] or "New VPS"

    def refresh_bar(self):
        labels = [(self._name(t), t.connected) for t in self.tab_list]
        sel = self.tab_list.index(self.current) if self.current in self.tab_list else -1
        self.bar.set_tabs(labels, sel)

    def update_title(self, tab):
        try:
            self.refresh_bar()
        except tk.TclError:
            pass

    def add_tab(self, data=None, select=True):
        tab = VpsTab(self, data)
        tab.grid(row=0, column=0, sticky="nsew", in_=self.content)
        self.tab_list.append(tab)
        if select:
            self.select_tab(tab)
        else:
            tab.grid_remove()
            self.refresh_bar()
        return tab

    def select_tab(self, tab):
        self.current = tab
        for t in self.tab_list:
            if t is tab:
                t.grid()
            else:
                t.grid_remove()
        self.refresh_bar()

    def _on_bar_select(self, idx):
        if 0 <= idx < len(self.tab_list):
            self.select_tab(self.tab_list[idx])

    def _on_bar_add(self):
        self.add_tab()

    def _on_bar_close(self, idx):
        if 0 <= idx < len(self.tab_list):
            self.close_tab(self.tab_list[idx])

    def close_tab(self, tab):
        tab.stop_retry_silently()
        if tab.connected:
            tab.disconnect(wait=True)
        pos = self.tab_list.index(tab)
        self.tab_list.remove(tab)
        tab.destroy()
        if not self.tab_list:
            self.add_tab()
        else:
            if self.current is tab:
                self.select_tab(self.tab_list[max(0, pos - 1)])
            else:
                self.refresh_bar()
        self.save_ini()

    def release_system_owner(self, current):
        for t in self.tabs():
            if t is current:
                continue
            # Another tab that is waiting to auto-reconnect gives up its turn
            if t.auto_retry and not t.busy:
                t.cancel_reconnect()
            if t.connected:
                t.disconnect("Disconnected: another connection took over", wait=True)

    def restart_as_admin(self, tab):
        self.save_ini()
        args = []
        saved = self._saved_tabs()
        if tab in saved:
            args = ["--autoconnect", str(saved.index(tab))]
        if not relaunch_as_admin(args):
            return False
        self.destroy()
        return True

    def save_ini(self):
        cp = configparser.ConfigParser(interpolation=None)
        i = 0
        for tab in self._saved_tabs():
            i += 1
            cp["vps%d" % i] = tab.get_data()
        try:
            with open(INI_PATH, "w", encoding="utf-8") as f:
                cp.write(f)
            log.info("Saved %d VPS entry to %s", i, INI_PATH)
        except OSError as e:
            messagebox.showerror("Save error", "Could not write %s\n%s" % (INI_PATH, e))

    def _load_ini(self):
        if not os.path.isfile(INI_PATH):
            return
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(INI_PATH, encoding="utf-8")
        except Exception:
            return
        for section in cp.sections():
            self.add_tab(dict(cp[section]), select=False)

    def _on_close(self):
        for t in self.tabs():
            t.stop_retry_silently()
        for t in self.tabs():
            if t.connected:
                t.disconnect(wait=True)
        try:
            tun.stop()
        except Exception:
            pass
        self.destroy()


# --------------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------------- #
def main():
    if "--remove-admin-menu" in sys.argv:
        unregister_admin_context_menu()
        return

    # 1. Register explicit AppUserModelID so Windows taskbar isolates and binds the custom icon
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SSHTunnelManager.App.1.0")
    except Exception:
        pass

    # 2. DPI Awareness
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    if winreg is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Unsupported", "This program is for Windows only.")
        return
    if paramiko is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Missing module", "Install paramiko first:\n\npip install paramiko")
        return

    register_admin_context_menu()
    App().mainloop()


if __name__ == "__main__":
    main()