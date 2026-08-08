# csctool

Change the CSC (region/carrier code) on a Samsung phone **without root and without tripping Knox** — a
command-line equivalent of the "Change CSC" feature in the SamFW tool.

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

## Limits

- Only CSCs **already inside the phone's bundle** can be activated. A code from a different bundle (e.g. a US
  carrier variant) needs a full firmware flash via Odin.
- Newer devices may factory-reset on CSC change (see the warning above).
- Works on Android 9–11 era firmware. Newer One UI security patches may block the AT interface.
- Back up first anyway — you are reconfiguring the device.
- Some carrier features (e.g. a VoLTE profile) may not match your local network after the change.

## Requirements

- Linux or macOS
- `adb` on PATH (Android platform-tools)
- Python 3.8+ with `pyserial`
- USB debugging enabled on the phone

## Install

```sh
pipx install .          # from this directory
# or
pip install pyserial && python3 csctool.py <command>
```

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

## Disclaimer

For use on **your own device**. Changing the CSC reconfigures carrier/region settings; do it at your own risk.

## License

MIT — see [LICENSE](LICENSE).
