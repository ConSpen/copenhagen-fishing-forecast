# Copenhagen Fishing Forecast

A scheduled job that watches the Øresund weather and emails me only when shore-fishing conditions are genuinely worth planning for. Runs daily on GitHub Actions. Costs nothing.

The point of the project: replace "checking the forecast every day" with a single email that arrives a few days ahead, recommending a specific window, a specific spot, a target species and what to fish with — and that arrives **only** when conditions clear a configurable bar. Silence the rest of the time.

## What an email looks like

Open [`docs/sample-email.html`](docs/sample-email.html) in a browser for a rendered preview. The plain-text version reads like this:

```
Subject: Fishing window: Sun 17 May, dawn at Nordhavn (5.0★)

COPENHAGEN FISHING FORECAST
A window worth planning for — 5.0★

When:  Sunday 17 May (in 2 days)
Time:  04:10–07:25 (dawn)
Where: Nordhavn — breakwaters and harbour edges near Sandkaj

WHY NOW
  5 m/s wind, 34% cloud, dry, water 10 °C, air 10 °C
  - Wind:       5 m/s mean, gusting 9 m/s — excellent for casting
  - Sun & moon: New Moon; a minor feeding period overlaps
  - Pressure:   steady (+0.1 hPa/3h) — excellent
  - Cloud:      34% — good light
  - Rain:       dry through the window
  - Water:      9.7 °C — excellent
  - Waves:      up to 0.2 m — excellent

WHAT TO TARGET
  Garfish (hornfisk) are at their seasonal peak this month in the
  Øresund, and the 10 °C water sits right in their range. Sea trout
  are the backup option if the garfish are quiet.

  Setup: light spinning rod with 8–18 g slim spoons fished fast and
  high in the water — or a strip of mackerel under a casting float.
  The classic trick: a tuft of red wool above the hook, so their
  bony beaks tangle in it and you barely need the hook to hold them.

  Guide: fishingindenmark.info/de/angeltipps/hornhechte-makrelen-...

OTHER WINDOWS WORTH KNOWING
  - Mon 18 May, dawn at Nordhavn (5.0★) — 3 m/s wind, 15% cloud, dry
  - Wed 20 May, dawn at Nordhavn (5.0★) — 3 m/s wind, 18% cloud, dry
  - Sun 17 May, dusk at Nordhavn (4.5★) — 8 m/s wind, 32% cloud, dry

RESOURCES
  Spot map:   fishingindenmark.info/de/angelplatze
  Rules:      fishingindenmark.info/de/informationen-und-regeln
  Fisketegn:  fisketegn.dk
```

## How it works

Every morning the workflow runs `python run.py`, which:

