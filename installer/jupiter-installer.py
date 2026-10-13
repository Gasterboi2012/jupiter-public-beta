#!/usr/bin/env python3
"""Jupiter TUI installer.

Runs on the live ISO at boot (tty1). Asks for:
  1. GPU driver (NVIDIA open kernel modules, or open-source/Mesa)
  2. Target disk (it will be wiped)
  3. A user account
Then installs the Jupiter base: Helium browser, Hyprland, and the
Dank Material Shell (DMS) desktop, via dnf into the target root.
"""
import argparse
import curses
import getpass
import json
import os
import shutil
import re
import shlex
import subprocess
import sys
import textwrap

RELEASE = "43"
TARGET = "/mnt/jupiter"
LOG = "/tmp/jupiter-install.log"
RPMFUSION_NONFREE = (
    f"https://mirrors.rpmfusion.org/nonfree/fedora/"
    f"rpmfusion-nonfree-release-{RELEASE}.noarch.rpm"
)

BASE_PACKAGES = [
    # system
    "@core", "kernel", "NetworkManager", "sudo", "glibc-langpack-en",
    "grub2-efi-x64", "grub2-efi-x64-modules", "grub2-pc", "shim-x64",
    "efibootmgr", "dosfstools", "e2fsprogs", "util-linux",
    # Hyprland + display manager
    "hyprland", "xdg-desktop-portal-hyprland", "sddm", "foot",
    "pipewire", "wireplumber", "grim", "slurp", "wl-clipboard",
    "google-noto-sans-fonts", "papirus-icon-theme", "plymouth", "plymouth-scripts",
    # Dank Material Shell (DMS) + Quickshell
    "dms", "quickshell", "dgop", "matugen", "cava",
    # Browser
    "helium-bin",
]

# Placeholder boot splash. Replace the script/theme with your own artwork;
# the installer writes this theme into the target and makes it the default.
SPLASH_NAME = "jupiter"
SPLASH_FILES = {
    f"{SPLASH_NAME}.plymouth": """\
[Plymouth Theme]
Name=Jupiter
Description=Jupiter placeholder boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/jupiter
ScriptFile=/usr/share/plymouth/themes/jupiter/jupiter.script
""",
    f"{SPLASH_NAME}.script": """\
# PLACEHOLDER splash - replace with your own artwork.
Window.SetBackgroundTopColor(0.05, 0.05, 0.10);
Window.SetBackgroundBottomColor(0.05, 0.05, 0.10);
logo = Image.Text("Jupiter", 0.85, 0.75, 1.0);
logo.SetX(Window.GetX() + Window.GetWidth() / 2 - logo.GetWidth() / 2);
logo.SetY(Window.GetY() + Window.GetHeight() / 2 - logo.GetHeight() / 2);
sprite = Sprite(logo);
""",
}

NVIDIA_PACKAGES = ["akmod-nvidia-open", "xorg-x11-drv-nvidia-cuda"]
NVIDIA_KARGS = "nvidia-drm.modeset=1 nvidia-drm.fbdev=1"

COPR_REPOS = {
    "avengemedia/dms": "DMS",
    "avengemedia/danklinux": "DankLinux",
    "imput/helium": "Helium",
}


# ---------------------------------------------------------------- helpers
def partition_path(disk, number):
    """/dev/sda -> /dev/sda1, /dev/nvme0n1 -> /dev/nvme0n1p1."""
    sep = "p" if disk[-1].isdigit() else ""
    return f"{disk}{sep}{number}"


def validate_username(name):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name):
        return "Use lowercase letters, digits, '_' or '-' (start with a letter)."
    if name in {"root", "bin", "daemon", "adm", "sys", "nobody"}:
        return "That name is reserved by the system."
    return None


def validate_password(pw, confirm):
    if len(pw) < 6:
        return "Password must be at least 6 characters."
    if pw != confirm:
        return "Passwords do not match."
    return None


