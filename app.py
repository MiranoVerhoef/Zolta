from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash, Response, stream_with_context
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from datetime import datetime, timedelta
from urllib.parse import urlparse
from functools import wraps
import os
import json
from queue import Queue, Empty
from threading import Lock
from markupsafe import escape as html_escape


# Build/version string used for cache-busting static assets
APP_VERSION = os.environ.get('APP_VERSION', '1.3.17')
CONFIG_PATH = os.environ.get('CONFIG_PATH', '/app/instance/config.json')

from queue import Queue, Empty

class StreamHub:
    def __init__(self):
        self._subs = {}
        self._lock = Lock()

    def subscribe(self, auction_id: int) -> Queue:
        q = Queue()
        with self._lock:
            self._subs.setdefault(auction_id, set()).add(q)
        return q

    def unsubscribe(self, auction_id: int, q: Queue):
        with self._lock:
            s = self._subs.get(auction_id)
            if s and q in s:
                s.remove(q)
            if s and len(s) == 0:
                self._subs.pop(auction_id, None)

    def publish(self, auction_id: int, payload: dict):
        data = json.dumps(payload)
        with self._lock:
            subs = list(self._subs.get(auction_id, set()))
        for q in subs:
            try:
                q.put_nowait(data)
            except Exception:
                pass

stream_hub = StreamHub()

def get_auction_state_payload(auction_id: int) -> dict:
    auction = Auction.query.get(auction_id)
    if not auction:
        return {}
    # recent bids (verified only)
    bids = Bid.query.filter_by(auction_id=auction_id, verified=True).order_by(Bid.amount.desc()).limit(10).all()
    recent = []
    for b in bids:
        try:
            t = b.created_at.strftime('%d-%m %H:%M')
        except Exception:
            t = ''
        recent.append({"name": b.name, "amount": float(b.amount), "time": t})
    winner = None
    if effective_status == 'ended' and auction.winner_name and auction.winner_amount is not None:
        winner = {"name": auction.winner_name, "amount": float(auction.winner_amount)}
    return {
        "auction_id": auction_id,
        "status": auction.status,
        "current_price": float(auction.current_price or 0),
        "bid_count": int(Bid.query.filter_by(auction_id=auction_id, verified=True).count()),
        "recent_bids": recent,
        "winner": winner,
    }

def publish_auction_update(auction_id: int):
    try:
        stream_hub.publish(auction_id, get_auction_state_payload(auction_id))
    except Exception:
        pass


def load_config_file():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f) or {}
    except Exception as e:
        print(f"Config load warning: {e}")
    return {}

def write_config_file(settings_dict):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(settings_dict, f, indent=2, sort_keys=True)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as e:
        print(f"Config write warning: {e}")

def sync_settings_from_config():
    cfg = load_config_file()
    if not cfg:
        return
    for key, value in cfg.items():
        setting = Settings.query.filter_by(key=key).first()
        if not setting:
            setting = Settings(key=key)
            db.session.add(setting)
        setting.value = '' if value is None else str(value)
    db.session.commit()

import signal
import sys
import uuid
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# Realtime bid updates (WebSocket/SSE friendly)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")


@app.errorhandler(HTTPException)
def _handle_http_exception(e):
    """Return JSON errors for API routes (avoid HTML error pages in fetch)."""
    try:
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': e.description}), e.code
    except Exception:
        pass
    return e


@app.errorhandler(Exception)
def _handle_unexpected_exception(e):
    """Ensure API routes never return HTML on unexpected errors."""
    try:
        if request.path.startswith('/api/'):
            app.logger.exception('Unhandled API error: %s', e)
            return jsonify({'success': False, 'error': 'Interne serverfout. Probeer het opnieuw.'}), 500
    except Exception:
        pass
    # Fall back to a minimal error response for non-API routes
    app.logger.exception('Unhandled error: %s', e)
    return 'Internal Server Error', 500

@socketio.on("join_auction")
def ws_join_auction(data):
    """Join an auction room so the client receives bid updates."""
    try:
        auction_id = int(data.get("auction_id"))
    except Exception:
        return
    join_room(f"auction_{auction_id}")
    # Immediately send a snapshot to the joining client
    try:
        snapshot = _build_auction_snapshot(auction_id)
    except Exception:
        snapshot = {"auction_id": auction_id}
    socketio.emit("bid_update", snapshot, room=f"auction_{auction_id}")

def ws_broadcast_auction(auction_id: int):
    """Broadcast a fresh snapshot to all viewers of an auction."""
    try:
        snapshot = _build_auction_snapshot(auction_id)
    except Exception:
        snapshot = {"auction_id": int(auction_id)}
    socketio.emit("bid_update", snapshot, room=f"auction_{int(auction_id)}")

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/instance/auctions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

db = SQLAlchemy(app)

# --- Server-Sent Events pub/sub for realtime bid updates (single-process) ---
_AUCTION_SUBS_LOCK = Lock()
_AUCTION_SUBS: dict[int, list[Queue]] = {}

def _build_auction_snapshot(auction_id: int) -> dict:
    auction = Auction.query.get(int(auction_id))
    if not auction:
        return {'auction_id': int(auction_id)}
    bids = Bid.query.filter_by(auction_id=auction.id).order_by(Bid.created_at.desc()).limit(10).all()
    highest = auction.highest_bidder
    return {
        'auction_id': auction.id,
        'status': effective_status,
        'current_price': float(auction.current_price),
        'bid_count': Bid.query.filter_by(auction_id=auction.id).count(),
        'winner_name': highest.bidder_name if highest else None,
        'winner_amount': float(highest.amount) if highest else None,
        'notify_winner': bool(getattr(auction, 'notify_winner', False)),
        'bids': [
            {
                'name': b.bidder_name,
                'amount': float(b.amount),
                'created_at': b.created_at.isoformat()
            } for b in bids
        ][::-1]
    }

