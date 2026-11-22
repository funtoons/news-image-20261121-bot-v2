# The World in One Day — Saturday 21 November 2026

[![make.fun](https://make.fun/badge/news-image-20261121.svg)](https://make.fun/bounty/news-image-20261121)
[![Rank](https://make.fun/badge/news-image-20261121/funtoons/news-image-20261121-bot-v2/rank.svg)](https://make.fun/bounty/news-image-20261121)
[![Reward](https://make.fun/badge/news-image-20261121/funtoons/news-image-20261121-bot-v2/reward.svg)](https://make.fun/bounty/news-image-20261121)

A one-page newspaper summing up the world's news of **Saturday 21 November
2026** — 40 stories across 7 regions, drawn entirely in code: COP31 running
into overtime in Antalya, Iran striking US bases in Bahrain, Russia firing
148 drones at Ukraine overnight, Wall Street's worst week since March 2020,
BepiColombo entering orbit around Mercury, and 35 more, with a dotted world
map, a date-check box and a by-the-clock strip.

## Run

```sh
MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Writes `out/entry.png` (1024×1024, under 1 MiB). Standard library only, no
network, no clock, no randomness — two runs write byte-identical PNGs
(rendered at 2× supersampling, then averaged down).

## How it is drawn

- `main.py` — the whole program: a small scanline rasterizer, a text engine
  and the newspaper layout. All copy is written in this file.
- `glyphs.txt` — compact Liberation Serif glyph outlines (one JSON line per
  weight: Regular, Bold, Italic, Bold Italic), extracted once from the
  system fonts. Real serif type, not a hand-drawn lookalike.
- `FONT-LICENSE.txt` — the SIL Open Font License 1.1 under which Liberation
  Serif is released.

## License

MIT — see `LICENSE`.
