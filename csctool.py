#!/usr/bin/env python3
"""csctool - change Samsung CSC (region/carrier code) without root, without wipe.

Uses Samsung's AT modem interface exposed in Test Mode (*#0*#). Sends the
pre-configurator command to activate a different CSC already present in the
phone's multi-CSC firmware bundle, then requests a reboot. Neither reboot nor
CSC activation is verified by this tool; Knox is untouched.
Whether user data survives depends on the firmware (ro.omc.changetype) - many
newer devices factory-reset on a sales-code change. Only CSCs shipped inside
the phone's bundle can be activated - anything else needs a full firmware
flash via Odin.

Requires: adb on PATH, pyserial. Linux or macOS.
"""

import argparse
from dataclasses import dataclass
import glob
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("error: pyserial not installed. Run: pip install pyserial")

ADB = "adb"
BAUD = 115200
AT_DELAY = 1.0  # seconds between AT commands (required by modem)

# Directories on device that may hold per-CSC folders (varies by model/One UI).
CSC_DIRS = [
    "/system/omc",
    "/prism/omc",
    "/optics/configs/carriers",
    "/optics/configs/carriers/single",
    "/efs/carrier",
    "/system/csc",
]
CSC_RE = re.compile(r"^[A-Z]{3}$")  # CSC codes: 3 uppercase letters (EGY, XSG, THL)


# --------------------------------------------------------------------------- #
# ANSI helpers
# --------------------------------------------------------------------------- #
def _c(code, s):
    return s if not sys.stdout.isatty() else f"\033[{code}m{s}\033[0m"


def bold(s):
    return _c("1", s)


def red(s):
    return _c("31", s)


def green(s):
    return _c("32", s)


def yellow(s):
    return _c("33", s)


def die(msg):
    sys.exit(red("error: ") + msg)


# --------------------------------------------------------------------------- #
# adb
# --------------------------------------------------------------------------- #
def adb(*args, timeout=15):
    return subprocess.run(
        [ADB, *args], capture_output=True, text=True, timeout=timeout
    )


def check_adb():
    if shutil.which(ADB) is None:
        die("adb not found on PATH. Install Android platform-tools.")


def require_one_device():
    """Ensure exactly one authorized device is connected."""
    out = adb("devices").stdout.splitlines()
    devices, unauthorized = [], []
    for line in out[1:]:
        line = line.strip()
        if not line:
            continue
        serial_id, _, state = line.partition("\t")
        if state == "device":
            devices.append(serial_id)
        elif state in ("unauthorized", "offline"):
            unauthorized.append((serial_id, state))
    if unauthorized:
        pairs = ", ".join(f"{s} ({st})" for s, st in unauthorized)
        die(f"device not ready: {pairs}. Accept the USB debugging prompt on the phone.")
    if not devices:
        die("no device detected. Connect the phone and enable USB debugging.")
    if len(devices) > 1:
        die(f"multiple devices connected ({', '.join(devices)}). Connect only one.")
    return devices[0]


def getprop(key):
    return adb("shell", "getprop", key).stdout.strip()


def require_samsung():
    mfr = getprop("ro.product.manufacturer").lower()
    if "samsung" not in mfr:
        die(f"not a Samsung device (manufacturer='{mfr or 'unknown'}').")


def current_csc():
    # ril.sales_code = active CSC; ro.csc.sales_code / ro.boot... = fallbacks
    for key in ("ril.sales_code", "ro.csc.sales_code", "ro.boot.sales_code"):
        val = getprop(key)
        if val:
            return val
    return "?"


def device_summary():
    return {
        "model": getprop("ro.product.model") or "?",
        "name": getprop("ro.product.name") or "?",
        "csc": current_csc(),
        "csc_original": getprop("ro.csc.sales_code") or "?",
        "android": getprop("ro.build.version.release") or "?",
        "firmware": getprop("ro.build.display.id") or "?",
    }


def list_supported_csc():
    """Best-effort scan of on-device OMC/CSC dirs for available codes."""
    found = set()
    for d in CSC_DIRS:
        res = adb("shell", "ls", d, timeout=20)
        if res.returncode != 0:
            continue
        for name in res.stdout.split():
            name = name.strip().strip("/")
            if CSC_RE.match(name):
                found.add(name)
    return sorted(found)


# --------------------------------------------------------------------------- #
# serial
# --------------------------------------------------------------------------- #
def find_ports():
    return sorted(
        glob.glob("/dev/ttyACM*")
        + glob.glob("/dev/ttyUSB*")
        + glob.glob("/dev/cu.usbmodem*")  # macOS
    )


