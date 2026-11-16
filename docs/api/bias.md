# Radar bias

::: dpc_radar.bias

## The measure, in full

`uv run python -m tools.build_bias --archive <folder> --dem <folder> --measure`
on the frames kept from 9 June to 16 November 2026. Every month is corrected
with the map built on the other months and compared with the «Merging»
(`CUM3`) on the three-hour windows in which either has at least 1 mm, away
from the rain gauges gone wrong.

- **MAE**: the mean error in mm; **total**: the rain of the estimate over the
  rain of the «Merging»; **correlation**: between the two, pixel by pixel.
- **≥ N mm**: how often the estimate passes N mm, over how often the
  «Merging» does (1 is right, 2 is twice as often); in brackets how well the
  two agree on where and when (CSI: 1 is always, 0 is never).
- The last column: where the «Merging» has 30 mm or more, the rain of the
  estimate over the rain of the «Merging».

"Land" is where at least a quarter of the 9 km around a pixel is land, abroad
too. The radar is "reliable" where the [reliability map](reliability.md) scores
0.5 or more.

| | | MAE (mm) | total | correlation | ≥ 10 mm | ≥ 30 mm | ≥ 50 mm | ≥ 100 mm | rain where the «Merging» has ≥ 30 mm |
|---|---|---|---|---|---|---|---|---|---|
| everywhere | radar alone | 1.91 | 1.20 | 0.78 | 1.31 (0.57) | 1.70 (0.34) | 1.81 (0.27) | 1.92 (0.26) | 0.91 |
| everywhere | one factor for Italy | 1.94 | 0.99 | 0.78 | 0.96 (0.50) | 0.94 (0.28) | 0.87 (0.24) | 0.70 (0.19) | 0.75 |
| everywhere | factor map | 1.55 | 1.00 | 0.81 | 0.97 (0.60) | 0.89 (0.38) | 0.84 (0.32) | 0.80 (0.40) | 0.75 |
| land | radar alone | 2.72 | 1.33 | 0.72 | 1.52 (0.44) | 2.04 (0.24) | 2.19 (0.18) | 2.90 (0.12) | 0.89 |
| land | one factor for Italy | 2.42 | 1.10 | 0.72 | 1.14 (0.43) | 1.15 (0.24) | 1.04 (0.18) | 1.07 (0.13) | 0.73 |
| land | factor map | 2.15 | 0.99 | 0.72 | 0.95 (0.45) | 0.82 (0.24) | 0.74 (0.18) | 0.68 (0.15) | 0.65 |
| sea | radar alone | 0.46 | 1.01 | 0.96 | 1.01 (0.87) | 1.03 (0.75) | 1.04 (0.67) | 0.91 (0.72) | 0.95 |
| sea | one factor for Italy | 1.08 | 0.83 | 0.96 | 0.70 (0.64) | 0.52 (0.43) | 0.53 (0.42) | 0.33 (0.30) | 0.78 |
| sea | factor map | 0.46 | 1.01 | 0.96 | 1.01 (0.87) | 1.03 (0.75) | 1.04 (0.67) | 0.91 (0.72) | 0.95 |
| north (from 44°N) | radar alone | 2.20 | 1.29 | 0.76 | 1.46 (0.50) | 1.88 (0.28) | 2.24 (0.21) | 2.93 (0.15) | 0.93 |
| north (from 44°N) | one factor for Italy | 2.03 | 1.07 | 0.76 | 1.08 (0.47) | 1.10 (0.28) | 1.14 (0.20) | 1.02 (0.14) | 0.77 |
| north (from 44°N) | factor map | 1.74 | 0.98 | 0.77 | 0.92 (0.50) | 0.76 (0.28) | 0.73 (0.21) | 0.61 (0.16) | 0.68 |
| centre (41.5-44°N) | radar alone | 3.61 | 1.46 | 0.67 | 1.70 (0.38) | 2.08 (0.20) | 2.05 (0.14) | 3.31 (0.10) | 0.83 |
| centre (41.5-44°N) | one factor for Italy | 3.09 | 1.21 | 0.67 | 1.31 (0.38) | 1.14 (0.19) | 0.97 (0.15) | 1.55 (0.14) | 0.68 |
| centre (41.5-44°N) | factor map | 2.70 | 1.02 | 0.67 | 0.98 (0.39) | 0.68 (0.19) | 0.66 (0.16) | 1.01 (0.20) | 0.59 |
| south (below 41.5°N) | radar alone | 3.68 | 1.38 | 0.67 | 1.59 (0.37) | 2.85 (0.16) | 2.43 (0.12) | 2.25 (0.00) | 0.83 |
| south (below 41.5°N) | one factor for Italy | 3.12 | 1.13 | 0.66 | 1.22 (0.37) | 1.54 (0.17) | 0.82 (0.15) | 0.45 (0.00) | 0.68 |
| south (below 41.5°N) | factor map | 2.94 | 1.00 | 0.64 | 0.99 (0.36) | 1.18 (0.16) | 0.76 (0.11) | 0.16 (0.00) | 0.60 |
| Sicily | radar alone | 3.58 | 1.02 | 0.62 | 1.13 (0.36) | 1.71 (0.20) | 1.28 (0.10) | 0.35 (0.00) | 0.69 |
| Sicily | one factor for Italy | 3.22 | 0.84 | 0.62 | 0.88 (0.35) | 0.87 (0.19) | 0.41 (0.08) | 0.16 (0.00) | 0.56 |
| Sicily | factor map | 3.45 | 0.96 | 0.60 | 1.03 (0.36) | 1.40 (0.15) | 1.12 (0.06) | 0.58 (0.00) | 0.61 |
| Sardinia | radar alone | 2.54 | 1.25 | 0.77 | 1.35 (0.45) | 2.25 (0.24) | 2.96 (0.18) | 9.31 (0.02) | 1.01 |
| Sardinia | one factor for Italy | 2.20 | 1.03 | 0.77 | 1.00 (0.44) | 1.28 (0.27) | 1.30 (0.20) | 0.92 (0.00) | 0.82 |
| Sardinia | factor map | 2.10 | 0.98 | 0.77 | 0.94 (0.45) | 1.07 (0.27) | 1.25 (0.21) | 0.85 (0.00) | 0.79 |
| land, radar reliable (score from 0.5) | radar alone | 2.76 | 1.32 | 0.73 | 1.49 (0.45) | 1.97 (0.24) | 2.21 (0.17) | 3.50 (0.10) | 0.89 |
| land, radar reliable (score from 0.5) | one factor for Italy | 2.45 | 1.09 | 0.72 | 1.12 (0.44) | 1.11 (0.24) | 1.06 (0.18) | 1.28 (0.12) | 0.74 |
| land, radar reliable (score from 0.5) | factor map | 2.21 | 1.00 | 0.72 | 0.95 (0.45) | 0.80 (0.23) | 0.75 (0.17) | 0.77 (0.12) | 0.65 |
| land, radar not reliable | radar alone | 2.38 | 1.37 | 0.63 | 1.90 (0.34) | 2.94 (0.18) | 1.98 (0.22) | 0.72 (0.33) | 0.81 |
| land, radar not reliable | one factor for Italy | 2.19 | 1.13 | 0.63 | 1.44 (0.33) | 1.68 (0.19) | 0.80 (0.23) | 0.33 (0.16) | 0.67 |
| land, radar not reliable | factor map | 1.65 | 0.97 | 0.69 | 0.95 (0.38) | 1.03 (0.27) | 0.69 (0.32) | 0.38 (0.32) | 0.64 |
| «Merging» under 10 mm | radar alone | 1.59 | 1.33 | 0.55 | - | - | - | - | - |
| «Merging» under 10 mm | one factor for Italy | 1.52 | 1.10 | 0.54 | - | - | - | - | - |
| «Merging» under 10 mm | factor map | 1.19 | 1.10 | 0.62 | - | - | - | - | - |
| «Merging» 10-30 mm | radar alone | 4.10 | 1.04 | 0.50 | 0.83 (0.83) | - | - | - | - |
| «Merging» 10-30 mm | one factor for Italy | 4.80 | 0.85 | 0.50 | 0.63 (0.63) | - | - | - | - |
| «Merging» 10-30 mm | factor map | 3.92 | 0.88 | 0.51 | 0.72 (0.72) | - | - | - | - |
| «Merging» 30-50 mm | radar alone | 9.35 | 0.93 | 0.28 | 0.97 (0.97) | 0.64 (0.64) | - | - | 0.93 |
| «Merging» 30-50 mm | one factor for Italy | 11.86 | 0.76 | 0.28 | 0.95 (0.95) | 0.35 (0.35) | - | - | 0.76 |
| «Merging» 30-50 mm | factor map | 10.98 | 0.76 | 0.25 | 0.93 (0.93) | 0.48 (0.48) | - | - | 0.76 |
| «Merging» from 50 mm | radar alone | 17.36 | 0.86 | 0.53 | 0.98 (0.98) | 0.87 (0.87) | 0.59 (0.59) | 1.32 (0.36) | 0.86 |
| «Merging» from 50 mm | one factor for Italy | 22.99 | 0.71 | 0.53 | 0.98 (0.98) | 0.79 (0.79) | 0.36 (0.36) | 0.52 (0.22) | 0.71 |
| «Merging» from 50 mm | factor map | 21.56 | 0.72 | 0.53 | 0.96 (0.96) | 0.72 (0.72) | 0.45 (0.45) | 0.69 (0.43) | 0.72 |
| 2026-06 | radar alone | 2.50 | 1.33 | 0.81 | 1.43 (0.54) | 2.01 (0.34) | 2.27 (0.28) | 3.00 (0.18) | 1.05 |
| 2026-06 | one factor for Italy | 2.27 | 1.10 | 0.81 | 1.13 (0.52) | 1.30 (0.34) | 1.27 (0.27) | 1.38 (0.18) | 0.87 |
| 2026-06 | factor map | 1.83 | 1.04 | 0.84 | 1.02 (0.58) | 1.06 (0.41) | 1.02 (0.36) | 0.96 (0.33) | 0.84 |
| 2026-07 | radar alone | 1.97 | 1.18 | 0.77 | 1.25 (0.52) | 1.41 (0.29) | 1.94 (0.14) | 20.58 (0.02) | 0.85 |
| 2026-07 | one factor for Italy | 1.92 | 0.97 | 0.77 | 0.91 (0.47) | 0.78 (0.25) | 0.98 (0.13) | 6.62 (0.03) | 0.71 |
| 2026-07 | factor map | 1.61 | 0.94 | 0.78 | 0.81 (0.51) | 0.65 (0.28) | 0.71 (0.16) | 1.29 (0.00) | 0.64 |
| 2026-08 | radar alone | 2.20 | 1.22 | 0.78 | 1.34 (0.50) | 1.48 (0.27) | 1.93 (0.17) | 0.94 (0.21) | 0.84 |
| 2026-08 | one factor for Italy | 2.06 | 1.01 | 0.78 | 1.00 (0.47) | 0.77 (0.24) | 0.74 (0.15) | 0.23 (0.07) | 0.70 |
| 2026-08 | factor map | 1.77 | 0.94 | 0.78 | 0.84 (0.50) | 0.52 (0.24) | 0.39 (0.12) | 0.13 (0.00) | 0.61 |
| 2026-09 | radar alone | 2.12 | 1.17 | 0.78 | 1.26 (0.57) | 1.55 (0.31) | 1.54 (0.21) | 1.63 (0.06) | 0.86 |
| 2026-09 | one factor for Italy | 2.10 | 0.96 | 0.78 | 0.93 (0.50) | 0.91 (0.28) | 0.74 (0.18) | 0.68 (0.03) | 0.70 |
| 2026-09 | factor map | 1.75 | 0.94 | 0.78 | 0.90 (0.58) | 0.70 (0.28) | 0.56 (0.17) | 0.64 (0.04) | 0.64 |
| 2026-10 | radar alone | 1.60 | 1.11 | 0.83 | 1.19 (0.63) | 1.44 (0.41) | 1.19 (0.43) | 1.16 (0.53) | 0.88 |
| 2026-10 | one factor for Italy | 1.78 | 0.89 | 0.83 | 0.84 (0.52) | 0.69 (0.30) | 0.50 (0.33) | 0.41 (0.32) | 0.71 |
| 2026-10 | factor map | 1.35 | 0.94 | 0.86 | 0.91 (0.64) | 0.84 (0.47) | 0.73 (0.46) | 0.80 (0.63) | 0.78 |
| 2026-11 | radar alone | 1.85 | 1.26 | 0.74 | 1.44 (0.57) | 2.35 (0.34) | 2.86 (0.26) | 2.35 (0.24) | 0.99 |
| 2026-11 | one factor for Italy | 1.92 | 1.06 | 0.74 | 1.06 (0.49) | 1.34 (0.27) | 1.43 (0.25) | 0.70 (0.15) | 0.83 |
| 2026-11 | factor map | 1.46 | 1.10 | 0.81 | 1.16 (0.63) | 1.44 (0.48) | 1.58 (0.38) | 0.95 (0.40) | 0.92 |