def build_plan(disk, gpu):
    """Return the ordered list of (description, argv) steps.

    Passwords are never part of argv; they are fed on stdin by run_step.
    """
    esp = partition_path(disk, 1)
    root = partition_path(disk, 2)
    steps = [
        ("Partitioning disk", ["sgdisk", "--zap-all", disk]),
        ("Creating EFI partition",
         ["sgdisk", "-n1:0:+512M", "-t1:EF00", "-c1:JUPITER-ESP", disk]),
        ("Creating root partition",
         ["sgdisk", "-n2:0:0", "-t2:8304", "-c2:JUPITER-ROOT", disk]),
        ("Refreshing partition table", ["partprobe", disk]),
        ("Formatting EFI", ["mkfs.fat", "-F32", "-n", "JUPESP", esp]),
        ("Formatting root", ["mkfs.ext4", "-F", "-L", "jupiter", root]),
        ("Mounting target", ["mount", root, TARGET]),
        ("Mounting EFI", ["mount", "--mkdir", esp, f"{TARGET}/boot/efi"]),
    ]
    # The live image ships the COPR repos (DMS, Helium) in /etc/yum.repos.d;
    # reposdir makes dnf use them for the target root too.
    dnf = ["dnf", f"--installroot={TARGET}", f"--releasever={RELEASE}",
           "--setopt=reposdir=/etc/yum.repos.d",
           "--setopt=install_weak_deps=False", "-y"]
    steps.append(("Installing Jupiter base (this takes a while)",
                  dnf + ["install"] + BASE_PACKAGES))
    if gpu == "nvidia":
        steps.append(("Enabling RPM Fusion (NVIDIA driver source)",
                      dnf + ["install", RPMFUSION_NONFREE]))
        steps.append(("Installing NVIDIA open kernel driver",
                      dnf + ["install"] + NVIDIA_PACKAGES))
    return steps


def chroot(argv):
    return ["chroot", TARGET] + argv


def post_install_plan(username, gpu, hostname="jupiter"):
    """Steps that run inside the target after packages are installed.

    The password is passed on stdin by install(), never in argv.
    """
    steps = [
        ("Preparing chroot", ["sh", "-c", " && ".join(
            f"mount --rbind /{d} {TARGET}/{d} && mount --make-rslave {TARGET}/{d}"
            for d in ("dev", "proc", "sys", "run"))]),
        ("Setting hostname", ["sh", "-c", f"echo {hostname} > {TARGET}/etc/hostname"]),
        ("Creating user account",
         chroot(["useradd", "-m", "-G", "wheel", "-s", "/bin/bash", username])),
        ("Setting user password", chroot(["chpasswd"])),
        ("Enabling services",
         chroot(["systemctl", "enable", "NetworkManager", "sddm"])),
        ("Enabling DMS user service for all users",
         chroot(["systemctl", "--global", "enable", "dms.service"])),
        ("Installing GRUB (UEFI)",
         chroot(["grub2-install", "--target=x86_64-efi",
                 "--efi-directory=/boot/efi", "--bootloader-id=Jupiter"])),
    ]
    if gpu == "nvidia":
        steps.append(("Enabling NVIDIA kernel options",
                      chroot(["grubby", "--update-kernel=ALL",
                              f"--args={NVIDIA_KARGS}"])))
    steps.append(("Generating GRUB config",
                  chroot(["grub2-mkconfig", "-o", "/boot/grub2/grub.cfg"])))
    return steps


HYPR_CONF = """\
# Jupiter default Hyprland config. Edit freely.
monitor = , preferred, auto, 1

$terminal = foot
$mod = SUPER

# Start the Dank Material Shell (user service) with the session
exec-once = systemctl --user start dms

input {{
    kb_layout = us
    follow_mouse = 1
}}

general {{
    gaps_in = 5
    gaps_out = 10
    border_size = 2
    col.active_border = rgba(a78bfaee) rgba(60a5faee) 45deg
    col.inactive_border = rgba(595959aa)
    layout = dwindle
}}

decoration {{
    rounding = 10
    blur {{
        enabled = true
        size = 6
        passes = 2
    }}
}}

animations {{
    enabled = true
}}

bind = $mod, Return, exec, $terminal
bind = $mod, B, exec, helium
bind = $mod, Q, killactive
bind = $mod, M, exit
bind = $mod, V, togglefloating
"""


