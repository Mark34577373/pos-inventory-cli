"""Public simulation routes, intentionally independent of business persistence."""
import secrets
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import BUSINESS_TIMEZONE, business_now
from services.interactive_demo import (
    CATALOG,
    DemoCapacity,
    DemoMemory,
    DemoRateLimit,
    change_cart,
    complete,
    empty_state,
    quote,
)
from services.money import format_cents, parse_cash

bp = Blueprint('interactive_demo', __name__, url_prefix='/demo')
memory = DemoMemory()


def visitor(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            previous = session.get('interactive_demo_token')
            with memory.visitor(previous, request.remote_addr or 'unknown',
                                current_app.secret_key, checkout=request.endpoint == 'interactive_demo.checkout') as (token, state):
                if previous and previous != token:
                    flash('Your temporary demo expired or restarted. A fresh fictional cart is ready.', 'success')
                session['interactive_demo_token'] = token
                g.demo_state = state
                return function(*args, **kwargs)
        except DemoRateLimit as error:
            return render_template('error.html', message=str(error), status_code=429), 429, {'Retry-After': '600'}
        except DemoCapacity as error:
            return render_template('error.html', message=str(error), status_code=503), 503, {'Retry-After': '60'}
    return wrapped


def fields(allowed):
    if set(request.form) - set(allowed) - {'csrf_token'}:
        abort(400, description='Unsupported demo fields. Prices and totals are calculated by the server.')


def number(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 2:
        raise ValueError('Enter a whole quantity from 0 to 20.')
    return int(value)


def page(error=None, status=200):
    query = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    if len(query) > 100 or category not in ('', 'Stationery', 'Accessories', 'Lighting'):
        abort(400, description='Choose a valid demo search or category.')
    products = [p for p in CATALOG if (not category or p['category'] == category)
                and query.casefold() in (p['name'] + ' ' + p['id']).casefold()]
    return render_template('demo_sale.html', products=products, search_term=query,
                           categories=('Stationery', 'Accessories', 'Lighting'), selected_category=category,
                           quote=quote(g.demo_state['cart'], g.demo_state['discount']),
                           state=g.demo_state, error=error), status


@bp.get('/pos')
@visitor
def catalog():
    return page()


@bp.post('/cart')
@visitor
def cart():
    fields({'product_id', 'quantity', 'action'})
    try:
        key, action = request.form.get('product_id', ''), request.form.get('action', '')
        quantity = number(request.form.get('quantity', '1'))
        current = g.demo_state['cart'].get(key, 0)
        if action == 'add':
            quantity += current
        elif action == 'plus':
            quantity = current + 1
        elif action == 'minus':
            quantity = max(0, current - 1)
        elif action == 'remove':
            quantity = 0
        elif action != 'set':
            raise ValueError('Choose a valid cart action.')
        change_cart(g.demo_state, key, quantity)
    except ValueError as error:
        return page(str(error), 400)
    return redirect(url_for('.catalog'), code=303)


@bp.post('/discount')
@visitor
def discount():
    fields({'kind', 'value'})
    try:
        proposed = dict(kind=request.form.get('kind', 'none'), value=request.form.get('value', ''))
        quote(g.demo_state['cart'], proposed)
        g.demo_state.update(discount=proposed, nonce=secrets.token_urlsafe(24))
    except ValueError as error:
        return page(str(error), 400)
    return redirect(url_for('.catalog'), code=303)


@bp.post('/cash-preview')
@visitor
def cash_preview():
    fields({'cash_received'})
    try:
        value = request.form.get('cash_received', '')
        if not value or len(value) > 20:
            raise ValueError('Enter a fictional cash amount.')
        received = parse_cash(value)
        result = quote(g.demo_state['cart'], g.demo_state['discount'])
        if not result['lines'] or not result['total_cents'] <= received <= 200_000:
            raise ValueError('Enter sufficient fictional cash, up to $2,000.00.')
        return jsonify(total=format_cents(result['total_cents']), change=format_cents(received - result['total_cents']))
    except ValueError as error:
        return jsonify(error=str(error)), 400


@bp.post('/checkout')
@visitor
def checkout():
    fields({'checkout_token', 'payment_method', 'cash_received'})
    try:
        complete(g.demo_state, request.form.get('checkout_token', ''),
                 request.form.get('payment_method', ''), request.form.get('cash_received', ''),
                 business_now().strftime('%Y-%m-%d %H:%M:%S'))
    except ValueError as error:
        return page(str(error), 400)
    return redirect(url_for('.receipt'), code=303)


@bp.get('/receipt')
@visitor
def receipt():
    if request.args:
        abort(400, description='Demo receipts are available only in their originating visitor session.')
    if not g.demo_state['receipt']:
        abort(404, description='No active demo receipt. Your demonstration may have expired or restarted.')
    return render_template('demo_receipt.html', receipt=g.demo_state['receipt'], demo_timezone=BUSINESS_TIMEZONE)


@bp.post('/reset')
@visitor
def reset():
    fields(set())
    g.demo_state.update(empty_state(memory.clock()))
    flash('Your fictional cart and receipt have been cleared.', 'success')
    return redirect(url_for('.catalog'), code=303)


@bp.post('/clear')
@visitor
def clear():
    fields(set())
    g.demo_state.update(cart={}, discount=dict(kind='none', value=''), nonce=secrets.token_urlsafe(24))
    return redirect(url_for('.catalog'), code=303)
