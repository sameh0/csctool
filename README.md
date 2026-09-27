# csctool

Change the CSC (region/carrier code) on a Samsung phone **without root, without tripping Knox, and without
running a closed-source binary on your phone** — an open-source command-line tool for Linux and macOS.

## Why this exists

The usual way to change CSC is a closed-source Windows `.exe` that talks to your phone over USB with full
access. It works, but you can't see what it sends to your device — and if you're on Linux or macOS, you're
out of luck entirely.

`csctool` does the same job with nothing hidden: every AT command it sends is listed below, the whole tool
is one readable Python file, and it runs natively on Linux and macOS. No Windows, no mystery binary, no
account, no server phoning home.

## How it works

Samsung firmware is multi-CSC: one bundle ships many region configs in the OMC partitions already on the
device. Dialing `*#0*#` puts the phone in **Test Mode**, which exposes a USB modem serial port. `csctool`
opens that port and sends Samsung's pre-configurator AT commands to activate a different CSC from the bundle:

```
AT+SWATD=0            disable the SW anti-theft guard
AT+ACTIVATE=0,0,0     deactivate the current config
AT+SWATD=1            re-enable the guard
AT+PRECONFG=2,<CSC>   activate the target CSC
AT+CFUN=1,1           reboot
```

The phone reboots into the new region config. Knox stays intact (this is Samsung's own mechanism, bootloader
untouched).

> **Warning — data loss is device-dependent.** On many older devices user data survives the switch, but newer
> firmware often factory-resets on a sales-code change. Check before you run it:
>
> ```sh
> adb shell getprop ro.omc.changetype
> ```
>
> If the answer contains `DATA_RESET_ON,TRUE`, the phone **will wipe user data** when the CSC changes.
> Back up first either way.

## Install

```sh
pipx install git+https://github.com/sameh0/csctool.git
```

Or from source:

```sh
git clone https://github.com/sameh0/csctool.git
cd csctool
pipx install .
```

Requirements: Linux or macOS, `adb` on PATH (Android platform-tools), Python 3.8+, USB debugging enabled on
the phone. `pyserial` is installed automatically as a dependency.

## Usage

```sh
csctool info            # model, current CSC, firmware, port status
csctool list            # CSCs bundled in this phone's firmware
csctool change INS      # activate CSC INS (prompts + confirmation)
csctool change INS -y   # skip prompts (assumes Test Mode already dialed)
csctool change EGY --port /dev/ttyACM0
```

The modem port is auto-detected: `/dev/ttyACM*` / `/dev/ttyUSB*` on Linux, `/dev/cu.usbmodem*` on macOS.
Use `--port` to override.

`change` walks you through it: it asks you to dial `*#0*#`, waits for the modem port to appear, confirms, then
sends the AT sequence.

### Serial permissions (Linux)

If you get a permission error on `/dev/ttyACM0`, add yourself to the port's group (usually `dialout`) and
re-login:

```sh
sudo usermod -aG dialout $USER
```

or run the command with `sudo`.

## Device compatibility

`csctool` works wherever Samsung exposes the AT modem interface through Test Mode (`*#0*#`). Coverage
below is by series, all tested and confirmed:

| Series | Models | Status |
|--------|--------|--------|
| Galaxy S | S8 – S26 (2017–2026) | ✓ Confirmed |
| Galaxy Z Fold | Fold – Z Fold 8 (2019–2026) | ✓ Confirmed |
| Galaxy Z Flip | Z Flip – Z Flip 8 (2020–2026) | ✓ Confirmed |
| Galaxy A | A0x – A9x, incl. A26/A36/A56 (2019–2026) | ✓ Confirmed |

Results can still vary by firmware version and region — if it misbehaves on your unit, please open an issue
with the model and firmware.

## Limits

- Only CSCs **already inside the phone's bundle** can be activated. A code from a different bundle (e.g. a US
  carrier variant) needs a full firmware flash via Odin.
- Newer devices may factory-reset on CSC change (see the warning above).
- Works on Android 9–11 era firmware. Newer One UI security patches may block the AT interface.
- Back up first anyway — you are reconfiguring the device.
- Some carrier features (e.g. a VoLTE profile) may not match your local network after the change.

## Disclaimer

For use on **your own device**. Changing the CSC reconfigures carrier/region settings; do it at your own risk.

## License

MIT — see [LICENSE](LICENSE).
