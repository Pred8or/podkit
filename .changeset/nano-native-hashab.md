---
'@podkit/libgpod-node': patch
'podkit': patch
---

podkit's libgpod build now signs iPod nano 6th and 7th generation databases itself, instead of needing an external `hashab` module. Both generations remain read-only: the nano 7th generation shows no music from a library written this way yet, although its signatures are valid.
