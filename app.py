from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import os
import signal
import sys
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/instance/auctions.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

db = SQLAlchemy(app)

# Database Models
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
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

def get_site_language():
    settings = get_all_settings()
    lang = (settings.get('language') or 'en').lower().strip()
    return 'nl' if lang == 'nl' else 'en'

TRANSLATIONS = {
    'en': {
        'auctions': 'Auctions',
        'admin_panel': 'Admin Panel',
        'admin': 'Admin',
        'logout': 'Logout',
        'back_to_auctions': 'Back to Auctions',
        'description': 'Description',
        'bid_history': 'Bid History (Top 10)',
        'live_now': 'Live Now',
        'starting_soon': 'Starting Soon',
        'ended': 'Ended',
        'final_price': 'Final Price',
        'current_bid': 'Current Bid',
        'starting_price': 'Starting Price',
        'place_your_bid': 'Place Your Bid',
        'your_name': 'Your Name',
        'email_address': 'Email Address',
        'bid_amount': 'Bid Amount (€)',
        'place_bid': 'Place Bid',
        'verification_email_sent': 'Check your email to confirm your bid. After confirming, you won\'t need to verify again for 7 days.',
        'homepage_title': 'Zolta Auctions',
        'live_auctions': 'Live Auctions',
        'upcoming_auctions': 'Upcoming Auctions',
        'recently_ended': 'Recently Ended',
        'no_auctions_yet': 'No Auctions Yet',
        'check_back_soon': 'Check back soon for upcoming auctions!',
    },
    'nl': {
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
        'final_price': 'Eindprijs',
        'current_bid': 'Huidig bod',
        'starting_price': 'Startprijs',
        'place_your_bid': 'Plaats je bod',
        'your_name': 'Je naam',
        'email_address': 'E-mailadres',
        'bid_amount': 'Bedrag (€)',
        'place_bid': 'Bod plaatsen',
        'verification_email_sent': 'Check je e-mail om je bod te bevestigen. Daarna hoef je 7 dagen niet opnieuw te verifiëren.',
        'homepage_title': 'Zolta Veilingen',
        'live_auctions': '🔥 Live veilingen',
        'upcoming_auctions': '⏰ Aankomende veilingen',
        'recently_ended': '✅ Recent afgelopen',
        'no_auctions_yet': 'Nog geen veilingen',
        'check_back_soon': 'Kom later terug voor nieuwe veilingen!',
    }
}

@app.context_processor
def inject_helpers():
    def t(key):
        lang = get_site_language()
        return TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)
    def now():
        return datetime.now()
    return dict(t=t, site_lang=get_site_language(), now=now)
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
    bids = Bid.query.filter_by(auction_id=auction_id).order_by(Bid.amount.desc()).limit(10).all()
    
    # Get saved user info from cookies
    saved_name = request.cookies.get('bidder_name', '')
    saved_email = request.cookies.get('bidder_email', '')
    
    return render_template('auction_detail.html', 
                         auction=auction, 
                         bids=bids,
                         saved_name=saved_name,
                         saved_email=saved_email)

