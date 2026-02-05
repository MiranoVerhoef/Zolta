# Zolta

**Version: 1.3.13**

A sleek, modern auction platform for internal equipment sales. Perfect for organizations looking to auction off surplus computers, monitors, and other equipment.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)



## Environment Variables

These match the included `docker-compose.yml`.

- `SECRET_KEY` – Flask secret key (change in production)
- `ADMIN_PASSWORD` – initial admin password (used only if no admin exists yet)
- `DEBUG` – `true` / `false`
- `TZ` – timezone inside the container (e.g. `Europe/Amsterdam`)
- `SITE_URL` – public base URL of your Zolta instance (no trailing slash). **Required for email links** (bid confirmation + winnaarmail).

Email settings are configured via **Admin → Settings** (SMTP + notifications).


## Live bied-updates

Zolta ververst biedingen met lichte polling (ongeveer elke 2 seconden). Hierdoor werkt het betrouwbaar achter vrijwel elke reverse proxy (geen websockets/SSE nodig).


### Tijdzone (aanbevolen)
Zet `TZ=Europe/Amsterdam` zodat start/eindtijden altijd kloppen.
