---
name: Hardware report
about: Tell us what lorascan did on your radio (works, partly works, or not at all)
title: "[board] <module / HAT / stick> on <computer>"
labels: hardware-report
---

**Board and wiring**: module or HAT, how it connects (spidev / CH341 / other), your profile file (paste it).

**What ran** (paste the output):
```
lorascan --version
lorascan probe --profile <your profile>
lorascan selftest --profile <your profile>
```

**Scans**: which command, how long, and attach or link the report HTML if you can (the share file from
`lorascan share --dry-run` is also welcome — it contains only aggregates).

**Site**: indoors/outdoors, antenna, roughly where (a city or a 0.1° cell is plenty), what LoRa networks you
know are nearby.

**Anything odd**: errors, floors that look wrong, channels the tool called busy that you think are quiet (or
the reverse).
