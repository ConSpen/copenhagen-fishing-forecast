"""Copenhagen Fishing Forecast — a conditions-aware shore-fishing alerter.

The package is small and deliberately modular so each concern can be read and
tested on its own:

    config    — loads and validates config.yaml
    weather   — Open-Meteo forecast + marine data
    astro     — sunrise/sunset, dawn/dusk windows, moon phase
    solunar   — solunar.org feeding-period data (with a moon-phase fallback)
    scoring   — turns conditions into a 0–5 star rating
    species   — seasonal target species and matching spinning setups
    state     — remembers which windows have already been emailed
    emailer   — builds and sends the HTML email
    pipeline  — wires it all together
"""

__version__ = "1.0.0"
