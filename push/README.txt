INSTALL ASSISTANT USB
=====================

This stick installs Arch Linux NEXT TO your existing Windows, into free space only.
It never wipes your disk and never touches your existing partitions. Before it changes
anything, it shows you a plan and waits for you to type INSTALL.

BEFORE YOU START
  1. In Windows, shrink C: to leave at least 40 GB unallocated
     (Disk Management -> right-click C: -> Shrink Volume).
  2. Turn off Windows Fast Startup / hibernation, and make sure BitLocker is off.
  3. Turn off Secure Boot in the firmware settings (the Arch installer is not signed for it).
  4. Plug in Ethernet if you can (Wi-Fi works too, see below).

BOOT FROM THE STICK
  Restart and open the boot menu (Gigabyte boards: usually F12; others F8, F10 or Esc).
  Pick the USB stick, the entry that says UEFI, then "Arch Linux install medium".
  You will land at a root prompt.

START THE ASSISTANT (type these three lines, pressing Enter after each)
  mkdir -p /aidata
  mount -t exfat -L AIDATA /aidata
  sh /aidata/start.sh

  It takes about a minute to load. If profile.enc is on this stick it asks for your
  passphrase first, then asks a few questions in plain English.

READ THE PLAN SCREEN BEFORE TYPING INSTALL
  - "device" must be your main disk, NOT the USB stick.
  - Every partition that already exists must be marked KEEP (untouched).
  - Exactly ONE line says CREATE (the new Linux partition), inside free space.
  - "boot partition ... (not formatted)" must be shown.
  If anything looks different, type anything except INSTALL. Nothing changes if you stop.

WI-FI (only if there is no Ethernet)
  iwctl
  station wlan0 scan
  station wlan0 get-networks
  station wlan0 connect "YourNetworkName"
  exit

AFTER "Install finished successfully"
  1. Type: reboot        (unplug the USB stick when the screen goes dark)
  2. A boot menu shows for about 8 seconds. Windows Boot Manager is still there.
  3. Log in with the username and password you chose.
  4. Open a terminal and run: claude     (sign in with your browser)
  5. Brave and the Proton apps install themselves in the background once you are online
     (a few minutes). Check with: flatpak list
  6. Phone: plug it in and run: adb devices
     Wireless: adb tcpip 5555   then   adb connect PHONE_IP:5555

IF SOMETHING GOES WRONG
  - Stick will not show in the boot menu: check Secure Boot is off and choose the UEFI entry.
  - "Nothing was changed": safe. Fix the cause and run start.sh again.
  - Install failed part-way: do NOT reboot. Log: /var/log/archinstall/install.log
    Your Windows partitions are never modified by this tool. A part-made Linux partition
    can be deleted (only the newest one, at the end) and start.sh run again.
  - Windows missing from the Linux boot menu: pick "Windows Boot Manager" from the PC's own
    boot menu (F12), or in Linux run: sudo grub-mkconfig -o /efi/grub/grub.cfg
  - Windows always still boots from the PC's own boot menu (F12).

WHAT IS ON THIS STICK
  Arch Linux installer, an offline AI model (Gemma) with llama.cpp, the safety-checked
  install generator (gen_config.py), and your encrypted personal setup (profile.enc).
  Your passphrase cannot be recovered. Keep it somewhere safe.