def wait_for_port(explicit=None, tries=10, interval=1.0):
    """Poll for the modem serial port to appear after Test Mode is enabled."""
    for _ in range(tries):
        ports = [explicit] if explicit else find_ports()
        ports = [p for p in ports if p and os.path.exists(p)]
        if ports:
            return ports
        time.sleep(interval)
    return []


def check_port_access(port):
    if not os.access(port, os.R_OK | os.W_OK):
        grp = ""
        try:
            import grp as grpmod

            gid = os.stat(port).st_gid
            grp = grpmod.getgrgid(gid).gr_name
        except Exception:
            grp = "dialout"
        die(
            f"no read/write access to {port}.\n"
            f"  Fix: sudo usermod -aG {grp} $USER   (then log out/in)\n"
            f"  Or run this command with sudo."
        )


REBOOT_COMMAND = "AT+CFUN=1,1"


@dataclass(frozen=True)
class ATResult:
    command: str
    reply: str
    outcome: str


def classify_reply(reply):
    """Classify observed text, not device state; negative tokens take priority."""
    if re.search(r"\bERROR\b", reply, re.IGNORECASE):
        return "error"
    if re.search(r"\bNA\b", reply, re.IGNORECASE):
        # Samsung-specific NA is not treated as positive evidence, even with OK.
        return "na"
    if re.search(r"\bOK\b", reply, re.IGNORECASE):
        return "acknowledged"
    return "unconfirmed"


def send_at_sequence(port, commands, verbose=True):
    """Send requests and return observations; replies do not verify activation."""
    check_port_access(port)
    try:
        ser = serial.Serial(port, BAUD, timeout=2)
    except serial.SerialException as e:
        die(f"cannot open {port}: {e}")

    results = []
    with ser:
        ser.reset_input_buffer()
        for cmd in commands:
            is_reboot = cmd == REBOOT_COMMAND
            sent = False
            try:
                written = ser.write((cmd + "\r\n").encode())
                if written != len((cmd + "\r\n").encode()):
                    raise serial.SerialException("incomplete command write")
                sent = True
                ser.flush()
                time.sleep(AT_DELAY)
                reply = ser.read(ser.in_waiting or 256).decode(errors="replace").strip()
                reply = reply.replace("\r", " ").replace("\n", " ").strip()
                result = ATResult(cmd, reply, classify_reply(reply))
            except (serial.SerialException, OSError) as e:
                if not is_reboot:
                    die(f"serial communication failed during '{cmd}': {e}. "
                        "Sequence stopped; device state is unverified.")
                # A dropped port may accompany reboot, but is not proof of one.
                result = ATResult(cmd, "", "disconnected" if sent else "send_failed")
            results.append(result)
            if verbose:
                status = result.reply or "(no reply)"
                mark = (red(result.outcome) if result.outcome in ("error", "na")
                        else yellow(result.outcome))
                print(f"  -> {cmd:<24} {mark}  {status}")
            if result.outcome in ("error", "na") and not is_reboot:
                die(f"modem returned a negative response for '{cmd}'. "
                    f"Aborting; device state is unverified. Reply: {result.reply}")
    return results


def report_change_results(target, results):
    """Present protocol observations without claiming reboot or active CSC."""
    preconfig = next((r for r in results
                      if r.command == f"AT+PRECONFG=2,{target}"), None)
    if (preconfig and preconfig.outcome == "acknowledged"
            and re.search(r"\+PRECONFG\s*:\s*2\s*,\s*OK\b",
                          preconfig.reply, re.IGNORECASE)):
        print(f"\nCSC configuration request for {target}: acknowledged by modem.")
    else:
        print(yellow(f"\nCSC configuration request for {target}: unconfirmed."))

    reboot = next((r for r in results if r.command == REBOOT_COMMAND), None)
    messages = {
        "na": "modem returned NA (even if followed by OK).",
        "error": "modem returned an error.",
        "acknowledged": "modem returned OK; this is only an acknowledgement.",
        "unconfirmed": "no conclusive reply received.",
        "disconnected": "serial connection lost after write; reboot is unverified.",
        "send_failed": "serial write failed; request delivery is unconfirmed.",
    }
    print(yellow("Reboot request: " + messages.get(
        reboot.outcome if reboot else None, "no result available.")))
    print("Reboot and active CSC have not been verified.")
    print("If the phone has not rebooted, reboot it manually; do not repeat the CSC sequence")
    print("solely because reboot is unconfirmed. The CSC request may already have applied.")
    print("After boot completes, verify the active CSC in Settings > About phone >")
    print("Software information > Service provider software version.")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def build_sequence(target):
    return [
        "AT+SWATD=0",
        "AT+ACTIVATE=0,0,0",
        "AT+SWATD=1",
        f"AT+PRECONFG=2,{target}",
        REBOOT_COMMAND,
    ]


