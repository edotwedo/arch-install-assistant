# Arch install assistant

Offline helper for installing Arch Linux **beside** the operating system already on the disk. It does not wipe the disk. It keeps every existing partition and creates one Linux root partition in free space. Before anything is written, it shows the plan and waits until the person types `INSTALL`.

A local model (llama.cpp) reads free-text answers during the interview. Plain code checks every value before use. The model cannot run commands, and it never sees the password or the private profile.

## What is in this repo

- `push/interview.py` runs the interview inside the Arch live environment.
- `push/gen_config.py` builds an archinstall config from the real disk layout.
- `push/profile_tools.py` restores an encrypted personal profile onto the new system, and ignores every path that is not on the allowlist.
- `push/start.sh` mounts the `AIDATA` stick and starts the interview.
- `push/mission.txt` is the instruction text given to the local model.
- `make_profile.ps1` builds `profile.enc` on the machine you are leaving. You type the passphrase. It is not stored here.
- `test_host.py` checks the validators, profile restore, and graphics detection without touching a disk.
- `push/README.txt` is the stick's own instruction sheet.

## What is not in this repo

Model weights, the llama.cpp binary, disk images, screenshots, and anyone's encrypted profile. `start.sh` expects `llama-b11056` and `gemma-4-E4B-it-Q4_K_M.gguf` on the stick. Bring those yourself and follow the model license.

## Stick layout

Label the data partition `AIDATA`. Put the files from `push/` on it, plus the llama.cpp build and the GGUF model. Boot the Arch ISO, then:

```sh
mkdir -p /aidata
mount -t exfat -L AIDATA /aidata
sh /aidata/start.sh
```

Read the plan. The device must be the internal disk, not the USB stick. Existing partitions must say KEEP. One line must say CREATE, inside free space. Type `INSTALL` only when that is what you see.

## Profile

```powershell
pwsh -File .\make_profile.ps1 -MemoryDir .\notes
```

The Linux side decrypts with `openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256`. Restore writes only `~/.claude/settings.json` and markdown files under the Claude memory folder.

## Test

```powershell
python test_host.py
```

`gen_config.py` and `probe.py` import archinstall, so they run in the live ISO, not on Windows.
