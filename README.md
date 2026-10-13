# Jupiter

A minimal Fedora 44 live ISO that boots into a TUI installer. The installer
asks for a GPU driver (NVIDIA open kernel modules or Mesa), a target disk, and
a user account, then installs the base:

## Layout

| Path | Purpose |
| --- | --- |
| `kickstart/jupiter-live.ks.in` | Live image kickstart template |
| `installer/jupiter-installer.py` | TUI installer (curses, stdlib only) |
| `installer/jupiter-installer.service` | Runs the installer on tty1 in the live image |
| `build/build-iso.sh` | Fills the template and runs `livemedia-creator` |
| `tests/test_installer.py` | Unit tests for installer logic |

## Build

Requires a Fedora host or VM, run as root:

```sh
sudo dnf install lorax-lmc-novirt pykickstart
sudo ./build/build-iso.sh
```

The ISO is written to `build/out/`.

## Test

```sh
python3 -m unittest tests/test_installer.py
python3 installer/jupiter-installer.py --dry-run --no-tui
```

## Notes

- The installer downloads packages at install time, so installation needs network access.
- Installing NVIDIA builds `akmod-nvidia-open` against the target kernel, so first boot may take a few minutes.
- The installer wipes the selected disk. Test in a VM first.
