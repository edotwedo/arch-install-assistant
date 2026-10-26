#!/usr/bin/env python3
"""Offline install interview.

A local model (llama.cpp server) interprets the person's free-text answers.
Every value it returns is validated by plain code before it is used, and the
model never sees the password or the private profile. The model cannot run commands.
"""
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.request

import profile_tools

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8081
BASE = f"http://127.0.0.1:{PORT}"
MISSION = open(os.path.join(HERE, "mission.txt")).read()
SERVER = None
PROFILE = {"tar": None, "defaults": {}}


def say(text: str, indent: str = "  ") -> None:
    width = max(40, shutil.get_terminal_size((100, 24)).columns - 4)
    print(textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent))


def start_server(model: str, llama_dir: str, threads: int) -> bool:
    global SERVER
    exe = os.path.join(llama_dir, "llama-server")
    env = dict(os.environ, LD_LIBRARY_PATH=llama_dir)
    print("Starting the offline assistant (first load can take a minute)...")
    SERVER = subprocess.Popen(
        [exe, "-m", model, "--port", str(PORT), "-c", "4096", "-t", str(threads), "--no-webui"],
        env=env, stdout=open("/tmp/llama-server.log", "w"), stderr=subprocess.STDOUT,
    )
    for _ in range(240):
        if SERVER.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
                if json.load(r).get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def ask_model(field: dict, answer: str) -> dict | None:
    schema = {
        "type": "object",
        "properties": {
            "kind": {"enum": ["answer", "question", "unclear"]},
            "value": {"type": "string"},
            "reply": {"type": "string"},
        },
        "required": ["kind", "value", "reply"],
        "additionalProperties": False,
    }
    system = (
        MISSION
        + f"\n\nRight now you are collecting: {field['label']}.\n{field['instructions']}\n"
        "Reply with JSON only. kind=answer when the person gave a usable value (put it in value). "
        "kind=question when they asked you something: put a real, factual answer of one or two sentences "
        "in reply, using the facts above, and finish by restating the choices. Never just repeat the question. "
        "kind=unclear when you cannot tell (put a short clarifying question in reply)."
    )
    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": answer}],
        "max_tokens": 200,
        "temperature": 0.1,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
    }
    try:
        req = urllib.request.Request(BASE + "/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            content = json.load(r)["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        print(f"  (assistant unavailable: {e}; using your text as typed)")
        return None


def v_hostname(v):
    v = v.strip().lower()
    return (True, v) if re.fullmatch(r"[a-z][a-z0-9-]{0,30}", v) else (False, "Use lowercase letters, digits and dashes, starting with a letter.")


def v_username(v):
    v = v.strip().lower()
    return (True, v) if re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", v) and v not in ("root",) else (False, "Use lowercase letters, digits, - or _, and not 'root'.")


def v_desktop(v):
    v = v.strip().lower()
    if v in ("kde", "none"):
        return True, v
    return False, "Choose 'kde' (a full graphical desktop) or 'none' (text only)."


def v_timezone(v):
    v = v.strip()
    ok = re.fullmatch(r"[A-Za-z_]+(/[A-Za-z_+\-0-9]+){1,2}", v) and os.path.isfile(f"/usr/share/zoneinfo/{v}")
    return (True, v) if ok else (False, "I need a valid region/city name like America/New_York.")


def v_kb(v):
    v = v.strip()
    try:
        maps = subprocess.check_output(["localectl", "list-keymaps"], text=True).split()
    except Exception:
        maps = ["us", "uk", "de", "fr", "es", "it"]
    return (True, v) if v in maps else (False, "That keyboard layout isn't in the list (try 'us').")


def make_v_space(avail_gib):
    def v_space(v):
        v = v.strip().lower()
        if v in ("all", "rest", "everything"):
            return True, "rest"
        for word, frac in (("half", 0.5), ("quarter", 0.25), ("third", 1 / 3)):
            if word in v:
                v = str(avail_gib * frac)
                break
        pct = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", v)
        if pct:
            v = str(avail_gib * float(pct.group(1)) / 100)
        v = re.sub(r"\s*(gib|gb|g|gigabytes?)\s*$", "", v)
        m = re.fullmatch(r"(\d+(?:\.\d+)?)", v)
        if not m:
            return False, f"Give a number of GiB (20 to {avail_gib}) or 'all'."
        n = float(m.group(1))
        if 0 < n < 1:
            n = avail_gib * n
        if n < 20 or n > avail_gib:
            return False, f"Linux needs at least 20 GiB, and only {avail_gib} GiB is free."
        return True, str(int(n) if n == int(n) else n)
    return v_space


def make_v_extras(keys):
    def v_extras(v):
        v = v.strip().lower()
        if v in ("", "none", "nothing", "no"):
            return True, ""
        if v in ("all", "everything"):
            return True, ",".join(keys)
        picked = [t for t in re.split(r"[,\s]+", v) if t]
        bad = [t for t in picked if t not in keys]
        if bad:
            return False, f"I don't know '{bad[0]}'. Choose from: {', '.join(keys)}, or 'none'."
        return True, ",".join(dict.fromkeys(picked))
    return v_extras


def collect(field: dict, use_ai: bool) -> str:
    default = field.get("default")
    print(f"\n{field['ask']}" + (f"  [press Enter for: {default}]" if default else ""))
    while True:
        answer = input("> ").strip()
        if not answer and default is not None:
            answer, use_this_ai = str(default), False
        elif not answer:
            continue
        else:
            use_this_ai = use_ai
        val = answer
        if use_this_ai:
            out = ask_model(field, answer)
            if out:
                if out["kind"] in ("question", "unclear"):
                    say(out["reply"])
                    continue
                val = out["value"]
        ok, result = field["validate"](val)
        if ok:
            print(f"  Got it: {result or 'none'}")
            return result
        print(f"  {result}")


def gen(*args) -> str:
    return subprocess.check_output([sys.executable, os.path.join(HERE, "gen_config.py"), *args], text=True)


def pick_device() -> tuple[str, int]:
    devs = [d for d in json.loads(gen("--list-devices")) if d["has_esp"] and d["free_gib"] >= 20]
    if not devs:
        print("No disk with an existing boot partition and at least 20 GiB of free space was found.")
        print("Shrink your existing OS partition first, then run this again. Nothing was changed.")
        sys.exit(1)
    if len(devs) == 1:
        d = devs[0]
    else:
        print("\nMore than one disk qualifies:")
        for i, d in enumerate(devs, 1):
            print(f"  {i}. {d['path']}  {d['model']}  {d['size_gib']} GiB  ({d['free_gib']} GiB free)")
        d = devs[int(input("Which one? number > ")) - 1]
    print(f"\nDisk: {d['path']} ({d['model']}, {d['size_gib']} GiB) with {d['free_gib']} GiB of free space.")
    return d["path"], d["free_gib"]


def load_profile() -> None:
    enc = os.path.join(HERE, "profile.enc")
    if not os.path.isfile(enc):
        return
    print("\nI found a saved personal setup on this stick.")
    for attempt in range(3):
        pw = getpass.getpass("Passphrase to unlock it (or just press Enter to skip): ")
        if not pw:
            print("Skipping your saved setup.")
            return
        tar = profile_tools.decrypt(enc, pw)
        if tar:
            PROFILE["tar"] = tar
            PROFILE["defaults"] = profile_tools.read_manifest(tar)
            print("Unlocked. I'll use your saved choices as defaults.")
            return
        print("That didn't unlock it." + (" Try again." if attempt < 2 else " Skipping your saved setup."))


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--llama-dir")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--no-ai", action="store_true")
    a = ap.parse_args()

    use_ai = not a.no_ai and a.model and a.llama_dir and start_server(a.model, a.llama_dir, a.threads)
    print("\n=== Install Assistant ===")
    print("I'll ask a few questions, then show you exactly what will happen before anything changes." if use_ai
          else "Assistant is off; I'll ask the same questions in plain mode.")

    load_profile()
    d = PROFILE["defaults"]
    device, avail = pick_device()

    hw = json.loads(gen("--detect-hardware"))
    print(f"Your hardware: graphics -> {hw['gfx']}. {hw['gfx_reason']}  CPU microcode -> {hw['ucode'] or 'none detected'}.")

    extras_map = json.loads(gen("--list-extras"))
    keys = list(extras_map)
    extras_text = "; ".join(f"{k} = {v}" for k, v in extras_map.items())

    fields = [
        {"key": "hostname", "label": "computer name", "ask": "What should this computer be called? (short, e.g. 'studio')", "default": d.get("hostname"),
         "instructions": "Extract a short lowercase hostname (letters, digits, dashes only; join words, e.g. 'audio box' becomes audiobox). "
                         "Facts you may use: the computer name is what this machine is called on the home network; it can be changed later.", "validate": v_hostname},
        {"key": "username", "label": "user name", "ask": "What username do you want?", "default": d.get("username"),
         "instructions": "Extract a lowercase login name.", "validate": v_username},
        {"key": "desktop", "label": "desktop choice", "ask": "Desktop: 'kde' for a full graphical desktop, or 'none' for text only?", "default": d.get("desktop"),
         "instructions": "Value must be exactly 'kde' or 'none'. Map 'pretty desktop', 'graphical', 'gui', 'windows and a taskbar' to kde and 'terminal only' to none. "
                         "Facts you may use: KDE Plasma is a full graphical desktop with windows, a taskbar, menus and a settings app, "
                         "familiar to Windows users, and it adds roughly 2 to 3 GB. 'none' installs no desktop, only a text terminal, "
                         "which suits experts and servers and can have a desktop added later.", "validate": v_desktop},
        {"key": "timezone", "label": "time zone", "ask": "Where are you? (city or time zone, e.g. 'New York')", "default": d.get("timezone"),
         "instructions": "Value must be a tz database name like America/New_York or Europe/Berlin.", "validate": v_timezone},
        {"key": "kb", "label": "keyboard layout", "ask": "Keyboard layout? (most US keyboards: 'us')", "default": d.get("kb"),
         "instructions": "Value must be a Linux keymap name such as us, uk, de, fr, es.", "validate": v_kb},
        {"key": "space", "label": "disk space for Linux", "ask": f"How many GiB should Linux get? (20 to {avail}, or 'all' of the free space)",
         "instructions": f"Value is either the word 'all' or a whole number of GiB between 20 and {avail}. "
                         f"Arithmetic you must use: half is {avail // 2}, a quarter is {avail // 4}, a third is {avail // 3}. "
                         "Facts you may use: a desktop Linux system is comfortable with 60 GiB or more; music and video work wants more; "
                         "20 GiB is the bare minimum.", "validate": make_v_space(avail)},
        {"key": "extras", "label": "optional setup groups", "ask": "Want any extras installed for you? Options: " + extras_text + ". Say which, or 'none'.",
         "default": d.get("extras"),
         "instructions": "Value is a comma-separated list using ONLY these exact keys: " + ", ".join(keys) + ". Use an empty value for none. "
                         "Map: phone stuff or adb -> phone; coding or programming -> dev; music or recording -> audio; movies, video or torrents -> media; "
                         "Claude -> claude; browser or Proton -> apps. Facts you may use: " + extras_text + ".", "validate": make_v_extras(keys)},
    ]
    ans = {}
    for f in fields:
        ans[f["key"]] = collect(f, bool(use_ai))

    while True:
        pw = getpass.getpass("\nChoose a password (typed privately, the assistant never sees it): ")
        if len(pw) >= 8 and pw == getpass.getpass("Type it again: "):
            break
        print("Passwords must match and be at least 8 characters.")

    outdir = "/root"
    env = dict(os.environ, GEN_PASSWORD=pw)
    cmd = [sys.executable, os.path.join(HERE, "gen_config.py"), "--device", device, "--username", ans["username"],
           "--hostname", ans["hostname"], "--desktop", ans["desktop"], "--timezone", ans["timezone"],
           "--kb-layout", ans["kb"], "--root-size-gib", ans["space"], "--extras", ans["extras"], "--outdir", outdir]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr or r.stdout)
        print("Nothing was changed.")
        sys.exit(1)
    print("\n" + r.stdout)
    if PROFILE["tar"]:
        print("  saved setup       : your Claude notes and settings will be restored after the install")
    print("Everything marked KEEP is left exactly as it is. Only the CREATE line is new.")
    if input("Type INSTALL to begin, anything else to stop (nothing changes if you stop): ").strip() != "INSTALL":
        print("Stopped. Nothing was changed.")
        sys.exit(0)
    rc = subprocess.run(["archinstall", "--config", f"{outdir}/user_configuration.json",
                         "--creds", f"{outdir}/user_credentials.json", "--silent"]).returncode
    if SERVER:
        SERVER.terminate()
    if rc != 0:
        print(f"\nThe install did NOT finish (archinstall exited with code {rc}).")
        print("Do not reboot yet. The log is at /var/log/archinstall/install.log")
        print("Your existing partitions are not touched by this tool; you can check them with: lsblk")
        sys.exit(rc)
    if PROFILE["tar"]:
        try:
            written = profile_tools.restore(PROFILE["tar"], "/mnt", ans["username"])
            subprocess.run(["arch-chroot", "/mnt", "chown", "-R", f"{ans['username']}:{ans['username']}", f"/home/{ans['username']}/.claude"], check=True)
            print(f"Restored {len(written)} personal files into your new home folder.")
        except Exception as e:
            print(f"The install finished, but restoring your saved setup failed: {e}")
    print("\nInstall finished successfully. Remove the USB stick and reboot; choose your system from the boot menu.")


if __name__ == "__main__":
    try:
        main()
    finally:
        if SERVER and SERVER.poll() is None:
            SERVER.terminate()