def _publish_auction_event(auction_id: int, payload: dict):
    with _AUCTION_SUBS_LOCK:
        subs = list(_AUCTION_SUBS.get(int(auction_id), []))
    for q in subs:
        try:
            q.put_nowait(payload)
        except Exception:
            pass


# Database Models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(40), default='admin')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

class Auction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)
    min_price = db.Column(db.Float, nullable=False)
    max_price = db.Column(db.Float, nullable=True)
    min_bid_increment = db.Column(db.Float, nullable=False, default=1.0)
    max_bid_increment = db.Column(db.Float, nullable=True)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    require_email_confirmation = db.Column(db.Boolean, default=True)
    whitelisted_domains = db.Column(db.Text, nullable=True)  # Comma-separated
    show_allowed_domains = db.Column(db.Boolean, default=True)  # Show domains to users
    language = db.Column(db.String(2), default='nl')  # Auction language for emails/confirmation
    winner_instructions = db.Column(db.Text, nullable=True)  # Optional winner email instructions
    # Notification options
    notify_winner = db.Column(db.Boolean, default=True)
    ending_soon_notified_at = db.Column(db.DateTime, nullable=True)
    ended_notified_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    bids = db.relationship('Bid', backref='auction', lazy=True, cascade='all, delete-orphan')

    @property
    def current_price(self):
        highest_bid = Bid.query.filter_by(auction_id=self.id).order_by(Bid.amount.desc()).first()
        return highest_bid.amount if highest_bid else self.min_price

    @property
    def highest_bidder(self):
        highest_bid = Bid.query.filter_by(auction_id=self.id).order_by(Bid.amount.desc()).first()
        return highest_bid if highest_bid else None

    @property
    def is_running(self):
        now = datetime.now()
        return self.is_active and self.start_date <= now <= self.end_date

    @property
    def status(self):
        now = datetime.now()
        if not self.is_active:
            return 'inactive'
        if now < self.start_date:
            return 'upcoming'
        if now > self.end_date:
            return 'ended'
        return 'active'

class Bid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    auction_id = db.Column(db.Integer, db.ForeignKey('auction.id'), nullable=False)
    bidder_name = db.Column(db.String(100), nullable=False)
    bidder_email = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BidVerification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    auction_id = db.Column(db.Integer, db.ForeignKey('auction.id'), nullable=False)
    bidder_name = db.Column(db.String(100), nullable=False)
    bidder_email = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_expired(self):
        return datetime.now() > self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

# Helper Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function


def staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        role = session.get('admin_role', 'admin')
        if role not in ('admin', 'auction_creator'):
            flash('Access denied', 'error')
            return redirect(url_for('admin_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def validate_email_domain(email, whitelisted_domains):
    if not whitelisted_domains:
        return True
    domains = [d.strip().lower() for d in whitelisted_domains.split(',') if d.strip()]
    if not domains:
        return True
    email_domain = email.split('@')[-1].lower()
    return email_domain in domains

def get_smtp_settings():
    """Get SMTP settings from database"""
    settings = {s.key: s.value for s in Settings.query.all()}
    return {
        'enabled': settings.get('smtp_enabled', 'false').lower() == 'true',
        'host': settings.get('smtp_host', ''),
        'port': int(settings.get('smtp_port', '587') or '587'),
        'username': settings.get('smtp_username', ''),
        'password': settings.get('smtp_password', ''),
        'from_email': settings.get('smtp_from_email', ''),
        'from_name': settings.get('smtp_from_name', 'Zolta'),
        'use_tls': settings.get('smtp_use_tls', 'true').lower() == 'true'
    }


def get_all_settings():
    """Return settings dict from DB"""
    return {s.key: s.value for s in Settings.query.all()}


def get_setting(key, default=None):
    settings = get_all_settings()
    val = settings.get(key)
    if val is None or val == '':
        return default
    return val


def compute_effective_status(auction, now=None):
    """Compute status based on start/end timestamps (does not mutate DB model).

    NOTE: Auction start/end datetimes are stored as *naive local* timestamps. To make this
    consistent regardless of container timezone, we compute 'now' in Europe/Amsterdam and
    drop tzinfo before comparing.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if now is None:
        now = datetime.now(ZoneInfo('Europe/Amsterdam')).replace(tzinfo=None)

    start = getattr(auction, 'start_date', None)
    end = getattr(auction, 'end_date', None)

    if start and now < start:
        return 'upcoming'
    if end and now >= end:
        return 'ended'
    return 'active'

def get_site_language():
    # Zolta is Dutch-only
    return 'nl'

TRANSLATIONS = {
    'nl': {

        'confirm_bid_subject': 'Bevestig je bod - {title}',
        'confirm_bid_heading': 'Bevestig je bod',
        'confirm_bid_cta': 'Klik hier om je bod te bevestigen',
        'confirm_bid_expires': 'Deze link verloopt over 30 minuten.',
        'ending_soon_subject': 'Veiling eindigt bijna: {title}',
        'ended_subject': 'Veiling afgelopen: {title}',
        'winner_subject': 'Je hebt gewonnen: {title}',
        'terms_label': 'Bij het plaatsen van een bod ga je akkoord met de voorwaarden',
        'terms_text': 'De website is niet verantwoordelijk voor iets dat te maken heeft met de veiling of de geveilde goederen.',
        'terms_required': 'Je moet de voorwaarden accepteren voordat je een bod kunt plaatsen.',

        'auctions': 'Veilingen',
        'admin_panel': 'Beheer',
        'admin': 'Admin',
        'logout': 'Uitloggen',
        'back_to_auctions': 'Terug naar veilingen',
        'description': 'Beschrijving',
        'bid_history': 'Biedgeschiedenis (Top 10)',
        'live_now': 'Nu live',
        'starting_soon': 'Start binnenkort',
        'ended': 'Afgelopen',
        'final_price': 'Winnend bod',
        'current_bid': 'Huidig bod',
        'starting_price': 'Startprijs',
        'place_your_bid': 'Plaats je bod',
        'your_name': 'Je naam',
        'email_address': 'E-mailadres',
        'bid_amount': 'Bedrag (€)',
        'place_bid': 'Bod plaatsen',
        'terms_label': 'Wanneer je een bod plaatst moet je akkoord gaan met de voorwaarden',
        'terms_text': 'De website is niet verantwoordelijk voor iets dat te maken heeft met de veiling of de geveilde goederen.',
        'view_terms': 'Bekijk voorwaarden',
        'close': 'Sluiten',
        'winner': 'Winnaar',
        'you_won': 'Je hebt gewonnen!',
        'verification_email_sent': 'Check je e-mail om je bod te bevestigen. Daarna hoef je 7 dagen niet opnieuw te verifiëren.',
        'homepage_title': 'Zolta Veilingen',
        'live_auctions': 'Live veilingen',
        'upcoming_auctions': ' Aankomende veilingen',
        'recently_ended': ' Recent afgelopen',
        'no_auctions_yet': 'Nog geen veilingen',
        'check_back_soon': 'Kom later terug voor nieuwe veilingen!',
    }
}

def t_for_lang(lang, key):
    lang = (lang or '').strip().lower()
    if lang != 'nl':
        lang = 'nl'
    return (TRANSLATIONS.get(lang, {}).get(key) or key)


@app.context_processor
def inject_helpers():
    def t(key: str):
        # Dutch-only
        return (TRANSLATIONS.get('nl', {}).get(key) or key)

    def t_for(lang: str, key: str):
        return t_for_lang(lang, key)

    def now():
        return datetime.now()

    return dict(
        t=t,
        t_for=t_for,
        site_lang=get_site_language(),
        now=now,
        settings=get_all_settings(),
        theme_background='#dcdcdc',
        app_version=APP_VERSION
    )


def send_email(to_email, subject, html_body, text_body=None):
    """Send email via SMTP"""
    smtp = get_smtp_settings()
    
    if not smtp['enabled']:
        print(f"SMTP disabled. Would send to {to_email}: {subject}")
        return False, "SMTP is not enabled"
    
    if not all([smtp['host'], smtp['username'], smtp['password'], smtp['from_email']]):
        return False, "SMTP not fully configured"
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{smtp['from_name']} <{smtp['from_email']}>"
        msg['To'] = to_email
        
        if text_body:
            msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        if smtp['use_tls']:
            server = smtplib.SMTP(smtp['host'], smtp['port'])
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp['host'], smtp['port'])
        
        server.login(smtp['username'], smtp['password'])
        server.sendmail(smtp['from_email'], to_email, msg.as_string())
        server.quit()
        
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)


def get_site_url():
    """Return the public base URL (no trailing slash) used for emails/assets.
    Configure via env SITE_URL or DB setting key 'site_url'.
    """
    url = (os.environ.get('SITE_URL') or get_setting('site_url', '') or '').strip()
    return url.rstrip('/')

def base_url_from_external_url(external_url: str) -> str:
    try:
        p = urlparse(external_url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ''

def build_email_html(title: str, heading: str, intro_html: str, *, cta_text: str | None = None, cta_url: str | None = None, footer_html: str | None = None, base_url: str | None = None) -> str:
    """Lightweight, email-client friendly HTML shell (table-based)."""
    base_url = (base_url or '').rstrip('/')
    if not base_url and cta_url:
        base_url = base_url_from_external_url(cta_url).rstrip('/')
    logo_url = f"{base_url}/static/img/zolta-email.png" if base_url else ''

    # inline styles for compatibility
    btn = ''
    if cta_text and cta_url:
        btn = f'''        <tr>          <td style="padding: 18px 0 6px 0;">            <a href="{cta_url}" style="display:inline-block;padding:12px 18px;border-radius:9999px;background:#2563eb;color:#ffffff;text-decoration:none;font-weight:700;">{cta_text}</a>          </td>        </tr>'''

    footer_html = footer_html or ''

    logo_block = ''
    if logo_url:
        logo_block = f'''        <tr>          <td style="padding: 10px 0 4px 0;">            <img src="{logo_url}" width="32" height="32" alt="" style="display:block;border:0;outline:none;text-decoration:none;border-radius:10px;"/>          </td>        </tr>'''

    return f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{title}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e5e7eb;">
          <tr>
            <td style="padding:22px 22px 18px 22px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {logo_block}
                <tr>
                  <td style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:18px;font-weight:800;color:#111827;padding:10px 0 4px 0;">{heading}</td>
                </tr>
                <tr>
                  <td style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;line-height:1.5;color:#374151;">{intro_html}</td>
                </tr>
                {btn}
                <tr>
                  <td style="padding: 14px 0 0 0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:12px;line-height:1.5;color:#6b7280;">{footer_html}</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        <div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:12px;color:#9ca3af;margin-top:14px;">Zolta</div>
      </td>
    </tr>
  </table>
</body>
</html>'''

def _unique_bidder_emails(auction_id: int):
    bids = Bid.query.filter_by(auction_id=auction_id).all()
    return sorted({b.bidder_email.strip().lower() for b in bids if b.bidder_email})

def check_and_send_auction_notifications():
    """Send email notifications.

    - 'Ending soon' emails: 30 minutes before end (once)
    - 'Ended' emails: right after end (once)
    - Winner email: sent once when the auction ends (if enabled)
    """
    now = datetime.now()
    soon_threshold = now + timedelta(minutes=30)
    site_url = get_site_url()

    def _auction_link(a: 'Auction') -> str:
        return f"{site_url}/auction/{a.id}" if site_url else ''

    # --- Ending soon ---
    soon_auctions = Auction.query.filter(
        Auction.is_active == True,
        Auction.start_date <= now,
        Auction.end_date > now,
        Auction.end_date <= soon_threshold,
        Auction.ending_soon_notified_at.is_(None)
    ).all()

    for auction in soon_auctions:
        # mark first to avoid duplicates if sending takes time
        auction.ending_soon_notified_at = now
        db.session.commit()

        emails = _unique_bidder_emails(auction.id)
        if not emails:
            continue

        end_str = auction.end_date.strftime('%d-%m-%Y %H:%M')
        current = auction.current_price
        link = _auction_link(auction)

        intro = f"""
            <p>De veiling <strong>{auction.title}</strong> eindigt binnen 30 minuten.</p>
            <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;border-collapse:collapse;margin-top:12px;\">
              <tr>
                <td style=\"padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;\">
                  <div style=\"font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;\">Huidig bod</div>
                  <div style=\"font-size:20px;font-weight:800;color:#111827;\">€{current:.2f}</div>
                  <div style=\"margin-top:6px;font-size:12px;color:#6b7280;\">Eindtijd: <strong>{end_str}</strong></div>
                </td>
              </tr>
            </table>
        """

        subject = t_for_lang('nl', 'ending_soon_subject').format(title=auction.title)
        html = build_email_html(
            title=subject,
            heading='Veiling eindigt bijna',
            intro_html=intro,
            cta_text='Open veiling' if link else None,
            cta_url=link if link else None,
            footer_html='Je ontvangt deze mail omdat je eerder een bod hebt geplaatst op deze veiling.',
            base_url=site_url
        )

        text = f"""Veiling eindigt bijna

Veiling: {auction.title}
Eindtijd: {end_str}
Huidig bod: €{current:.2f}

{('Open veiling: ' + link) if link else ''}
"""

        for em in emails:
            send_email(em, subject, html, text)

    # --- Ended ---
    ended_auctions = Auction.query.filter(
        Auction.is_active == True,
        Auction.end_date < now,
        Auction.ended_notified_at.is_(None)
    ).all()

    for auction in ended_auctions:
        auction.ended_notified_at = now
        db.session.commit()

        bids = Bid.query.filter_by(auction_id=auction.id).order_by(Bid.amount.desc()).all()
        highest = bids[0] if bids else None
        winner_line_html = ''
        winner_line_text = ''
        if highest and auction.notify_winner:
            winner_line_html = f"<p><strong>Winnaar:</strong> {highest.bidder_name} met €{highest.amount:.2f}</p>"
            winner_line_text = f"Winnaar: {highest.bidder_name} met €{highest.amount:.2f}\n"

        end_str = auction.end_date.strftime('%d-%m-%Y %H:%M')
        final_price = auction.current_price
        link = _auction_link(auction)

        subject = t_for_lang('nl', 'ended_subject').format(title=auction.title)
        intro = f"""
            <p>De veiling <strong>{auction.title}</strong> is afgelopen.</p>
            <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;border-collapse:collapse;margin-top:12px;\">
              <tr>
                <td style=\"padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;\">
                  <div style=\"font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;\">Winnend bod</div>
                  <div style=\"font-size:20px;font-weight:800;color:#111827;\">€{final_price:.2f}</div>
                  <div style=\"margin-top:6px;font-size:12px;color:#6b7280;\">Eindtijd: <strong>{end_str}</strong></div>
                </td>
              </tr>
            </table>
            {winner_line_html}
        """

        html = build_email_html(
            title=subject,
            heading='Veiling afgelopen',
            intro_html=intro,
            cta_text='Bekijk veiling' if link else None,
            cta_url=link if link else None,
            footer_html='Bedankt voor het meedoen.',
            base_url=site_url
        )

        text = f"""Veiling afgelopen

Veiling: {auction.title}
Eindtijd: {end_str}
Winnend bod: €{final_price:.2f}
{winner_line_text}
{('Bekijk veiling: ' + link) if link else ''}
"""

        # send to all bidders (unique)
        for em in sorted({b.bidder_email.strip().lower() for b in bids if b.bidder_email}):
            send_email(em, subject, html, text)

        # Winner email (separate)
        if highest and auction.notify_winner and highest.bidder_email:
            w_subject = t_for_lang('nl', 'winner_subject').format(title=auction.title)
            instruction_text = (auction.winner_instructions or 'Neem contact op met de veilinghouder om afhalen/betalen af te stemmen.').strip()
            instruction_html = html_escape(instruction_text)
            w_intro = f"""
                <p>Hallo {highest.bidder_name},</p>
                <p><strong>Gefeliciteerd!</strong> Je hebt de veiling <strong>{auction.title}</strong> gewonnen.</p>
                <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;border-collapse:collapse;margin-top:12px;\">
                  <tr>
                    <td style=\"padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;\">
                      <div style=\"font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;\">Winnend bod</div>
                      <div style=\"font-size:22px;font-weight:900;color:#111827;\">€{highest.amount:.2f}</div>
                    </td>
                  </tr>
                </table>
                <p style=\"margin-top:12px;\">{instruction_html}</p>
            """

            w_html = build_email_html(
                title=w_subject,
                heading='Je hebt gewonnen!',
                intro_html=w_intro,
                cta_text='Bekijk veiling' if link else None,
                cta_url=link if link else None,
                footer_html='Bedankt voor het meedoen.',
                base_url=site_url
            )
            w_text = f"""Je hebt gewonnen!

Veiling: {auction.title}
Winnend bod: €{highest.amount:.2f}

{instruction_text}
{('Bekijk veiling: ' + link) if link else ''}
"""
            send_email(highest.bidder_email, w_subject, w_html, w_text)

def start_notification_scheduler():
    if os.environ.get('ENABLE_NOTIFICATIONS', 'true').lower() != 'true':
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception as e:
        print(f"APScheduler not available: {e}")
        return

    scheduler = BackgroundScheduler(daemon=True)
    def _job():
        with app.app_context():
            # request context needed for url_root; build without it
            # so generate links from SITE_URL setting if present
            try:
                # Monkey patch request-less context
                check_and_send_auction_notifications()
            except Exception as e:
                print(f"Notification job error: {e}")

    scheduler.add_job(_job, 'interval', seconds=60, id='auction_notifications', replace_existing=True)
    scheduler.start()
    print("Auction notification scheduler started")

# Public Routes
@app.route('/')
def index():
    now = datetime.now()
    active_auctions = Auction.query.filter(
        Auction.is_active == True,
        Auction.start_date <= now,
        Auction.end_date >= now
    ).order_by(Auction.end_date.asc()).all()
    
    upcoming_auctions = Auction.query.filter(
        Auction.is_active == True,
        Auction.start_date > now
    ).order_by(Auction.start_date.asc()).all()
    
    ended_auctions = Auction.query.filter(
        Auction.is_active == True,
        Auction.end_date < now
    ).order_by(Auction.end_date.desc()).limit(10).all()
    
    return render_template('index.html', 
                         active_auctions=active_auctions,
                         upcoming_auctions=upcoming_auctions,
                         ended_auctions=ended_auctions)

@app.route('/auction/<int:auction_id>')
def auction_detail(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    effective_status = compute_effective_status(auction)


    bids = Bid.query.filter_by(auction_id=auction_id).order_by(Bid.amount.desc()).limit(10).all()
    
    # Get saved user info from cookies
    saved_name = request.cookies.get('bidder_name', '')
    saved_email = request.cookies.get('bidder_email', '')
    
    return render_template('auction_detail.html', 
                         auction=auction, 
                         bids=bids,
                         saved_name=saved_name,
                         saved_email=saved_email,
                         effective_status=effective_status)

@app.route('/api/auction/<int:auction_id>/bid', methods=['POST'])
def place_bid(auction_id):
    """Place a bid via JSON API. Always responds with JSON (never HTML)."""
    try:
        auction = Auction.query.get_or_404(auction_id)

        # Use effective status so bidding opens/closes correctly even when container TZ differs
        effective_status = compute_effective_status(auction)
        if effective_status != 'active':
            return jsonify({'success': False, 'error': 'Deze veiling accepteert momenteel geen biedingen.'}), 400

        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        amount = data.get('amount')

        # Validation
        if not name or not email or amount in (None, ''):
            return jsonify({'success': False, 'error': 'Naam, e-mailadres en bedrag zijn verplicht.'}), 400

        try:
            amount = float(amount)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Ongeldig bedrag.'}), 400

        # Email domain validation
        if auction.whitelisted_domains:
            if not validate_email_domain(email, auction.whitelisted_domains):
                allowed = auction.whitelisted_domains.replace(',', ', ')
                return jsonify({'success': False, 'error': f'E-mailadres moet eindigen op een van deze domeinen: {allowed}'}), 400

        # Bid amount validation
        current_price = auction.current_price
        min_bid = current_price + auction.min_bid_increment

        if amount < min_bid:
            return jsonify({'success': False, 'error': f'Minimum bod is €{min_bid:.2f}'}), 400

        if auction.max_bid_increment:
            max_bid = current_price + auction.max_bid_increment
            if amount > max_bid:
                return jsonify({'success': False, 'error': f'Maximum bod is €{max_bid:.2f}'}), 400

        if auction.max_price and amount > auction.max_price:
            return jsonify({'success': False, 'error': f'Het bod mag niet hoger zijn dan €{auction.max_price:.2f}'}), 400

        # Email confirmation flow (7-day remembered verification)
        if auction.require_email_confirmation:
            verified_email = (request.cookies.get('verified_email') or '').strip().lower()
            verified_until_raw = (request.cookies.get('verified_until') or '').strip()
            is_verified = False
            try:
                verified_until = datetime.utcfromtimestamp(int(verified_until_raw)) if verified_until_raw else None
                if verified_until and verified_until > datetime.now() and verified_email == email:
                    is_verified = True
            except Exception:
                is_verified = False

            if not is_verified:
                token = uuid.uuid4().hex
                verification = BidVerification(
                    token=token,
                    auction_id=auction_id,
                    bidder_name=name,
                    bidder_email=email,
                    amount=amount,
                    expires_at=datetime.now() + timedelta(minutes=30)
                )
                db.session.add(verification)
                db.session.commit()

                verify_url = url_for('verify_bid', token=token, _external=True)
                lang = getattr(auction, 'language', None) or get_site_language()
                base_url = base_url_from_external_url(verify_url) or get_site_url()

                intro_html = f"""
                    <p>Hallo {name},</p>
                    <p>Je staat op het punt om je bod te bevestigen voor <strong>{auction.title}</strong>.</p>
                    <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-top:12px;">
                      <tr>
                        <td style="padding:10px 12px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb;">
                          <div style="font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;">Bedrag</div>
                          <div style="font-size:20px;font-weight:800;color:#111827;">€{amount:.2f}</div>
                        </td>
                      </tr>
                    </table>
                """

                html_body = build_email_html(
                    title=t_for_lang(lang, 'confirm_bid_subject').format(title=auction.title),
                    heading=t_for_lang(lang, 'confirm_bid_heading'),
                    intro_html=intro_html,
                    cta_text=t_for_lang(lang, 'confirm_bid_cta'),
                    cta_url=verify_url,
                    footer_html=t_for_lang(lang, 'confirm_bid_expires'),
                    base_url=base_url
                )

                text_body = f"""{t_for_lang(lang, 'confirm_bid_heading')}

Veiling: {auction.title}
Bedrag: €{amount:.2f}

{t_for_lang(lang, 'confirm_bid_cta')}: {verify_url}

{t_for_lang(lang, 'confirm_bid_expires')}
"""

                subject = t_for_lang(lang, 'confirm_bid_subject').format(title=auction.title)

                success, message = send_email(
                    email,
                    subject,
                    html_body,
                    text_body
                )

                if not success:
                    return jsonify({'success': False, 'error': f'E-mailbevestiging is vereist, maar verzenden van e-mail is mislukt: {message}'}), 400

                return jsonify({
                    'success': True,
                    'verification_required': True,
                    'message': TRANSLATIONS.get('nl', {}).get('verification_email_sent')
                }), 202

        # Create bid
        bid = Bid(
            auction_id=auction_id,
            bidder_name=name,
            bidder_email=email,
            amount=amount
        )
        db.session.add(bid)
        db.session.commit()

        # Notify viewers
        try:
            _publish_auction_event(auction_id, {'type': 'snapshot', 'data': _build_auction_snapshot(auction_id)})
        except Exception:
            pass
        try:
            ws_broadcast_auction(auction_id)
        except Exception:
            pass

        response = jsonify({
            'success': True,
            'message': 'Bod geplaatst!',
            'new_price': amount,
            'bid_id': bid.id
        })

        # Save to cookies
        response.set_cookie('bidder_name', name, max_age=30*24*60*60)
        response.set_cookie('bidder_email', email, max_age=30*24*60*60)

        return response

    except Exception as e:
        app.logger.exception('Bid placement failed: %s', e)
        return jsonify({'success': False, 'error': 'Interne serverfout. Probeer het opnieuw.'}), 500



@app.route('/verify/<token>')
def verify_bid(token):
    verification = BidVerification.query.filter_by(token=token).first_or_404()

    # Helper: always set bidder + verification cookies so the user won't need to verify again,
    # even if the bid can no longer be placed (e.g. someone else outbid in the meantime).
    def _resp_with_cookies(resp):
        resp.set_cookie('bidder_name', verification.bidder_name, max_age=30*24*60*60)
        resp.set_cookie('bidder_email', verification.bidder_email, max_age=30*24*60*60)

        verified_until = int((datetime.now() + timedelta(days=7)).timestamp())
        resp.set_cookie('verified_email', verification.bidder_email, max_age=7*24*60*60)
        resp.set_cookie('verified_until', str(verified_until), max_age=7*24*60*60)
        return resp

    # If already used/expired, still set cookies (user did verify earlier) but don't place bid again.
    if verification.is_used:
        flash('Deze bevestigingslink is al gebruikt.', 'error')
        return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=verification.auction_id)))

    if verification.is_expired:
        flash('Deze bevestigingslink is verlopen. Plaats je bod opnieuw.', 'error')
        return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=verification.auction_id)))

    auction = Auction.query.get_or_404(verification.auction_id)

    # Mark the verification as used regardless of whether the bid is still valid at this moment.
    # This prevents re-using the same confirmation link multiple times.
    verification.used_at = datetime.now()
    db.session.commit()

    # If auction is not running anymore, just remember the user and redirect.
    if not auction.is_running:
        flash('Deze veiling accepteert geen biedingen meer.', 'error')
        return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=auction.id)))

    # Re-validate bid amount at confirmation time
    current_price = auction.current_price
    min_bid = current_price + auction.min_bid_increment
    amount = float(verification.amount)

    if amount < min_bid:
        flash('Email bevestigd. Je bod was inmiddels ingehaald; plaats een nieuw bod.', 'info')
        return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=auction.id)))

    if auction.max_bid_increment:
        max_bid = current_price + auction.max_bid_increment
        if amount > max_bid:
            flash(f'Je bod is nu te hoog. Maximum bod is €{max_bid:.2f}.', 'error')
            return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=auction.id)))

    if auction.max_price and amount > auction.max_price:
        flash(f'Het bod mag niet hoger zijn dan €{auction.max_price:.2f}.', 'error')
        return _resp_with_cookies(redirect(url_for('auction_detail', auction_id=auction.id)))

    bid = Bid(
        auction_id=auction.id,
        bidder_name=verification.bidder_name,
        bidder_email=verification.bidder_email,
        amount=amount
    )
    db.session.add(bid)
    db.session.commit()

    # Realtime update for other viewers
    try:
        _publish_auction_event(auction.id, {'type': 'snapshot', 'data': _build_auction_snapshot(auction.id)})
        ws_broadcast_auction(auction.id)
    except Exception:
        pass

    resp = _resp_with_cookies(redirect(url_for('auction_detail', auction_id=auction.id)))
    flash('Bod bevestigd en geplaatst!', 'success')
    return resp

@app.route('/api/auction/<int:auction_id>/stream')
def auction_stream(auction_id):
    """Server-Sent Events stream for real-time bid updates."""
    def gen():
        q = stream_hub.subscribe(auction_id)
        try:
            # send initial state
            initial = get_auction_state_payload(auction_id)
            yield f"data: {json.dumps(initial)}\n\n"
            while True:
                msg = q.get()
                if msg is None:
                    break
                yield f"data: {msg}\n\n"
        except GeneratorExit:
            pass
        except Exception:
            # never crash the worker because a client disconnected
            pass
        finally:
            stream_hub.unsubscribe(auction_id, q)
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(gen(), headers=headers)


@app.route('/api/auction/<int:auction_id>/status')
def auction_status(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    highest_bid = auction.highest_bidder
    
    effective_status = compute_effective_status(auction)
    return jsonify({
        'current_price': auction.current_price,
        'highest_bidder': highest_bid.bidder_name if highest_bid else None,
        'bid_count': len(auction.bids),
        'status': effective_status,
        'end_date': auction.end_date.isoformat()
    })


@app.route('/api/auction/<int:auction_id>/state')
def auction_state(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    effective_status = compute_effective_status(auction)


    bids = Bid.query.filter_by(auction_id=auction_id).order_by(Bid.amount.desc()).limit(10).all()
    highest = auction.highest_bidder

    saved_email = (request.cookies.get('bidder_email') or '').strip().lower()
    highest_email = (highest.bidder_email or '').strip().lower() if highest else ''
    is_winner = bool(saved_email and highest and saved_email == highest_email)

    winner_name = highest.bidder_name if (effective_status == 'ended' and is_winner and highest) else None
    winner_amount = float(highest.amount) if (effective_status == 'ended' and is_winner and highest) else None

    return jsonify({
        'auction_id': auction.id,
        'status': effective_status,
        'current_price': auction.current_price,
        'bid_count': len(auction.bids),
        'highest_bidder_name': highest.bidder_name if highest else None,
        'highest_bidder_email': highest.bidder_email if highest else None,
        'highest_bid_amount': float(highest.amount) if highest else None,
        'start_date': auction.start_date.isoformat(),
        'end_date': auction.end_date.isoformat(),
        'notify_winner': bool(getattr(auction, 'notify_winner', False)),
        'is_winner': is_winner,
        'winner_name': winner_name,
        'winner_amount': winner_amount,
        'bids': [{
            'name': b.bidder_name,
            'amount': float(b.amount),
            'created_at': b.created_at.isoformat()
        } for b in bids]
    })


# Admin Routes
@app.route('/admin')
@staff_required
def admin_dashboard():
    auctions = Auction.query.order_by(Auction.created_at.desc()).all()
    return render_template('admin/dashboard.html', auctions=auctions)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session['admin_role'] = getattr(admin, 'role', 'admin')
            return redirect(url_for('admin_dashboard'))
        
        flash('Invalid credentials', 'error')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('index'))

@app.route('/admin/auction/new', methods=['GET', 'POST'])
@staff_required
def admin_new_auction():
    if request.method == 'POST':
        # Handle file upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                image_filename = f"{uuid.uuid4().hex}.{ext}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], image_filename))
        
        # Parse dates
        start_date = datetime.fromisoformat(request.form.get('start_date'))
        end_date = datetime.fromisoformat(request.form.get('end_date'))
        
        # Create auction
        auction = Auction(
            title=request.form.get('title'),
            description=request.form.get('description'),
            image_filename=image_filename,
            min_price=float(request.form.get('min_price')),
            max_price=float(request.form.get('max_price')) if request.form.get('max_price') else None,
            min_bid_increment=float(request.form.get('min_bid_increment', 1)),
            max_bid_increment=float(request.form.get('max_bid_increment')) if request.form.get('max_bid_increment') else None,
            start_date=start_date,
            end_date=end_date,
            require_email_confirmation=request.form.get('require_email_confirmation') == 'on',
            whitelisted_domains=request.form.get('whitelisted_domains', '').strip() or None,
            show_allowed_domains=request.form.get('show_allowed_domains') == 'on',
            notify_winner=request.form.get('notify_winner') == 'on',
            winner_instructions=request.form.get('winner_instructions', '').strip() or None,
            language='nl',
            is_active=True
        )
        
        db.session.add(auction)
        db.session.commit()
        
        flash('Auction created successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/auction_form.html', auction=None)

@app.route('/admin/auction/<int:auction_id>/edit', methods=['GET', 'POST'])
@staff_required
def admin_edit_auction(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    
    if request.method == 'POST':
        # Handle file upload
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                # Delete old image
                if auction.image_filename:
                    old_path = os.path.join(app.config['UPLOAD_FOLDER'], auction.image_filename)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                
                ext = file.filename.rsplit('.', 1)[1].lower()
                auction.image_filename = f"{uuid.uuid4().hex}.{ext}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], auction.image_filename))
        
        # Update fields
        auction.title = request.form.get('title')
        auction.description = request.form.get('description')
        auction.min_price = float(request.form.get('min_price'))
        auction.max_price = float(request.form.get('max_price')) if request.form.get('max_price') else None
        auction.min_bid_increment = float(request.form.get('min_bid_increment', 1))
        auction.max_bid_increment = float(request.form.get('max_bid_increment')) if request.form.get('max_bid_increment') else None
        auction.start_date = datetime.fromisoformat(request.form.get('start_date'))
        auction.end_date = datetime.fromisoformat(request.form.get('end_date'))
        auction.require_email_confirmation = request.form.get('require_email_confirmation') == 'on'
        auction.whitelisted_domains = request.form.get('whitelisted_domains', '').strip() or None
        auction.show_allowed_domains = request.form.get('show_allowed_domains') == 'on'
        auction.notify_winner = request.form.get('notify_winner') == 'on'
        auction.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        
        flash('Auction updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/auction_form.html', auction=auction)

@app.route('/admin/auction/<int:auction_id>/delete', methods=['POST'])
@staff_required
def admin_delete_auction(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    
    # Delete image file
    if auction.image_filename:
        image_path = os.path.join(app.config['UPLOAD_FOLDER'], auction.image_filename)
        if os.path.exists(image_path):
            os.remove(image_path)
    
    db.session.delete(auction)
    db.session.commit()
    
    flash('Auction deleted successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/auction/<int:auction_id>/bids')
@admin_required
def admin_auction_bids(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    bids = Bid.query.filter_by(auction_id=auction_id).order_by(Bid.amount.desc()).all()
    return render_template('admin/bids.html', auction=auction, bids=bids)

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
@admin_required
def admin_settings():
    if request.method == 'POST':
        # Update settings
        setting_keys = [
            'default_whitelisted_domains',
            'smtp_enabled', 'smtp_host', 'smtp_port', 'smtp_username',
            'smtp_password', 'smtp_from_email', 'smtp_from_name', 'smtp_use_tls'
        ]
        
        for key in setting_keys:
            setting = Settings.query.filter_by(key=key).first()
            if not setting:
                setting = Settings(key=key)
                db.session.add(setting)
            
            # Handle checkbox fields
            if key in ['smtp_enabled', 'smtp_use_tls']:
                setting.value = 'true' if request.form.get(key) == 'on' else 'false'
            else:
                setting.value = request.form.get(key, '')
        
        db.session.commit()
        # Persist settings to config file for easy manual edits
        write_config_file({s.key: s.value for s in Settings.query.all()})
        
        db.session.commit()
        flash('Settings saved!', 'success')
    
    settings = {s.key: s.value for s in Settings.query.all()}
    return render_template('admin/settings.html', settings=settings)

@app.route('/admin/settings/test-email', methods=['POST'])
@admin_required
def admin_test_email():
    test_email = request.form.get('test_email', '').strip()
    if not test_email:
        flash('Please enter a test email address', 'error')
        return redirect(url_for('admin_settings'))
    
    success, message = send_email(
        test_email,
        'Test Email - Zolta',
        '<h1>Test Email</h1><p>If you received this email, your SMTP settings are configured correctly!</p>',
        'Test Email\n\nIf you received this email, your SMTP settings are configured correctly!'
    )
    
    if success:
        flash(f'Test email sent to {test_email}!', 'success')
    else:
        flash(f'Failed to send test email: {message}', 'error')
    
    return redirect(url_for('admin_settings'))

# Initialize database and create default admin
def ensure_sqlite_columns():
    """Best-effort lightweight migrations for SQLite when using create_all()."""
    try:
        db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if not db_uri.startswith('sqlite:////'):
            return
        db_path = db_uri.replace('sqlite:////', '')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(auction)")
        cols = {row[1] for row in cur.fetchall()}
        # Add missing columns (SQLite supports ADD COLUMN)
        if 'notify_winner' not in cols:
            cur.execute("ALTER TABLE auction ADD COLUMN notify_winner BOOLEAN DEFAULT 1")
        if 'ending_soon_notified_at' not in cols:
            cur.execute("ALTER TABLE auction ADD COLUMN ending_soon_notified_at DATETIME")
        if 'ended_notified_at' not in cols:
            cur.execute("ALTER TABLE auction ADD COLUMN ended_notified_at DATETIME")
        if 'language' not in cols:
            cur.execute("ALTER TABLE auction ADD COLUMN language VARCHAR(2) DEFAULT 'nl'")
        
        # Ensure Admin.role exists
        cur.execute("PRAGMA table_info(admin)")
        admin_cols = {row[1] for row in cur.fetchall()}
        if 'role' not in admin_cols:
            cur.execute("ALTER TABLE admin ADD COLUMN role VARCHAR(40) DEFAULT 'admin'")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"SQLite migration skipped/failed: {e}")


def ensure_db_schema():
    """Lightweight SQLite schema migration for existing installs.
    Flask-SQLAlchemy's create_all() won't add new columns to existing tables.
    """
    try:
        engine = db.get_engine()
    except Exception:
        engine = db.engine
    with engine.connect() as conn:
        # Auction table columns
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(auction)").fetchall()]
        def add_col(name, ddl):
            if name not in cols:
                conn.exec_driver_sql(f"ALTER TABLE auction ADD COLUMN {ddl}")
        add_col("notify_winner", "notify_winner BOOLEAN DEFAULT 1")
        add_col("ending_soon_notified_at", "ending_soon_notified_at DATETIME")
        add_col("ended_notified_at", "ended_notified_at DATETIME")
        add_col("language", "language VARCHAR(2) DEFAULT 'nl'")
        add_col("winner_instructions", "winner_instructions TEXT")

def init_db():
    with app.app_context():
        os.makedirs('/app/instance', exist_ok=True)
        # --- lightweight SQLite migrations (no Alembic) ---
        try:
            # Ensure admin.role column exists (for permission-based users)
            db_path = '/app/instance/auctions.db'
            try:
                from sqlalchemy import text as _sql_text
                # check columns
                cols = db.session.execute(_sql_text("PRAGMA table_info(admin)")).fetchall()
                col_names = [c[1] for c in cols] if cols else []
                if 'role' not in col_names:
                    db.session.execute(_sql_text("ALTER TABLE admin ADD COLUMN role VARCHAR(32) DEFAULT 'admin'"))
                    db.session.commit()
            except Exception:
                db.session.rollback()
        except Exception:
            pass

        db.create_all()
        ensure_db_schema()
        sync_settings_from_config()
        
        # Ensure instance folder exists for persistent SQLite database
        
        # Create uploads folder
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

        # Default site language
        if not Settings.query.filter_by(key='language').first():
            db.session.add(Settings(key='language', value='nl'))
            db.session.commit()

        
        # Create default admin if none exists
        if not Admin.query.first():
            default_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            admin = Admin(
                username='admin',
                password_hash=generate_password_hash(default_password)
            )
            db.session.add(admin)
            db.session.commit()
            print(f"Created default admin user: admin / {default_password}")


# Auto-init DB and start notification scheduler when running under a WSGI server (gunicorn)
if os.environ.get('AUTO_INIT', 'true').lower() == 'true':
    try:
        init_db()
    except Exception as e:
        print(f"DB init failed: {e}")
    try:
        start_notification_scheduler()
    except Exception as e:
        print(f"Scheduler start failed: {e}")



@app.route('/admin/users')
@admin_required
def admin_users():
    users = Admin.query.order_by(Admin.username.asc()).all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/new', methods=['GET','POST'])
@admin_required
def admin_new_user():
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        role = request.form.get('role','auction_creator').strip()
        if not username or not password:
            flash('Username and password are required', 'error')
            return redirect(url_for('admin_new_user'))
        if Admin.query.filter_by(username=username).first():
            flash('User already exists', 'error')
            return redirect(url_for('admin_new_user'))
        u = Admin(username=username, password_hash=generate_password_hash(password), role=role)
        db.session.add(u)
        db.session.commit()
        write_config_file({s.key: s.value for s in Settings.query.all()})
        flash('User created', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=None)

@app.route('/admin/users/<int:user_id>/edit', methods=['GET','POST'])
@admin_required
def admin_edit_user(user_id):
    user = Admin.query.get_or_404(user_id)
    if request.method == 'POST':
        user.role = request.form.get('role', user.role)
        new_pw = request.form.get('password','').strip()
        if new_pw:
            user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash('User updated', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', user=user)

@app.post('/admin/users/<int:user_id>/delete')
@admin_required
def admin_delete_user(user_id):
    user = Admin.query.get_or_404(user_id)
    # prevent deleting yourself
    if user.username == session.get('admin_username'):
        flash('You cannot delete your own account', 'error')
        return redirect(url_for('admin_users'))
    db.session.delete(user)
    db.session.commit()
    flash('User deleted', 'success')
    return redirect(url_for('admin_users'))
if __name__ == '__main__':
    def _graceful_exit(signum, frame):
        print('Shutting down...')
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_exit)
    signal.signal(signal.SIGINT, _graceful_exit)

    init_db()
    start_notification_scheduler()
    app.run(
    host="0.0.0.0",
    port=5000,
    debug=os.environ.get("DEBUG", "false").lower() == "true",
    use_reloader=False,
    threaded=True,
)