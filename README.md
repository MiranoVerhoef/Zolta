# Zolta

A sleek, modern auction platform for internal equipment sales. Perfect for organizations looking to auction off surplus computers, monitors, and other equipment.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)



## Environment Variables

These match the default `docker-compose.yml`:

- `SECRET_KEY` (required)  
  A secure random string used for session cookies.

- `ADMIN_PASSWORD` (required)  
  Password for the `admin` user (username is always `admin`).

- `DEBUG` (optional, default: `false`)  
  Set to `true` to enable Flask debug behavior.

- `TZ` (optional, default: `Europe/Amsterdam`)  
  Timezone used for auction start/end times.

Optional (not set in compose by default):

- `ENABLE_NOTIFICATIONS` (optional, default: `false`)  
  Enables email notifications (30 minutes before end + ended).

- `AUTO_INIT` (optional, default: `true`)  
  Auto-initialize the database on startup.

- `APP_VERSION` (optional)  
  Used for cache-busting static assets.