def fstab_text(root_uuid, esp_uuid):
    return (
        f"UUID={root_uuid}\t/\text4\tdefaults\t1 1\n"
        f"UUID={esp_uuid}\t/boot/efi\tvfat\tumask=0077,shortname=winnt\t0 2\n"
    )


# ---------------------------------------------------------------- running
def log(msg):
    with open(LOG, "a") as fh:
        fh.write(msg + "\n")


def run_step(desc, argv, dry, stdin_text=None):
    shown = " ".join(shlex.quote(a) for a in argv)
    print(f"\033[1;35m==>\033[0m {desc}")
    log(f"$ {shown}")
    if dry:
        print(f"    [dry-run] {shown}")
        return 0
    proc = subprocess.run(argv, input=stdin_text, text=True,
                          capture_output=True)
    if proc.stdout:
        log(proc.stdout)
    if proc.stderr:
        log(proc.stderr)
    if proc.returncode != 0:
        print(f"\033[1;31m    failed (exit {proc.returncode}).\033[0m "
              f"See {LOG}")
        print(proc.stderr[-1500:])
        return proc.returncode
    return 0


# ---------------------------------------------------------------- network
def network_online():
    out = subprocess.run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"],
                         capture_output=True, text=True).stdout.strip()
    return out == "full"


def parse_wifi_list(text):
    """Parse `nmcli -t -f SSID,SECURITY device wifi list` into [(ssid, sec)]."""
    nets = {}
    for line in text.splitlines():
        # nmcli escapes literal colons in -t output as "\\:"
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 2:
            continue
        ssid = parts[0].replace("\\:", ":")
        if ssid:
            nets.setdefault(ssid, parts[-1].strip() or "open")
    return sorted(nets.items())


def scan_wifi():
    out = subprocess.run(
        ["nmcli", "-t", "-f", "SSID,SECURITY", "device", "wifi", "list",
         "--rescan", "yes"], capture_output=True, text=True).stdout
    return parse_wifi_list(out)


def connect_wifi(ssid, password):
    """Connect via NetworkManager. Returns (ok, message)."""
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        argv += ["password", password]
    proc = subprocess.run(argv, capture_output=True, text=True)
    log(f"$ nmcli device wifi connect {shlex.quote(ssid)} (password hidden)")
    log(proc.stdout + proc.stderr)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def copy_network_profiles():
    """Copy live-session NetworkManager profiles (e.g. Wi-Fi) to the target."""
    src = "/etc/NetworkManager/system-connections"
    dst = f"{TARGET}/etc/NetworkManager/system-connections"
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        shutil.copy2(os.path.join(src, name), os.path.join(dst, name))


# ---------------------------------------------------------------- TUI
BANNER = r"""
     _   _   _ ____ ___ _____ _____ ____
    | | | | | |  _ \_ _|_   _| ____|  _ \
 _  | | | | | | |_) | |  | | |  _| | |_) |
| |_| | |_| | |  __/| |  | | | |___|  _ <
 \___/ \___/  |_|  |___| |_| |_____|_| \_\
"""


