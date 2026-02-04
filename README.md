# Zolta

**Version:** 0.1.0

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

Email settings are configured via **Admin → Settings** (SMTP + notifications).