@app.route('/api/auction/<int:auction_id>/bid', methods=['POST'])
def place_bid(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    
    if not auction.is_running:
        return jsonify({'success': False, 'error': 'This auction is not currently accepting bids.'}), 400
    
    data = request.json
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    amount = data.get('amount')
    
    # Validation
    if not name or not email or not amount:
        return jsonify({'success': False, 'error': 'Name, email, and bid amount are required.'}), 400
    
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid bid amount.'}), 400
    
    # Email domain validation
    if auction.whitelisted_domains:
        if not validate_email_domain(email, auction.whitelisted_domains):
            allowed = auction.whitelisted_domains.replace(',', ', ')
            return jsonify({'success': False, 'error': f'Email must be from one of these domains: {allowed}'}), 400
    
    # Bid amount validation
    current_price = auction.current_price
    min_bid = current_price + auction.min_bid_increment
    
    if amount < min_bid:
        return jsonify({'success': False, 'error': f'Minimum bid is €{min_bid:.2f}'}), 400
    
    if auction.max_bid_increment:
        max_bid = current_price + auction.max_bid_increment
        if amount > max_bid:
            return jsonify({'success': False, 'error': f'Maximum bid is €{max_bid:.2f}'}), 400
    
    if auction.max_price and amount > auction.max_price:
        return jsonify({'success': False, 'error': f'Bid cannot exceed €{auction.max_price:.2f}'}), 400
    
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
            # Create a verification token and email the bidder
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

            html_body = f"""
                <h2>Confirm your bid</h2>
                <p>Hi {name},</p>
                <p>Please confirm your bid for <strong>{auction.title}</strong>:</p>
                <ul>
                    <li>Bid amount: <strong>€{amount:.2f}</strong></li>
                </ul>
                <p><a href=\"{verify_url}\">Click here to confirm your bid</a></p>
                <p>This link expires in 30 minutes.</p>
            """

            text_body = f"""Confirm your bid

Auction: {auction.title}
Bid amount: €{amount:.2f}

Confirm here: {verify_url}

This link expires in 30 minutes.
"""

            success, message = send_email(
                email,
                f"Confirm your bid - {auction.title}",
                html_body,
                text_body
            )

            if not success:
                # If SMTP isn't enabled/configured, we should not accept the bid silently
                return jsonify({'success': False, 'error': f'Email confirmation is required, but sending email failed: {message}'}), 400

            return jsonify({
                'success': True,
                'verification_required': True,
                'message': TRANSLATIONS.get(get_site_language(), TRANSLATIONS['en']).get('verification_email_sent')
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
    
    response = jsonify({
        'success': True, 
        'message': 'Bid placed successfully!',
        'new_price': amount,
        'bid_id': bid.id
    })
    
    # Save to cookies
    response.set_cookie('bidder_name', name, max_age=30*24*60*60)  # 30 days
    response.set_cookie('bidder_email', email, max_age=30*24*60*60)
    
    return response


@app.route('/verify/<token>')
def verify_bid(token):
    verification = BidVerification.query.filter_by(token=token).first_or_404()

    if verification.is_used:
        flash('This confirmation link has already been used.', 'error')
        return redirect(url_for('auction_detail', auction_id=verification.auction_id))

    if verification.is_expired:
        flash('This confirmation link has expired. Please place your bid again.', 'error')
        return redirect(url_for('auction_detail', auction_id=verification.auction_id))

    auction = Auction.query.get_or_404(verification.auction_id)

    if not auction.is_running:
        flash('This auction is no longer accepting bids.', 'error')
        return redirect(url_for('auction_detail', auction_id=auction.id))

    # Re-validate bid amount at confirmation time
    current_price = auction.current_price
    min_bid = current_price + auction.min_bid_increment
    amount = float(verification.amount)

    if amount < min_bid:
        flash(f'Your bid is no longer high enough. Minimum bid is now €{min_bid:.2f}. Please bid again.', 'error')
        return redirect(url_for('auction_detail', auction_id=auction.id))

    if auction.max_bid_increment:
        max_bid = current_price + auction.max_bid_increment
        if amount > max_bid:
            flash(f'Your bid is now too high. Maximum bid is €{max_bid:.2f}. Please bid again.', 'error')
            return redirect(url_for('auction_detail', auction_id=auction.id))

    if auction.max_price and amount > auction.max_price:
        flash(f'Bid cannot exceed €{auction.max_price:.2f}. Please bid again.', 'error')
        return redirect(url_for('auction_detail', auction_id=auction.id))

    bid = Bid(
        auction_id=auction.id,
        bidder_name=verification.bidder_name,
        bidder_email=verification.bidder_email,
        amount=amount
    )
    db.session.add(bid)

    verification.used_at = datetime.now()
    db.session.commit()

    # Set cookies: remember bidder + remember verification for 7 days for this email
    resp = redirect(url_for('auction_detail', auction_id=auction.id))
    resp.set_cookie('bidder_name', verification.bidder_name, max_age=30*24*60*60)
    resp.set_cookie('bidder_email', verification.bidder_email, max_age=30*24*60*60)

    verified_until = int((datetime.now() + timedelta(days=7)).timestamp())
    resp.set_cookie('verified_email', verification.bidder_email, max_age=7*24*60*60)
    resp.set_cookie('verified_until', str(verified_until), max_age=7*24*60*60)

    flash('Bid confirmed and placed successfully!', 'success')
    return resp

@app.route('/api/auction/<int:auction_id>/status')
def auction_status(auction_id):
    auction = Auction.query.get_or_404(auction_id)
    highest_bid = auction.highest_bidder
    
    return jsonify({
        'current_price': auction.current_price,
        'highest_bidder': highest_bid.bidder_name if highest_bid else None,
        'bid_count': len(auction.bids),
        'status': auction.status,
        'end_date': auction.end_date.isoformat()
    })

# Admin Routes
@app.route('/admin')
@admin_required
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
            return redirect(url_for('admin_dashboard'))
        
        flash('Invalid credentials', 'error')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('index'))

@app.route('/admin/auction/new', methods=['GET', 'POST'])
@admin_required
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
            is_active=True
        )
        
        db.session.add(auction)
        db.session.commit()
        
        flash('Auction created successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/auction_form.html', auction=None)

@app.route('/admin/auction/<int:auction_id>/edit', methods=['GET', 'POST'])
@admin_required
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
        auction.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        
        flash('Auction updated successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/auction_form.html', auction=auction)

@app.route('/admin/auction/<int:auction_id>/delete', methods=['POST'])
@admin_required
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
def admin_settings():
    if request.method == 'POST':
        # Update settings
        setting_keys = [
            'site_title', 'site_description', 'language', 'default_whitelisted_domains',
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
def init_db():
    with app.app_context():
        db.create_all()
        
        # Ensure instance folder exists for persistent SQLite database
        os.makedirs('/app/instance', exist_ok=True)
        
        # Create uploads folder
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
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

if __name__ == '__main__':
    def _graceful_exit(signum, frame):
        print('Shutting down...')
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_exit)
    signal.signal(signal.SIGINT, _graceful_exit)

    init_db()
    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('DEBUG', 'false', use_reloader=False, threaded=True).lower() == 'true')