def draw_frame(scr, title, subtitle=""):
    scr.erase()
    h, w = scr.getmaxyx()
    if curses.has_colors():
        scr.attron(curses.color_pair(1))
    for i, line in enumerate(BANNER.strip("\n").splitlines()[:5]):
        x = max(0, (w - len(line)) // 2)
        if 1 + i < h:
            scr.addnstr(1 + i, x, line, w - x - 1, curses.A_BOLD)
    if curses.has_colors():
        scr.attroff(curses.color_pair(1))
    row = 7
    scr.addnstr(row, max(0, (w - len(title)) // 2), title, w - 1,
                curses.A_BOLD)
    if subtitle:
        for i, line in enumerate(textwrap.wrap(subtitle, max(10, w - 12))):
            scr.addnstr(row + 2 + i, 6, line, w - 7)
    return row + 3 + (2 if subtitle else 0)


def menu(scr, title, items, subtitle=""):
    idx = 0
    while True:
        y = draw_frame(scr, title, subtitle) + 1
        for i, label in enumerate(items):
            attr = curses.A_REVERSE if i == idx else curses.A_NORMAL
            if curses.has_colors() and i == idx:
                attr |= curses.color_pair(2)
            scr.addnstr(y + i, 8, f"  {label}  ", scr.getmaxyx()[1] - 10, attr)
        scr.addnstr(scr.getmaxyx()[0] - 2, 4,
                    "Up/Down to move, Enter to select, q to quit", 200)
        scr.refresh()
        key = scr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(items)
        elif key in (curses.KEY_ENTER, 10, 13):
            return idx
        elif key in (ord("q"), 27):
            return None


def prompt_text(scr, title, subtitle="", secret=False, validator=None,
                prefilled=""):
    buf = prefilled
    error = ""
    while True:
        y = draw_frame(scr, title, subtitle) + 1
        shown = "*" * len(buf) if secret else buf
        scr.addnstr(y, 8, "> " + shown + " ", scr.getmaxyx()[1] - 10,
                    curses.A_BOLD)
        if error:
            scr.addnstr(y + 2, 8, error, scr.getmaxyx()[1] - 10,
                        curses.color_pair(3) if curses.has_colors() else 0)
        scr.refresh()
        key = scr.getch()
        if key in (curses.KEY_ENTER, 10, 13):
            if validator and not secret:
                error = validator(buf) or ""
                if error:
                    continue
            return buf
        if key == 27:
            return None
        if key in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= key < 127:
            buf += chr(key)


def list_disks():
    out = subprocess.run(
        ["lsblk", "-J", "-d", "-o", "NAME,SIZE,MODEL,TYPE"],
        capture_output=True, text=True).stdout or "{}"
    data = json.loads(out)
    disks = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") == "disk" and not dev["name"].startswith(("loop", "sr", "zram")):
            disks.append((f"/dev/{dev['name']}", dev.get("size", "?"),
                          (dev.get("model") or "").strip()))
    return disks


def wifi_flow(scr):
    nets = scan_wifi()
    if not nets:
        draw_frame(scr, "No Wi-Fi networks found",
                   "Check the Wi-Fi adapter, then try again.")
        scr.getch()
        return
    idx = menu(scr, "Wi-Fi networks",
               [f"{ssid}  [{sec}]" for ssid, sec in nets],
               "Pick a network to join.")
    if idx is None:
        return
    ssid, sec = nets[idx]
    password = None
    if sec != "open":
        password = prompt_text(scr, f"Password for {ssid}", secret=True)
        if password is None:
            return
        password = password or None
    ok, msg = connect_wifi(ssid, password)
    draw_frame(scr, "Connected" if ok else "Connection failed", msg[:200])
    scr.getch()


def network_screen(scr):
    """Make sure there is a connection before anything is downloaded.

    Returns True when online, False if the user skipped, None to quit.
    """
    while True:
        if network_online():
            return True
        choice = menu(scr, "Network connection", [
            "Connect to Wi-Fi",
            "Check connection again",
            "Skip (install will fail without network)",
        ], "Jupiter downloads its packages during install. Plug in Ethernet "
           "or connect to Wi-Fi first.")
        if choice is None:
            return None
        if choice == 0:
            wifi_flow(scr)
        elif choice == 2:
            return False


def tui_flow(scr):
    """Returns dict(gpu, disk, username, password) or None if cancelled."""
    curses.curs_set(0)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_MAGENTA, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(3, curses.COLOR_RED, -1)

    net = network_screen(scr)
    if net is None:
        return None

    gpu_idx = menu(scr, "Graphics driver", [
        "NVIDIA (open kernel modules, nvidia-open)",
        "Open-source / AMD / Intel / other (Mesa)",
    ], "Pick the driver that matches your GPU. NVIDIA cards from Turing "
       "(RTX 20xx / GTX 16xx) onward support the open modules.")
    if gpu_idx is None:
        return None
    gpu = "nvidia" if gpu_idx == 0 else "mesa"

    disks = list_disks()
    if not disks:
        draw_frame(scr, "No disks found")
        scr.getch()
        return None
    labels = [f"{d} ({size}) {model}" for d, size, model in disks]
    d_idx = menu(scr, "Install target", labels,
                 "WARNING: the selected disk will be completely erased.")
    if d_idx is None:
        return None
    disk = disks[d_idx][0]

    confirm = menu(scr, f"Erase {disk}?", ["No, go back", "Yes, erase it"],
                   "All data on this disk will be lost.")
    if confirm != 1:
        return None

    username = prompt_text(scr, "Create your user account",
                           "Lowercase username (used to log in).",
                           validator=validate_username)
    if username is None:
        return None
    pw = prompt_text(scr, "Choose a password", "At least 6 characters.",
                     secret=True)
    if pw is None:
        return None
    pw2 = prompt_text(scr, "Confirm password", secret=True)
    if pw2 is None:
        return None
    err = validate_password(pw, pw2)
    if err:
        draw_frame(scr, "Password error", err)
        scr.getch()
        return tui_flow(scr)

    summary = (f"Network: {'online' if network_online() else 'offline'}\n"
               f"Disk: {disk}\nDriver: {'NVIDIA open' if gpu == 'nvidia' else 'Mesa (open)'}\n"
               f"User: {username}\nBrowser: Helium\nDesktop: Hyprland + Dank Material Shell")
    ok = menu(scr, "Ready to install", ["Install Jupiter", "Cancel"], summary)
    if ok != 0:
        return None
    return {"gpu": gpu, "disk": disk, "username": username, "password": pw}


def install(cfg, dry):
    gpu, disk, user, pw = cfg["gpu"], cfg["disk"], cfg["username"], cfg["password"]
    for desc, argv in build_plan(disk, gpu):
        if run_step(desc, argv, dry) != 0:
            return 1
    if not dry:
        root = subprocess.run(["blkid", "-s", "UUID", "-o", "value",
                               partition_path(disk, 2)],
                              capture_output=True, text=True).stdout.strip()
        esp = subprocess.run(["blkid", "-s", "UUID", "-o", "value",
                              partition_path(disk, 1)],
                             capture_output=True, text=True).stdout.strip()
        os.makedirs(f"{TARGET}/etc", exist_ok=True)
        with open(f"{TARGET}/etc/fstab", "w") as fh:
            fh.write(fstab_text(root, esp))
    if not dry:
        copy_network_profiles()
    for desc, argv in post_install_plan(user, gpu):
        stdin_text = f"{user}:{pw}\n" if desc == "Setting user password" else None
        if run_step(desc, argv, dry, stdin_text) != 0:
            return 1
    if dry:
        print("    [dry-run] write Plymouth theme 'jupiter' and rebuild initramfs")
    else:
        theme = f"{TARGET}/usr/share/plymouth/themes/{SPLASH_NAME}"
        os.makedirs(theme, exist_ok=True)
        for fname, body in SPLASH_FILES.items():
            with open(os.path.join(theme, fname), "w") as fh:
                fh.write(body)
        if run_step("Setting boot splash (placeholder)",
                    chroot(["plymouth-set-default-theme", "-R", SPLASH_NAME]),
                    dry) != 0:
            return 1
    home = f"{TARGET}/home/{user}/.config/hypr"
    if dry:
        print(f"    [dry-run] write {home}/hyprland.conf")
        return 0
    os.makedirs(home, exist_ok=True)
    with open(f"{home}/hyprland.conf", "w") as fh:
        fh.write(HYPR_CONF)
    subprocess.run(chroot(["chown", "-R", f"{user}:{user}", f"/home/{user}"]),
                   check=True)
    subprocess.run(["sync"], check=True)
    subprocess.run(["umount", "-R", TARGET], check=False)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Jupiter installer")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the plan without changing any disk")
    ap.add_argument("--no-tui", action="store_true",
                    help="skip the menus (for testing)")
    args = ap.parse_args()

    if os.geteuid() != 0 and not args.dry_run:
        print("jupiter-installer must run as root.")
        return 1

    if args.dry_run and args.no_tui:
        for desc, argv in build_plan("/dev/sdX", "nvidia"):
            print(f"{desc}: {' '.join(argv)}")
        return 0

    cfg = curses.wrapper(tui_flow)
    if cfg is None:
        print("Installation cancelled.")
        return 1
    print(BANNER)
    rc = install(cfg, args.dry_run)
    if rc == 0:
        print("\n\033[1;32mJupiter is installed.\033[0m Remove the USB stick "
              "and press Enter to reboot.")
        if not args.dry_run:
            input()
            subprocess.run(["reboot"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
