// Auction System Frontend JavaScript

document.addEventListener('DOMContentLoaded', function() {
    // Initialize countdown timers
    initCountdowns();
    
    // Initialize bid forms
    initBidForms();
    
    // Auto-refresh auction status
    initAutoRefresh();
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
        element.innerHTML = '<span class="countdown-ended">Auction Ended</span>';
        return;
    }
    
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((diff % (1000 * 60)) / 1000);
    
    element.innerHTML = `
        <div class="countdown-item">
            <span class="countdown-value">${days}</span>
            <span class="countdown-label">Days</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${hours.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Hours</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${minutes.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Minutes</span>
        </div>
        <div class="countdown-item">
            <span class="countdown-value">${seconds.toString().padStart(2, '0')}</span>
            <span class="countdown-label">Seconds</span>
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
        submitBtn.textContent = 'Submitting...';
        
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
            showMessage('error', 'An error occurred. Please try again.');
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
            hintEl.textContent = `Minimum bid: €${newMin}`;
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
                    bidForm.innerHTML = '<div class="alert alert-info">This auction has ended.</div>';
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
    return confirm(message || 'Are you sure you want to delete this item?');
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