def cmd_info(args):
    check_adb()
    require_one_device()
    require_samsung()
    d = device_summary()
    print(bold("Device"))
    print(f"  Model        {d['model']} ({d['name']})")
    print(f"  Android      {d['android']}")
    print(f"  Firmware     {d['firmware']}")
    print(f"  Current CSC  {bold(d['csc'])}")
    if d["csc_original"] not in ("?", d["csc"]):
        print(f"  Original CSC {d['csc_original']}")
    ports = find_ports()
    print(bold("\nSerial"))
    if ports:
        print(f"  Modem port   {', '.join(ports)} (Test Mode looks active)")
    else:
        print("  Modem port   none — dial *#0*# to enable Test Mode")


def cmd_list(args):
    check_adb()
    require_one_device()
    require_samsung()
    print(f"Current CSC: {bold(current_csc())}\n")
    codes = list_supported_csc()
    if not codes:
        print(yellow("Could not read the bundled CSC list from this device."))
        print("Scanned: " + ", ".join(CSC_DIRS))
        print("You can still try a known code with: csctool change <CSC>")
        return
    print(f"Bundled CSCs ({len(codes)}):")
    for i in range(0, len(codes), 8):
        print("  " + "  ".join(codes[i : i + 8]))


def cmd_change(args):
    target = args.csc.upper()
    if not CSC_RE.match(target):
        die(f"'{target}' is not a valid CSC (expect 3 uppercase letters, e.g. EGY).")

    check_adb()
    require_one_device()
    require_samsung()

    cur = current_csc()
    print(f"Current CSC: {bold(cur)}   Target: {bold(target)}")
    if target == cur:
        die(f"CSC is already {target}. Nothing to do.")

    supported = list_supported_csc()
    if supported:
        if target not in supported:
            die(
                f"'{target}' is not in this phone's bundle.\n"
                f"  Available: {', '.join(supported)}\n"
                f"  A code outside the bundle needs a full firmware flash (Odin)."
            )
    else:
        print(yellow("warning: could not verify the bundle list; proceeding on trust."))

    # Test Mode (manual, per design) — user dials the code.
    print()
    print(bold("Step 1: enable Test Mode on the phone"))
    print("  Open the phone dialer and type:  " + bold("*#0*#"))
    print("  The Test Mode screen opens and the USB modem port becomes available.")
    if not args.yes:
        input("  Press Enter once you've dialed *#0*# ... ")

    print(bold("\nStep 2: locating modem serial port"))
    ports = wait_for_port(explicit=args.port)
    if not ports:
        die(
            "no serial port appeared (/dev/ttyACM*, /dev/ttyUSB*).\n"
            "  Check: Test Mode dialed (*#0*#)? USB cable data-capable? phone unlocked?"
        )
    port = ports[0]
    print(f"  Using {bold(port)}")

    print(bold("\nStep 3: confirm"))
    print("  This requests a different CSC and a reboot; activation is not verified.")
    changetype = getprop("ro.omc.changetype")
    if "DATA_RESET_ON,TRUE" in changetype.upper():
        print(red("  WARNING: this firmware factory-resets on CSC change "
                  f"(ro.omc.changetype={changetype}). Back up first!"))
    else:
        print("  Knox is not tripped. Data is normally kept, but back up first anyway.")
    if not args.yes:
        ans = input(f"  Type the target CSC ({target}) to proceed: ").strip().upper()
        if ans != target:
            die("confirmation did not match. Aborted.")

    print(bold(f"\nStep 4: sending AT sequence -> {target}"))
    results = send_at_sequence(port, build_sequence(target))
    report_change_results(target, results)


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="csctool",
        description="Change Samsung CSC via the AT modem interface (no root, no Knox trip).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="show device model, current CSC, port status")
    sub.add_parser("list", help="list CSCs bundled in the phone's firmware")

    c = sub.add_parser("change", help="activate a different bundled CSC")
    c.add_argument("csc", help="target CSC code, e.g. EGY, XSG, THL")
    c.add_argument("--port", help="serial port override (e.g. /dev/ttyACM0)")
    c.add_argument("-y", "--yes", action="store_true", help="skip prompts")

    args = p.parse_args(argv)
    handlers = {"info": cmd_info, "list": cmd_list, "change": cmd_change}
    try:
        handlers[args.cmd](args)
    except KeyboardInterrupt:
        sys.exit("\naborted.")
    except subprocess.TimeoutExpired:
        die("adb command timed out. Is the device responsive?")


if __name__ == "__main__":
    main()
