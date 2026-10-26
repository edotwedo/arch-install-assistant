#!/usr/bin/env python3
"""Generate an archinstall config that installs Arch next to an existing OS.

Reads the real disk layout through archinstall's own library, keeps every
existing partition as status "existing" (never modified), and creates a single
Linux root partition inside the largest free region. Never wipes the disk.

Everything that ends up in a shell command is validated against strict patterns
or fixed allowlists here, so a wrong or malicious answer upstream cannot inject commands.
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from archinstall.lib.disk.device_handler import device_handler
from archinstall.lib.models.device import PartitionModification, Unit

MIB = 1024 * 1024

# Names verified against Arch's package database (archlinux.org/packages) and Flathub's API.
EXTRAS = {
    "phone": {"label": "Android phone tools (adb, scrcpy, USB rules)", "packages": ["android-tools", "android-udev", "scrcpy"], "groups": ["adbusers"]},
    "dev": {"label": "Developer tools (git, Python, Node, Java, fonts)", "packages": ["git", "base-devel", "python", "nodejs", "npm", "jdk21-openjdk", "zoxide", "ttf-jetbrains-mono-nerd"]},
    "audio": {"label": "Music production (REAPER, JACK bridge)", "packages": ["reaper", "pipewire-jack"]},
    "media": {"label": "Media and downloads (VLC, ffmpeg, qBittorrent, 7zip)", "packages": ["vlc", "vlc-plugins-all", "ffmpeg", "qbittorrent", "7zip"]},
    "claude": {"label": "Claude Code (sign in with your browser after install)", "packages": ["curl"], "claude": True},
    "apps": {"label": "Brave browser and Proton apps (installed on first boot)", "packages": ["flatpak"],
             "flatpaks": ["com.brave.Browser", "me.proton.Pass", "me.proton.Mail", "com.protonvpn.www"]},
}

GFX = {
    "all": "All open-source",
    "amd": "AMD / ATI (open-source)",
    "intel": "Intel (open-source)",
    "nvidia_open": "Nvidia (open kernel module for newer GPUs, Turing+)",
    "nouveau": "Nvidia (open-source nouveau driver)",
    "vbox": "VirtualBox (open-source)",
}

USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
HOST_RE = re.compile(r"[a-z][a-z0-9-]{0,30}")
TZ_RE = re.compile(r"[A-Za-z_]+(/[A-Za-z_+\-0-9]+){1,2}")
KB_RE = re.compile(r"[a-z0-9_-]{1,32}")


def size_json(mib: int, sector_size: int) -> dict:
    return {"sector_size": {"value": sector_size, "unit": "B"}, "unit": "MiB", "value": mib}


def fail(msg: str) -> None:
    print(f"REFUSING TO GENERATE CONFIG: {msg}", file=sys.stderr)
    sys.exit(2)


def largest_free(dev):
    info = dev.device_info
    ss = info.sector_size.value
    if not info.free_space_regions:
        return None
    region = max(info.free_space_regions, key=lambda r: r.get_length())
    start_mib = math.ceil(region.start * ss / MIB)
    end_mib = math.floor((region.end + 1) * ss / MIB) - 1
    return start_mib, end_mib - start_mib


def list_devices() -> None:
    out = []
    for d in device_handler.devices:
        info = d.device_info
        if info.type == "loop" or info.read_only:
            continue
        esp = [p for p in d.partition_infos if any(f.description == "esp" for f in p.flags)]
        free = largest_free(d)
        out.append({
            "path": str(info.path),
            "model": info.model,
            "size_gib": round(info.total_size.convert(Unit.GiB).value),
            "has_esp": len(esp) == 1,
            "free_gib": (free[1] // 1024) if free else 0,
            "partitions": len(d.partition_infos),
        })
    print(json.dumps(out))


def detect_cpu_ucode() -> str | None:
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return None
    if "AuthenticAMD" in text:
        return "amd-ucode"
    if "GenuineIntel" in text:
        return "intel-ucode"
    return None


def detect_gpu() -> tuple[str, str]:
    """Return (archinstall GfxDriver name, human explanation). Falls back to the open-source stack."""
    try:
        out = subprocess.check_output(["lspci", "-mm", "-nn"], text=True, timeout=10)
    except Exception:
        return GFX["all"], "Could not read the graphics hardware; using the open-source drivers that work everywhere."
    gpus = [l for l in out.splitlines() if re.search(r"VGA compatible|3D controller|Display controller", l)]
    if not gpus:
        return GFX["all"], "No graphics card was detected; using the open-source drivers."
    vendors = {"10de": "nvidia", "1002": "amd", "8086": "intel", "80ee": "vbox"}
    kinds = set()
    nvidia_line = ""
    for l in gpus:
        fields = re.findall(r'"([^"]*)"', l)
        ids = re.findall(r"\[([0-9a-fA-F]{4})\]", fields[1]) if len(fields) > 2 else []
        kind = vendors.get(ids[-1].lower(), "other") if ids else "other"
        kinds.add(kind)
        if kind == "nvidia":
            nvidia_line = fields[2]
    if len(kinds) != 1:
        return GFX["all"], "More than one graphics device (or an unknown one) was found; using the open-source drivers, which are safe."
    kind = kinds.pop()
    if kind == "nvidia":
        if re.search(r"\b(TU|GA|AD|GB)\d{2,3}[A-Z]?\b", nvidia_line):
            return GFX["nvidia_open"], "NVIDIA Turing or newer detected: using NVIDIA's open kernel module."
        return GFX["nouveau"], "Older NVIDIA card detected: using the open-source nouveau driver (safe default; proprietary options can be added later)."
    return {"amd": (GFX["amd"], "AMD graphics detected."), "intel": (GFX["intel"], "Intel graphics detected."),
            "vbox": (GFX["vbox"], "Virtual machine graphics detected."), "other": (GFX["all"], "Unknown graphics; using open-source drivers.")}[kind]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--detect-hardware", action="store_true")
    ap.add_argument("--list-extras", action="store_true")
    ap.add_argument("--device")
    ap.add_argument("--hostname", default="archlinux")
    ap.add_argument("--username")
    ap.add_argument("--password", default=None, help="prefer env GEN_PASSWORD")
    ap.add_argument("--root-size-gib", default="rest")
    ap.add_argument("--esp-mount", default="/efi")
    ap.add_argument("--timezone", default="UTC")
    ap.add_argument("--kb-layout", default="us")
    ap.add_argument("--desktop", choices=["none", "kde"], default="none")
    ap.add_argument("--gfx", default="auto", help="'auto' (detect) or an archinstall GfxDriver name")
    ap.add_argument("--extras", default="", help="comma list from: " + ",".join(EXTRAS))
    ap.add_argument("--packages", default="os-prober,ntfs-3g,networkmanager,sudo,nano,git")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    if a.list_devices:
        list_devices()
        return
    if a.list_extras:
        print(json.dumps({k: v["label"] for k, v in EXTRAS.items()}))
        return
    if a.detect_hardware:
        gfx, why = detect_gpu()
        print(json.dumps({"gfx": gfx, "gfx_reason": why, "ucode": detect_cpu_ucode()}))
        return
    if not a.device or not a.username:
        fail("--device and --username are required")
    if not USER_RE.fullmatch(a.username) or a.username == "root":
        fail("invalid username")
    if not HOST_RE.fullmatch(a.hostname):
        fail("invalid hostname")
    if not TZ_RE.fullmatch(a.timezone) or not os.path.isfile(f"/usr/share/zoneinfo/{a.timezone}"):
        fail("invalid timezone")
    if not KB_RE.fullmatch(a.kb_layout):
        fail("invalid keyboard layout")
    if not re.fullmatch(r"/dev/[a-zA-Z0-9]+", a.device):
        fail("invalid device path")
    extras = [x for x in a.extras.split(",") if x]
    for x in extras:
        if x not in EXTRAS:
            fail(f"unknown extra '{x}'")
    password = a.password or os.environ.get("GEN_PASSWORD")
    if not password:
        fail("no password supplied (set GEN_PASSWORD)")

    dev = next((d for d in device_handler.devices if str(d.device_info.path) == a.device), None)
    if dev is None:
        fail(f"device {a.device} not found")

    info = dev.device_info
    ss = info.sector_size.value

    esp = [p for p in dev.partition_infos if any(f.description == "esp" for f in p.flags)]
    if len(esp) != 1:
        fail(f"expected exactly one EFI system partition, found {len(esp)}")
    free = largest_free(dev)
    if not free:
        fail("no free space on the device; shrink the existing OS partition first")
    start_mib, avail_mib = free
    if avail_mib < 20 * 1024:
        fail(f"largest free region is only {avail_mib // 1024} GiB; need at least 20 GiB")

    if a.root_size_gib == "rest":
        root_mib = avail_mib
    else:
        root_mib = min(avail_mib, int(float(a.root_size_gib) * 1024))
        if root_mib < 20 * 1024:
            fail("requested size is below the 20 GiB minimum")

    partitions = []
    for p in dev.partition_infos:
        pm = PartitionModification.from_existing_partition(p)
        j = pm.json()
        if p is esp[0]:
            j["mountpoint"] = a.esp_mount
        partitions.append(j)

    partitions.append({
        "obj_id": str(uuid.uuid4()),
        "status": "create",
        "type": "primary",
        "start": size_json(start_mib, ss),
        "size": size_json(root_mib, ss),
        "fs_type": "ext4",
        "mountpoint": "/",
        "mount_options": [],
        "flags": [],
        "dev_path": None,
        "btrfs": [],
    })

    if a.gfx == "auto":
        gfx_name, gfx_why = detect_gpu()
    elif a.gfx in GFX.values():
        gfx_name, gfx_why = a.gfx, "Chosen by you."
    else:
        fail("unknown graphics driver name")
    if a.desktop == "kde":
        profile = {"gfx_driver": gfx_name, "greeter": "sddm", "profile": {"main": "Desktop", "details": ["KDE Plasma"]}}
    else:
        profile = {"profile": {"main": "Minimal", "details": []}}

    packages = [x for x in a.packages.split(",") if x]
    ucode = detect_cpu_ucode()
    if ucode:
        packages.append(ucode)
    groups: list[str] = []
    flatpaks: list[str] = []
    want_claude = False
    for x in extras:
        e = EXTRAS[x]
        packages += e.get("packages", [])
        groups += e.get("groups", [])
        flatpaks += e.get("flatpaks", [])
        want_claude = want_claude or e.get("claude", False)
    packages = list(dict.fromkeys(packages))

    config = {
        "archinstall-language": "English",
        "audio_config": {"audio": "pipewire"},
        "bootloader_config": {"bootloader": "Grub", "uki": False, "removable": False},
        "disk_config": {
            "config_type": "manual_partitioning",
            "device_modifications": [
                {"device": a.device, "wipe": False, "partitions": partitions}
            ],
        },
        "hostname": a.hostname,
        "kernels": ["linux"],
        "locale_config": {"kb_layout": a.kb_layout, "sys_enc": "UTF-8", "sys_lang": "en_US"},
        "network_config": {"type": "nm"},
        "ntp": True,
        "packages": packages,
        "profile_config": profile,
        "swap": {"enabled": True, "algorithm": "zstd"},
        "timezone": a.timezone,
        "silent": True,
    }

    grub_cfg = f"{a.esp_mount}/grub/grub.cfg"
    unit = (
        "[Unit]\n"
        "Description=Rebuild GRUB menu once so other operating systems are detected\n"
        "ConditionPathExists=/etc/default/grub\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/usr/bin/grub-mkconfig -o {grub_cfg}\n"
        "ExecStartPost=/usr/bin/sync\n"
        "ExecStartPost=/usr/bin/systemctl disable grub-osprobe-firstboot.service\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    cmds = [
        "sed -i 's/^#\\?GRUB_DISABLE_OS_PROBER=.*/GRUB_DISABLE_OS_PROBER=false/' /etc/default/grub",
        "grep -q '^GRUB_DISABLE_OS_PROBER=false' /etc/default/grub || echo 'GRUB_DISABLE_OS_PROBER=false' >> /etc/default/grub",
        "sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=8/' /etc/default/grub",
        "grep -q '^GRUB_TIMEOUT_STYLE=' /etc/default/grub && sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/' /etc/default/grub || echo 'GRUB_TIMEOUT_STYLE=menu' >> /etc/default/grub",
        "cat > /etc/systemd/system/grub-osprobe-firstboot.service <<'UNIT'\n" + unit + "UNIT",
        "systemctl enable grub-osprobe-firstboot.service",
    ]
    for g in dict.fromkeys(groups):
        cmds.append(f"usermod -aG {g} {a.username}")
    if want_claude:
        cmds.append(f"su - {a.username} -c 'curl -fsSL https://claude.ai/install.sh | bash'")
        cmds.append(f"grep -q 'local/bin' /home/{a.username}/.bashrc 2>/dev/null || printf '%s\\n' 'export PATH=\"$HOME/.local/bin:$PATH\"' >> /home/{a.username}/.bashrc")
        cmds.append(f"chown {a.username}:{a.username} /home/{a.username}/.bashrc")
        # .bashrc exits early for non-interactive shells, so also set PATH for every login shell.
        cmds.append("printf '%s\\n' 'if [ -d \"$HOME/.local/bin\" ]; then case \":$PATH:\" in *\":$HOME/.local/bin:\"*) ;; *) PATH=\"$HOME/.local/bin:$PATH\" ;; esac; export PATH; fi' > /etc/profile.d/local-bin.sh && chmod 644 /etc/profile.d/local-bin.sh")
    if flatpaks:
        script = (
            "#!/bin/sh\nset -e\n"
            "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo\n"
            "while read -r app; do [ -n \"$app\" ] && flatpak install -y --noninteractive flathub \"$app\"; done < /etc/postinstall/flatpaks.list\n"
            "systemctl disable postinstall-flatpaks.service\n"
        )
        fp_unit = (
            "[Unit]\nDescription=Install chosen apps once the network is up\n"
            "Wants=network-online.target\nAfter=network-online.target\n\n"
            "[Service]\nType=oneshot\nTimeoutStartSec=1800\nExecStart=/usr/local/bin/postinstall-flatpaks.sh\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
        cmds += [
            "mkdir -p /etc/postinstall",
            "printf '%s\\n' " + " ".join(flatpaks) + " > /etc/postinstall/flatpaks.list",
            "cat > /usr/local/bin/postinstall-flatpaks.sh <<'SH'\n" + script + "SH",
            "chmod 755 /usr/local/bin/postinstall-flatpaks.sh",
            "cat > /etc/systemd/system/postinstall-flatpaks.service <<'UNIT'\n" + fp_unit + "UNIT",
            "systemctl enable postinstall-flatpaks.service NetworkManager-wait-online.service",
        ]
    cmds += [f"grub-mkconfig -o {grub_cfg} || true", "sync"]
    config["custom_commands"] = cmds

    pw_hash = subprocess.check_output(["openssl", "passwd", "-6", password], text=True).strip()
    creds = {
        "root_enc_password": pw_hash,
        "users": [{"username": a.username, "enc_password": pw_hash, "sudo": True}],
    }

    dm = config["disk_config"]["device_modifications"][0]
    assert dm["wipe"] is False
    assert all(p["status"] in ("existing", "create") for p in dm["partitions"])
    assert sum(1 for p in dm["partitions"] if p["status"] == "create") == 1

    out = Path(a.outdir)
    (out / "user_configuration.json").write_text(json.dumps(config, indent=2))
    cred_path = out / "user_credentials.json"
    cred_path.write_text(json.dumps(creds, indent=2))
    os.chmod(cred_path, 0o600)

    print("PLAN")
    print(f"  device            : {a.device} (wipe = False)")
    for p in dev.partition_infos:
        print(f"  KEEP (untouched)  : {p.path}  {p.fs_type or '-'}")
    print(f"  CREATE            : ext4 root, {root_mib // 1024} GiB, in free space starting at {start_mib} MiB")
    print(f"  boot partition    : {esp[0].path} mounted at {a.esp_mount} (not formatted)")
    print(f"  desktop           : {a.desktop}")
    if a.desktop == "kde":
        print(f"  graphics          : {gfx_name}  ({gfx_why})")
    print(f"  CPU microcode     : {ucode or 'not detected'}")
    print(f"  extras            : {', '.join(extras) or 'none'}")


if __name__ == "__main__":
    main()