1. Pulls a 7-day hourly forecast for each configured spot (wind, gusts, cloud, precipitation, surface pressure, air temperature, sunrise, sunset) from [Open-Meteo](https://open-meteo.com), plus the marine forecast (wave height, sea-surface temperature) from the same provider.
2. For each day in the planning window (2–7 days out), builds two candidate fishing windows: dawn (around sunrise) and dusk (around sunset).
3. Scores each window across seven factors (see below), producing a 0–5 star rating and a transparent breakdown.
4. Recommends a target species and a concrete spinning setup based on the calendar month and the sea-surface temperature, flagging whether the run is opening, at its peak, or closing. Each species links out to its dedicated guide on [fishingindenmark.info](https://fishingindenmark.info/de) — the Danish Sportfishing Association's national fishing portal — for spot details and deeper technique notes.
5. Filters out any window that has already been emailed (the sent-log lives in `data/sent_log.json` and is committed back by the workflow).
6. If any window remains above the threshold, picks the best, builds an HTML email and sends it via Apple's SMTP server. Otherwise, nothing happens.

No third-party APIs require keys. Open-Meteo is free for non-commercial use; the solunar feeding-period data is a free public endpoint from [solunar.org](https://solunar.org). Both are treated as optional — a partial outage degrades the run but never breaks it.

## The scoring model

Each factor is scored 0–1 against a hand-tuned curve, then combined with the weights below.

| Factor       | Weight | What good looks like                                          |
| ------------ | -----: | ------------------------------------------------------------- |
| Wind         |   0.35 | 3–6 m/s mean, gusts under ~12 m/s                             |
| Solunar      |   0.16 | New or full moon; a major/minor feeding period in the window  |
| Precipitation|   0.15 | Dry, or drizzle under 0.3 mm/h                                |
| Cloud        |   0.12 | 50–90 %, especially for sea trout                             |
| Pressure     |   0.12 | Stable or slowly rising over the previous 3 hours             |
| Water temp   |   0.06 | Mostly drives species choice; penalises only the extremes     |
| Wave height  |   0.04 | Sheltered inner-harbour water (under 0.4 m)                   |

The curves and the moon-phase maths are documented in `fishing_forecast/scoring.py` and `fishing_forecast/astro.py`. Each window in the email comes with a per-factor breakdown so you can see exactly why it scored what it scored.

A note on solunar theory: it is a long-standing angling heuristic, not a fitted model. It gets 16 % of the weight, with the moon-phase part of it computed locally from a known new-moon epoch so a solunar API outage falls back to a useful baseline rather than zero signal.

## Setup

Public repo, two GitHub secrets, two Actions clicks. About fifteen minutes.

**1. Create an iCloud app-specific password.**

Sign in at [appleid.apple.com](https://appleid.apple.com), open *Sign-In and Security*, then *App-Specific Passwords*, and generate one labelled "Fishing forecast". Copy the 16-character password — it is shown once.

**2. Fork or clone this repo and push to your own GitHub.**

```bash
git clone <your fork>
cd copenhagen-fishing-forecast
git push origin main
```

**3. Add the two secrets.**

In the repo on GitHub, go to *Settings → Secrets and variables → Actions → New repository secret*:

- `ICLOUD_EMAIL` — your iCloud address (the one you want to send from and to)
- `ICLOUD_APP_PASSWORD` — the password from step 1

**4. Edit `config.yaml`.**

Change the spots, recipient address, alert threshold and (if you want) the weights. Defaults are tuned for shore spinning in Copenhagen.

**5. Enable Actions and trigger a manual run to verify.**

In the *Actions* tab, accept the workflow prompt, open the *Fishing forecast* workflow, click *Run workflow*, and tick *Dry run* the first time. The job will write the generated email to a downloadable artifact instead of sending it — so you can verify the output before the SMTP step ever fires.

After that, the workflow runs itself every morning at 06:00 UTC.

## Running locally

```bash
pip install -r requirements-dev.txt    # includes pytest
python run.py --mock --dry-run         # synthetic data, writes email to ./output
python run.py --dry-run                # live data, writes email to ./output
python run.py                          # live data, sends the email
pytest                                 # run the test suite
```

For local sends, put your credentials in a `.env` file next to `run.py`:

```
ICLOUD_EMAIL=you@icloud.com
ICLOUD_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

The `.env` file is git-ignored. Credentials are never read from `config.yaml`.

## Project layout

```
copenhagen-fishing-forecast/
├── config.yaml              # spots, threshold, scoring weights, recipient
├── run.py                   # CLI entry point
├── requirements.txt         # runtime: requests, PyYAML
├── requirements-dev.txt     # adds pytest
├── pytest.ini
├── .github/workflows/
│   ├── forecast.yml         # daily cron + manual dispatch
│   └── tests.yml            # pytest on push / PR
├── fishing_forecast/
│   ├── pipeline.py          # orchestrates a single run
│   ├── weather.py           # Open-Meteo forecast + marine
│   ├── astro.py             # sun/moon maths, dawn/dusk windows
│   ├── solunar.py           # solunar.org client + moon-phase fallback
│   ├── scoring.py           # the scoring model
│   ├── species.py           # seasonal species + spinning setups
│   ├── emailer.py           # HTML email + iCloud SMTP
│   ├── state.py             # sent-windows log (de-duplication)
│   ├── mockdata.py          # synthetic data for offline runs
│   └── config.py            # config loader with validation
├── tests/                   # pytest suite (parsing, scoring, species, state, config)
├── data/sent_log.json       # committed back by the workflow on each real run
└── docs/sample-email.html   # a rendered example email
```

## Honest caveats

- **Seven-day forecasts shift.** The email is a planning heads-up; conditions worth re-checking a day or two before going.
- **Solunar is a heuristic.** It is a soft signal, not a fitted model. The weight is set accordingly.
- **Marine data near shore can be patchy.** The Open-Meteo marine grid is sparser than the atmospheric one. Where wave height or sea-surface temperature is missing for a spot, the model scores those factors neutrally rather than guessing.
- **Fishing licence and regulations are your responsibility.** A Danish fishing licence (*fisketegn*) is required for ages 18–65. Sea trout in the Øresund have a 40 cm minimum size; coloured spawning fish should be released. Cod stocks are tightly regulated and the rules change — check the current Fiskeristyrelsen / DTU Aqua position before targeting them.

## What this demonstrates

A small but complete piece of practical AI / digital work: an event-driven automation that pulls from public APIs, applies a transparent scoring model with weighted heuristics, runs on free infrastructure (GitHub Actions), and only interrupts you when it has something to say. The code is modular, typed, covered by tests, and deliberately readable — every assumption is named.

## Licence

MIT.
