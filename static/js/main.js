// Auction System Frontend JavaScript


function setCookie(name, value, days) {
    const expires = new Date(Date.now() + (days * 864e5)).toUTCString();
    document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Lax`;
}
function getCookie(name) {
    return document.cookie.split('; ').reduce((r, v) => {
        const parts = v.split('=');
        return parts[0] === name ? decodeURIComponent(parts.slice(1).join('=')) : r
    }, '');
}



document.addEventListener('DOMContentLoaded', function() {
    // Initialize countdown timers
    initCountdowns();
    
    // Initialize bid forms
    initBidForms();
    
    // Auto-refresh auction status
    initAutoRefresh();
    initLiveBidRefresh();
    // Smooth page transitions (progressive enhancement)
    initPageTransitions();


    // PWA: register service worker
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js').catch(function(err) {
            console.warn('ServiceWorker registration failed:', err);
        });
    }
});

// Countdown Timer
function initCountdowns() {
    const countdowns = document.querySelectorAll('[data-countdown]');
    countdowns.forEach(el => {
        const endDate = new Date(el.dataset.countdown);
        updateCountdown(el, endDate);
        setInterval(() => updateCountdown(el, endDate), 1000);
    });
}

function updateCountdown(element, endDate) {
    const now = new Date();
    const diff = endDate - now;
    
    if (diff <= 0) {
        element.innerHTML = '<span class="countdown-ended">Veiling afgelopen</span>';
        return;
    }
    
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);
    
    element.innerHTML = `
        <div class="countdown-item">
            <span class="countdown-value">${days}</span>
            <span class="countdown-label">Dagen</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${hours.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Uur</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${minutes.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Minuten</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${seconds.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Seconden</span>
        </div>
    `;
}

// Bid Form Handling
function initBidForms() {
    const bidForm = document.getElementById('bid-form');
    if (!bidForm) return;
    
    bidForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const submitBtn = bidForm.querySelector('button[type="submit"]');
        const originalText = submitBtn.textContent;
        submitBtn.disabled = true;
        submitBtn.textContent = 'Bezig met verzenden...';
        
        const auctionId = bidForm.dataset.auctionId;

        const formData = {
            name: bidForm.querySelector('[name="name"]').value,
            email: bidForm.querySelector('[name="email"]').value,
            amount: parseFloat(bidForm.querySelector('[name="amount"]').value)
        };
        
        try {
            const response = await fetch(`/api/auction/${auctionId}/bid`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });
            
            const data = await response.json();
            
            if (data.success) {
                showMessage('success', data.message);
                if (data.verification_required) {
                    // Bid will be placed after email confirmation
                    return;
                }
                // Update current price display
                updatePriceDisplay(data.new_price);
                // Refresh bid list
                refreshBidList(auctionId);
                // Update min bid
                updateMinBid(data.new_price);
            } else {
                showMessage('error', data.error);
            }
        } catch (error) {
            showMessage('error', 'Er ging iets mis. Probeer het opnieuw.');
            console.error(error);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
        }
    });
    
    // Calculate suggested bid on amount focus
    const amountInput = bidForm.querySelector('[name="amount"]');
    if (amountInput) {
        amountInput.addEventListener('focus', function() {
            if (!this.value) {
                this.value = this.min;
            }
        });
    }
}

function showMessage(type, message) {
    const container = document.getElementById('message-container');
    if (!container) return;
    
    const alert = document.createElement('div');
    alert.className = `alert alert-${type}`;
    alert.textContent = message;
    
    container.innerHTML = '';
    container.appendChild(alert);
    
    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        alert.remove();
    }, 5000);
}

function updatePriceDisplay(newPrice) {
    const priceEl = document.getElementById('current-price');
    if (priceEl) {
        priceEl.textContent = `€${newPrice.toFixed(2)}`;
        priceEl.classList.add('price-updated');
        setTimeout(() => priceEl.classList.remove('price-updated'), 500);
    }
}

function updateMinBid(currentPrice) {
    const minBidInput = document.querySelector('[name="amount"]');
    if (minBidInput) {
        const minIncrement = parseFloat(minBidInput.dataset.minIncrement) || 1;
        const newMin = (currentPrice + minIncrement).toFixed(2);
        minBidInput.min = newMin;
        minBidInput.value = newMin;
        
        const hintEl = minBidInput.parentElement.querySelector('.form-hint');
        if (hintEl) {
            hintEl.textContent = `Minimum bod: €${newMin}`;
        }
    }
}

async function refreshBidList(auctionId) {
    try {
        const response = await fetch(`/api/auction/${auctionId}/status`);
        const data = await response.json();
        
        // Update bid count
        const bidCountEl = document.getElementById('bid-count');
        if (bidCountEl) {
            bidCountEl.textContent = data.bid_count;
        }
        
        // Reload page to show updated bid list (simple approach)
        // For a more sophisticated approach, you could fetch and render bids via AJAX
        window.location.reload();
    } catch (error) {
        console.error('Failed to refresh bid list:', error);
    }
}

// Auto-refresh auction status
function initAutoRefresh() {
    // Index page: reload when an upcoming auction should go live
    const indexMarker = document.querySelector('[data-page="index"]');
    if (indexMarker) {
        const upcomingCards = document.querySelectorAll('[data-auction-start]');
        if (upcomingCards.length) {
            setInterval(() => {
                const now = new Date();
                for (const card of upcomingCards) {
                    const start = new Date(card.dataset.auctionStart);
                    if (!isNaN(start) && now >= start) {
                        window.location.reload();
                        return;
                    }
                }
            }, 30000);
        }
        return;
    }

    const auctionInfo = document.querySelector('[data-auction-id]');
    if (!auctionInfo) return;
    
    const auctionId = auctionInfo.dataset.auctionId;
    
    // Refresh every 30 seconds
    setInterval(async () => {
        try {
            const response = await fetch(`/api/auction/${auctionId}/status`);
            const data = await response.json();
            
            // Update price if changed
            const priceEl = document.getElementById('current-price');
            if (priceEl) {
                const displayPrice = `€${data.current_price.toFixed(2)}`;
                if (priceEl.textContent !== displayPrice) {
                    priceEl.textContent = displayPrice;
                    priceEl.classList.add('price-updated');
                    setTimeout(() => priceEl.classList.remove('price-updated'), 500);
                    updateMinBid(data.current_price);
                }
            }
            
            // Update bid count
            const bidCountEl = document.getElementById('bid-count');
            if (bidCountEl) {
                bidCountEl.textContent = data.bid_count;
            }
            
            // Check if auction ended
            if (data.status === 'ended') {
                const bidForm = document.getElementById('bid-form');
                if (bidForm) {
                    bidForm.innerHTML = '<div class="alert alert-info">Deze veiling is afgelopen.</div>';
                    // stop polling? (auto)
                }
            }
        } catch (error) {
            console.error('Failed to refresh auction status:', error);
        }
    }, 30000);
}

// Format currency
function formatCurrency(amount) {
    return new Intl.NumberFormat('nl-NL', {
        style: 'currency',
        currency: 'EUR'
    }).format(amount);
}

// Format date
function formatDate(dateString) {
    return new Date(dateString).toLocaleString('nl-NL', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Confirm delete
function confirmDelete(message) {
    return confirm(message || 'Weet je zeker dat je dit wilt verwijderen?');
}

// Image preview for file upload
document.querySelectorAll('input[type="file"][accept*="image"]').forEach(input => {
    input.addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        const preview = document.getElementById('image-preview');
        if (preview) {
            const reader = new FileReader();
            reader.onload = function(e) {
                preview.src = e.target.result;
                preview.style.display = 'block';
            };
            reader.readAsDataURL(file);
        }
    });
});


function initPageTransitions(){
    // Prefer the View Transitions API if available
    const supportsVT = typeof document.startViewTransition === 'function';

    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href');
        if (!href) return;

        // Only intercept same-origin navigations (no new tab, no anchors)
        const isExternal = href.startsWith('http') || href.startsWith('mailto:') || href.startsWith('tel:');
        const isHashOnly = href.startsWith('#');
        if (isExternal || isHashOnly) return;

        a.addEventListener('click', (e) => {
            // respect modifiers / new tab
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target === '_blank') return;
            e.preventDefault();

            const go = () => { window.location.href = href; };

            if (supportsVT) {
                document.startViewTransition(go);
            } else {
                document.body.classList.add('page-fade-out');
                setTimeout(go, 140);
            }
        });
    });

    // fade-in on load for non-VT browsers
    if (!supportsVT) {
        document.body.classList.add('page-fade-in');
        requestAnimationFrame(() => document.body.classList.add('page-fade-in-active'));
        setTimeout(() => {
            document.body.classList.remove('page-fade-in');
            document.body.classList.remove('page-fade-in-active');
        }, 250);
    }
}


function initLiveBidRefresh() {
    const detail = document.querySelector('.auction-detail-grid[data-auction-id]');
    if (!detail) return;
    const auctionId = detail.dataset.auctionId;

    const applySnapshot = (data) => {
        if (!data) return;
        updatePriceDisplay(data.current_price);

        const bidCountEl = document.getElementById('bid-count');
        if (bidCountEl) bidCountEl.textContent = data.bid_count;

        const list = document.getElementById('recent-bids');
        if (list && Array.isArray(data.bids)) {
            const header = `
                <div class="bid-header" aria-hidden="true">
                    <div>Naam</div>
                    <div>Datum</div>
                    <div class="text-right">Bod</div>
                </div>`;

            if (data.bids.length === 0) {
                list.innerHTML = header + '<div class="empty-bids">Nog geen biedingen.</div>';
            } else {
                list.innerHTML = header + data.bids.map((b, idx) => {
                    const dt = new Date(b.created_at);
                    const ts = isNaN(dt) ? '' : dt.toLocaleString('nl-NL');
                    const winning = idx === 0 ? ' winning' : '';
                    return `
                        <div class="bid-item${winning}">
                            <div class="bid-name">${escapeHtml(b.name)}</div>
                            <div class="bid-time">${escapeHtml(ts)}</div>
                            <div class="bid-amount">€${Number(b.amount).toFixed(2)}</div>
                        </div>`;
                }).join('');
            }
        }

        if (data.status === 'ended' && data.winner_name && data.notify_winner && !window.__zoltaWinnerShown) {
            window.__zoltaWinnerShown = true;
            showWinnerOverlay(data.winner_name, data.winner_amount);
        }
    };

    const fetchState = async () => {
        try {
            const res = await fetch(`/api/auction/${auctionId}/state`, { cache: 'no-store' });
            if (!res.ok) return;
            const data = await res.json();
            applySnapshot({
                status: data.status,
                current_price: data.current_price,
                bid_count: data.bid_count,
                winner_name: data.highest_bidder_name,
                winner_amount: data.highest_bid_amount,
                notify_winner: data.notify_winner,
                bids: data.bids
            });
        } catch (e) { /* ignore */ }
    };

    // Always start immediately + keep polling (proxy-safe)
    fetchState();
    setInterval(fetchState, 2000);
}

function showWinnerOverlay(name, amount) {
    const overlay = document.getElementById('winner-overlay');
    if (!overlay) return;
    const nm = overlay.querySelector('[data-winner-name]');
    const am = overlay.querySelector('[data-winner-amount]');
    if (nm) nm.textContent = name || '';
    if (am) am.textContent = (amount != null) ? `€${Number(amount).toFixed(2)}` : '';
    overlay.classList.add('show');
    setTimeout(() => overlay.classList.remove('show'), 5000);
}


function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s]));
}
