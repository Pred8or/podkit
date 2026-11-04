---
'@podkit/libgpod-node': patch
---

The iPod nano 7th generation on firmware 1.0.1 can now be written. That firmware lists only SQLite post-process command set 9, not set 25, so writes failed with "no command set 25"; libgpod now falls back to set 9 and skips carrying over track choice columns that set 9 does not create.
