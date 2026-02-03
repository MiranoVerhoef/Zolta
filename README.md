# ⚡ Zolta

A sleek, modern auction platform for internal equipment sales. Perfect for organizations looking to auction off surplus computers, monitors, and other equipment.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)

##  Features

### Public Features
-  **Live Auctions** - Real-time bidding with auto-refreshing prices
-  **Countdown Timers** - See exactly when auctions end
-  **Remember Me** - Bidder info saved in cookies
-  **Responsive Design** - Works on desktop and mobile

### Admin Features
-  **Create Auctions** - Full control over auction parameters
-  **Image Upload** - Add photos of items
-  **Flexible Pricing** - Set min/max prices and bid increments
-  **SMTP Integration** - Send bid confirmations and winner notifications
-  **Email Whitelisting** - Restrict bidding to specific domains (e.g., `@company.com`)
-  **Privacy Toggle** - Show or hide allowed domains from bidders

---

##  Quick Start

### Option 1: Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/zolta.git
cd zolta

# Start the application
docker-compose up -d

# Access at http://localhost:5000
# Admin: http://localhost:5000/admin (admin / admin123)
```

### Option 2: Docker Run

```bash
docker run -d \
  -p 5000:5000 \
  -e SECRET_KEY=your-secret-key \
  -e ADMIN_PASSWORD=your-admin-password \
  -v zolta_data:/app/instance \
  -v zolta_uploads:/app/static/uploads \
  --name zolta \
  ghcr.io/YOUR_USERNAME/zolta:latest
```

### Option 3: Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

---

##  Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask secret key for sessions | `your-secret-key-change-in-production` |
| `ADMIN_PASSWORD` | Initial admin password | `admin123` |
| `DEBUG` | Enable debug mode | `false` |

### SMTP Configuration (Admin Panel)

Configure email in **Admin** → **Settings**:

| Provider | Host | Port | Notes |
|----------|------|------|-------|
| Gmail | `smtp.gmail.com` | 587 | Use [App Password](https://myaccount.google.com/apppasswords) |
| Microsoft 365 | `smtp.office365.com` | 587 | Use full email as username |
| SendGrid | `smtp.sendgrid.net` | 587 | Username: `apikey` |

### Email Domain Whitelisting

Restrict who can bid by email domain:
- Set domains when creating an auction: `company.com, partner.org`
- Toggle **"Show allowed domains to bidders"** to show/hide the list
- Leave empty to allow all email addresses

---

##  Project Structure

```
zolta/
├── app.py                    # Main Flask application
├── Dockerfile                # Docker build instructions
├── docker-compose.yml        # Local development
├── docker-compose.ghcr.yml   # Pull from GHCR
├── requirements.txt          # Python dependencies
├── .github/
│   └── workflows/
│       └── docker-publish.yml  # Auto-publish to GHCR
├── static/
│   ├── css/style.css         # Stylesheet
│   ├── js/main.js            # Frontend JavaScript
│   └── uploads/              # Uploaded images
└── templates/
    ├── base.html             # Base template
    ├── index.html            # Homepage
    ├── auction_detail.html   # Auction page
    └── admin/
        ├── login.html        # Admin login
        ├── dashboard.html    # Manage auctions
        ├── auction_form.html # Create/edit
        ├── bids.html         # View bids
        └── settings.html     # SMTP & settings
```

---

## 🔒 Security Notes

**For Production:**

1. ✅ Change `SECRET_KEY` to a long random string
2. ✅ Change `ADMIN_PASSWORD` to something secure
3. ✅ Use HTTPS (deploy behind nginx/Traefik with SSL)
4. ✅ Backup Docker volumes regularly

---

##  Troubleshooting

**Can't login to admin?**
- Default: `admin` / `admin123`
- Check `ADMIN_PASSWORD` environment variable

**Images not uploading?**
- Max size: 16MB
- Allowed formats: PNG, JPG, JPEG, GIF, WebP

**SMTP not working?**
- Use the "Send Test Email" button in settings
- For Gmail, you must use an App Password
- Check firewall allows outbound port 587/465

**Reset the database?**
```bash
docker-compose down
docker volume rm zolta_data
docker-compose up -d
```

---

##  License

MIT License - Feel free to use and modify for your organization.

---

<p align="center">
  <strong>⚡ Zolta</strong> - Auction platform made simple
</p>
