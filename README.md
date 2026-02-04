# ⚡ Zolta

A sleek, modern auction platform for internal equipment sales. Perfect for organizations looking to auction off surplus computers, monitors, and other equipment.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![Python](https://img.shields.io/badge/python-3.9+-green.svg)

## ✨ Features

### Public Features
- 🔥 **Live Auctions** - Real-time bidding with auto-refreshing prices
- ⏰ **Countdown Timers** - See exactly when auctions end
- 🍪 **Remember Me** - Bidder info saved in cookies
- 📱 **Responsive Design** - Works on desktop and mobile

### Admin Features
- 📦 **Create Auctions** - Full control over auction parameters
- 📷 **Image Upload** - Add photos of items
- 💰 **Flexible Pricing** - Set min/max prices and bid increments
- 📧 **SMTP Integration** - Send bid confirmations and winner notifications
- 🔒 **Email Whitelisting** - Restrict bidding to specific domains (e.g., `@company.com`)
- 👁️ **Privacy Toggle** - Show or hide allowed domains from bidders

---

## 🚀 Quick Start

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

## 📦 Publishing to GitHub Container Registry (GHCR)

### Automatic Publishing with GitHub Actions

This repository includes a workflow that automatically builds and publishes Docker images to GHCR.

**Step 1: Create a GitHub Repository**

```bash
cd zolta
git init
git add .
git commit -m "Initial commit: Zolta auction platform"

# Create a new repo on GitHub, then:
git remote add origin https://github.com/YOUR_USERNAME/zolta.git
git branch -M main
git push -u origin main
```

**Step 2: Enable GitHub Packages**

1. Go to your repository on GitHub
2. Click **Settings** → **Actions** → **General**
3. Scroll to "Workflow permissions"
4. Select **"Read and write permissions"**
5. Click **Save**

**Step 3: Push and Build**

The GitHub Action triggers automatically on:
- Push to `main` or `master` branch → Creates `latest` tag
- Push a version tag (e.g., `v1.0.0`) → Creates versioned tag

```bash
# Push code (triggers build)
git push origin main

# Create a release (optional)
git tag v1.0.0
git push origin v1.0.0
```

**Step 4: Make Package Public (Optional)**

1. Go to your GitHub profile → **Packages**
2. Click on your `zolta` package
3. Click **Package settings** → **Change visibility** → **Public**

### Manual Publishing

```bash
# 1. Login to GHCR (use a Personal Access Token)
echo "YOUR_GITHUB_TOKEN" | docker login ghcr.io -u YOUR_USERNAME --password-stdin

# 2. Build the image
docker build -t ghcr.io/YOUR_USERNAME/zolta:latest .

# 3. Push to GHCR
docker push ghcr.io/YOUR_USERNAME/zolta:latest

# 4. Tag a version (optional)
docker tag ghcr.io/YOUR_USERNAME/zolta:latest ghcr.io/YOUR_USERNAME/zolta:v1.0.0
docker push ghcr.io/YOUR_USERNAME/zolta:v1.0.0
```

### Creating a GitHub Personal Access Token

1. Go to **GitHub** → **Settings** → **Developer settings**
2. Click **Personal access tokens** → **Tokens (classic)**
3. Click **Generate new token (classic)**
4. Select scopes:
   - `read:packages` - Pull images
   - `write:packages` - Push images
   - `delete:packages` - Delete images (optional)
5. Click **Generate token** and save it securely

---

## ⚙️ Configuration

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

## 📁 Project Structure

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

## 🛠️ Troubleshooting

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

## 📄 License

MIT License - Feel free to use and modify for your organization.

---

<p align="center">
  <strong>⚡ Zolta</strong> - Auction platform made simple
</p>


## Persistent data (important)

Zolta stores data in two paths inside the container:

- **Database:** `/app/instance/auctions.db`
- **Uploaded images:** `/app/static/uploads/`

When using Docker/Compose, make sure both paths are mounted to persistent storage so upgrades don’t wipe your data.

The provided `docker-compose.yml` already mounts:
- `zolta_data:/app/instance`
- `zolta_uploads:/app/static/uploads`

If you prefer bind mounts instead of named volumes:

```yaml
volumes:
  - ./instance:/app/instance
  - ./uploads:/app/static/uploads
```

