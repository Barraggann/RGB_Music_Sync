# RGB Music Sync

Makes your PC's RGB lighting follow the song playing in **Apple Music for Windows**.

- **Lyrics view open (full-screen player):** the lights mirror its animated, blurred background *live*. Colors are sampled in a ring around the window edges, and that ring is wrapped around each fan/strip, so the LEDs move as the background moves.
- **Otherwise:** the album cover's two main colors flow around the fans as a slow gradient.
- **Keyboard:** VIA keyboards with RGB Matrix (e.g. Royal Kludge R65) get the dominant color of the same ring.
- Song changes and mode switches cross-fade smoothly.

The full-screen player window can be behind other windows (e.g. while gaming). It just can't be minimized.

## Requirements

- Windows 10/11
- [Apple Music for Windows](https://apps.microsoft.com/detail/9pfhdd62mxs1) (the Microsoft Store app)
- [Python 3.10+](https://www.python.org/downloads/) with the `py` launcher
- [OpenRGB](https://openrgb.org) for motherboard, fans, strips, RAM, GPU, mice, etc. Check that your devices show up in OpenRGB first.
- Optional: a VIA-compatible keyboard with RGB Matrix

## Setup

1. Install OpenRGB (`winget install OpenRGB.OpenRGB`), open it once and check that your devices are listed.
2. If your motherboard/RGB vendor app (Gigabyte Control Center, Armoury Crate, iCUE, ...) controls the lighting, turn its RGB control off. Otherwise the two programs fight over the LEDs.
3. Download this repo (green **Code** button → *Download ZIP*, or `git clone`) and put it in a folder you'll keep.
4. In that folder, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   This installs the Python packages and adds a startup shortcut. Add `-NoAutostart` to skip the shortcut.
5. Start it now with `wscript start_music_rgb.vbs`, or just sign out and back in.

`start_music_rgb.vbs` starts OpenRGB with its SDK server and then the sync, both without windows.

## Running manually / debugging

```powershell
py music_rgb.py            # shows what it's doing; Ctrl+C to stop
py music_rgb.py --dry-run  # only print the colors it would use, no lights needed
```

To stop the background version, end `pythonw.exe` in Task Manager. To turn off autostart, delete `MusicRGB.lnk` from the Startup folder (Win+R → `shell:startup`). If you move this folder, run `install.ps1` again.

## Settings

All settings are at the top of `music_rgb.py`:

| Setting | Default | What it does |
|---|---|---|
| `PALETTE_SIZE` | 2 | Album colors used when the lyrics view isn't open |
| `FLOW_SECONDS` | 8 | Time for the album gradient to travel around once (0 = static) |
| `PATTERN_LEDS` | 12 | LEDs per full loop of colors. Set it to the LED count of one fan. |
| `LIVE_SMOOTHING` | 0.3 | Seconds; how closely the lights follow the live background |
| `FADE_SMOOTHING` | 1.0 | Seconds; fades for song changes / mode switches |
| `LIVE_SATURATION` | 1.35 | Color boost for the (muted) live background |
| `LIVE_MIN_BRIGHTNESS` | 0.45 | Brightness lift for colored areas (dark areas stay dark) |
| `ARGB_HEADER_LEDS` | 60 | LEDs assigned to addressable headers that report 0 LEDs |
| `SKIP_ZONES` | B850M Eagle fan headers | OpenRGB zones to leave alone, per device name |
| `KEYBOARD_USB_ID` | R65 | `(vendor_id, product_id)` of your VIA keyboard, or `None` |
| `KEYBOARD_MIN_BRIGHTNESS` | 0.6 | The keyboard never goes darker than this |

### Using a different keyboard

The keyboard has to run VIA-compatible firmware (QMK/VIA, or many Royal Kludge, Keychron and Akko boards). Find its IDs in Device Manager → your keyboard → *Details* → *Hardware Ids*. For example, `VID_342D&PID_E508` becomes `KEYBOARD_USB_ID = (0x342D, 0xE508)`. Its colors are changed live and never saved to the keyboard, so unplugging it (or stopping the script with Ctrl+C) restores its own effect.

## How it works

- **Now playing + artwork:** Windows media session API (`GlobalSystemMediaTransportControls`)
- **Finding the full-screen player and its artwork/buttons:** UI Automation
- **Live background:** `PrintWindow` capture of the player window, sampled at 30 fps
- **Color math:** done in OKLab so blends don't go muddy or grey
- **Lights:** OpenRGB SDK (`openrgb-python`); keyboard over VIA raw HID (`hidapi`)

## Troubleshooting

- **Lights flash between two colors/effects:** something else is writing to the same LEDs. Usually it's the vendor RGB app (Gigabyte Control Center, Armoury Crate, iCUE, ...) still running. Quit it and turn off its autostart. It can also be two OpenRGB copies running at once (OpenRGB 1.1 runs as a Windows service; the launcher only starts OpenRGB if it isn't already running).
- **Fans keep their own rainbow and flicker while the sync runs:** OpenRGB detects the header but can't actually drive it (e.g. the IT5711 chip on newer Gigabyte B850/X870 boards with OpenRGB 1.1). Add the device and zone names, as shown in OpenRGB, to `SKIP_ZONES`. The sync then leaves those headers alone, which stops the flicker. A newer OpenRGB build may add proper support later.
- **Lights don't change:** make sure OpenRGB is running with the SDK server on (SDK Server tab → Start Server) and that another RGB app isn't overriding it.
- **Some fan LEDs stay dark:** raise `ARGB_HEADER_LEDS`, or set your zone sizes in OpenRGB (Resize zone).
- **Stuck on album colors even with lyrics open:** the full-screen player must be open (not the mini/side lyrics panel), and the window must not be minimized.
