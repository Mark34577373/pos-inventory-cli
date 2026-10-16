"""Bounded, process-local fictional checkout. This module never accesses SQLite."""
import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager
from decimal import Decimal
from threading import RLock
from types import MappingProxyType

from services.discounts import discount_amount
from services.money import calculate_tax_cents, parse_cash

CATALOG = tuple(MappingProxyType(dict(id=key, name=name, price_cents=cents,
                                    category=category, quantity=stock))
                for key, name, cents, category, stock in (
    ('notebook', 'Demo Notebook', 425, 'Stationery', 20),
    ('pen', 'Demo Pen', 125, 'Stationery', 20),
    ('planner', 'Demo Weekly Planner With Refillable Stationery Pages', 1299, 'Stationery', 12),
    ('cable', 'Demo USB Cable', 750, 'Accessories', 20),
    ('stand', 'Demo Laptop Stand', 2499, 'Accessories', 20),
    ('case', 'Demo Travel Case', 1599, 'Accessories', 15),
    ('lamp', 'Demo Desk Lamp', 2499, 'Lighting', 3),
    ('keyboard', 'Demo Keyboard', 2999, 'Accessories', 0),
))
PRODUCTS = MappingProxyType({product['id']: product for product in CATALOG})
TAX_RATE = Decimal('0.0825')
MAX_LINES, MAX_QUANTITY, MAX_TOTAL_CENTS = 6, 20, 100_000
SESSION_TTL, MAX_SESSIONS, MAX_BUCKETS = 1800, 256, 1024


def quote(cart, discount):
    if len(cart) > MAX_LINES:
        raise ValueError('A demo cart supports at most six different products.')
    lines = []
    for key, quantity in cart.items():
        product = PRODUCTS.get(key)
        if product is None or type(quantity) is not int or not 1 <= quantity <= min(MAX_QUANTITY, product['quantity']):
            raise ValueError('Choose an available fictional product and a valid quantity.')
        lines.append(dict(product, quantity=quantity, available_quantity=product['quantity'],
                          line_total_cents=product['price_cents'] * quantity))
    subtotal = sum(line['line_total_cents'] for line in lines)
    if subtotal > MAX_TOTAL_CENTS:
        raise ValueError('The demo cart cannot exceed $1,000.00 before discounts and tax.')
    discounted = discount_amount(lines, discount['kind'], discount['value'])
    tax = calculate_tax_cents(subtotal - discounted, TAX_RATE)
    return dict(lines=lines, subtotal_cents=subtotal, discount_cents=discounted,
                tax_cents=tax, total_cents=subtotal - discounted + tax)


def empty_state(now):
    return dict(cart={}, discount=dict(kind='none', value=''), nonce=secrets.token_urlsafe(24),
                receipt=None, completed=None, expires=now + SESSION_TTL)


class DemoRateLimit(ValueError):
    pass


class DemoCapacity(ValueError):
    pass


class DemoMemory:
    """One Gunicorn worker / two threads; fail closed at resource capacity."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, RLock()
        self.states, self.buckets = {}, {}

    def consume(self, key, limit, window, now):
        if key not in self.buckets:
            if len(self.buckets) >= MAX_BUCKETS:
                raise DemoCapacity('The demo is busy. Please try again shortly.')
            self.buckets[key] = [0, now + window]
        bucket = self.buckets[key]
        if bucket[0] >= limit:
            raise DemoRateLimit('Too many demo requests. Please try again later.')
        bucket[0] += 1

    @contextmanager
    def visitor(self, token, address, secret, checkout=False):
        with self.lock:
            now = self.clock()
            self.states = {key: value for key, value in self.states.items() if value['expires'] > now}
            self.buckets = {key: value for key, value in self.buckets.items() if value[1] > now}
            ip = hmac.new(str(secret).encode(), address.encode(), hashlib.sha256).hexdigest()
            self.consume(('requests', ip), 300, 60, now)
            if not isinstance(token, str) or token not in self.states:
                self.consume(('new', ip), 20, 600, now)
                if len(self.states) >= MAX_SESSIONS:
                    raise DemoCapacity('The demo is busy. Please try again shortly.')
                token = secrets.token_urlsafe(32)
                self.states[token] = empty_state(now)
            state = self.states[token]
            if checkout:
                self.consume(('checkout', token), 10, 600, now)
                self.consume(('checkout-ip', ip), 60, 600, now)
            state['expires'] = now + SESSION_TTL
            yield token, state


def change_cart(state, key, quantity):
    if key not in PRODUCTS or type(quantity) is not int or not 0 <= quantity <= MAX_QUANTITY:
        raise ValueError('Choose a valid fictional product and whole quantity from 0 to 20.')
    cart = dict(state['cart'])
    if quantity:
        cart[key] = quantity
    else:
        cart.pop(key, None)
    # Revalidate discounts on every edit. Failed edits leave the old cart intact.
    quote(cart, state['discount'])
    state.update(cart=cart, nonce=secrets.token_urlsafe(24))


def complete(state, nonce, method, cash, timestamp):
    if method not in ('Cash', 'Card') or not isinstance(nonce, str) or len(nonce) > 100 or not nonce.isascii():
        raise ValueError('Choose simulated cash or simulated card.')
    received = None
    if method == 'Cash':
        if not cash or len(cash) > 20:
            raise ValueError('Enter a fictional cash amount.')
        received = parse_cash(cash)
        if received > MAX_TOTAL_CENTS * 2:
            raise ValueError('Fictional cash received cannot exceed $2,000.00.')
    elif cash:
        raise ValueError('Cash received must be empty for a simulated card payment.')
    fingerprint = (method, received)
    completed = state['completed']
    if completed and hmac.compare_digest(nonce, completed['nonce']):
        if fingerprint != completed['fingerprint']:
            raise ValueError('That demo checkout was already completed with different payment details.')
        return state['receipt']
    if not hmac.compare_digest(nonce, state['nonce']):
        raise ValueError('This demo sale expired or changed. Review your cart and try again.')
    result = quote(state['cart'], state['discount'])
    if not result['lines']:
        raise ValueError('Add a fictional product before completing a sale.')
    if received is not None and received < result['total_cents']:
        raise ValueError('Fictional cash received is less than the total due.')
    receipt = dict(result, identifier='DEMO-' + secrets.token_hex(6).upper(),
                   timestamp=timestamp, payment_method=method, cash_received_cents=received,
                   change_cents=None if received is None else received - result['total_cents'])
    state.update(cart={}, discount=dict(kind='none', value=''), receipt=receipt,
                 completed=dict(nonce=nonce, fingerprint=fingerprint), nonce=secrets.token_urlsafe(24))
    return receipt
