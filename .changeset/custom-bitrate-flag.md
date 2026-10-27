---
"podkit": minor
---

Add `--custom-bitrate <kbps>` to `podkit sync` and document `customBitrate` as the way below the `low` preset

The presets stop at `low` (~128 kbps). The existing `customBitrate` setting (global, per device, or `PODKIT_CUSTOM_BITRATE`) replaces the preset's bitrate with any value from 64 to 320 kbps and is the cap for every lossy target, including lossy reduction of MP3/AAC sources when `[bitrate].reduce` converts. It can now also be set for a single run with `--custom-bitrate`, and `sync --dry-run` shows it on the quality line (`Quality: low (custom bitrate 96 kbps)`).

For example, `customBitrate = 96` with `[bitrate] reduce = "always"` encodes lossless sources to AAC at 96 kbps and re-encodes MP3s more than 25% above that to AAC at 96 kbps, while MP3s at or near 96 kbps are copied. Without `reduce`, MP3 and AAC files are still copied untouched. Presets are unchanged.
