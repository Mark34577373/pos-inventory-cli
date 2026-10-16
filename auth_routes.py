"""Authentication routes; store data routes remain in web_app.py."""

from contextlib import closing
from urllib.parse import unquote, urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import MethodNotAllowed, NotFound

from database.database import get_db_connection
from services.auth import (
    authenticate,
    consume_rate_limit,
    create_login_session,
    register_user,
    revoke_session,
)

bp = Blueprint("auth", __name__)


def safe_next(value):
    if not value or len(value) > 2048:
        return url_for("dashboard")
    decoded = unquote(value)
    try:
        parsed = urlsplit(decoded)
    except ValueError:
        return url_for("dashboard")
    if (
        parsed.scheme
        or parsed.netloc
        or not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(char) < 32 for char in decoded)
    ):
        return url_for("dashboard")
    try:
        endpoint, _ = current_app.url_map.bind("localhost").match(parsed.path, method="GET")
    except (NotFound, MethodNotAllowed):
        return url_for("dashboard")
    if endpoint in {"static", "auth.login", "auth.register", "auth.demo", "auth.logout"}:
        return url_for("dashboard")
    return value


def establish_login(user_id, *, store_id=None):
    revoke_session(session.get("login_token"))
    token = create_login_session(user_id,store_id=store_id)
    session.clear()
    session["login_token"] = token
    session.permanent = True


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        if not consume_rate_limit(
            current_app.secret_key, "register", request.remote_addr or "unknown"
        ):
            return render_template(
                "auth.html", mode="register", error="Too many attempts. Try again in 15 minutes."
            ), 429
        try:
            register_user(
                request.form.get("email", ""),
                request.form.get("password", ""),
                request.form.get("password_confirm", ""),
                request.form.get("store_name", ""),
            )
        except ValueError as error:
            return render_template("auth.html", mode="register", error=str(error)), 400
        flash("Account created. Sign in to open your store.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth.html", mode="register")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not consume_rate_limit(
            current_app.secret_key,
            "login",
            request.remote_addr or "unknown",
            request.form.get("email", ""),
        ):
            return render_template(
                "auth.html", mode="login", error="Too many attempts. Try again in 15 minutes."
            ), 429
        user_id = authenticate(request.form.get("email", ""), request.form.get("password", ""))
        if user_id is None:
            return render_template(
                "auth.html", mode="login", error="Invalid email or password."
            ), 400
        try:
            establish_login(user_id)
        except ValueError:
            return render_template(
                "auth.html", mode="login", error="Unable to sign in to a store."
            ), 403
        return redirect(safe_next(request.form.get("next", "")))
    return render_template("auth.html", mode="login")


@bp.route("/stores",methods=["GET","POST"])
def stores():
    if not g.user or g.is_demo:
        abort(403)
    if request.method=='POST':
        if set(request.form)-{'csrf_token','store_id'}:
            abort(400)
        try:
            store_id=int(request.form.get('store_id',''))
            # Membership is checked again when creating the replacement login session.
            with closing(get_db_connection()) as conn:
                if not conn.execute("SELECT 1 FROM store_memberships WHERE user_id=? AND store_id=? AND role='owner'",(g.user['id'],store_id)).fetchone():
                    abort(404)
            establish_login(g.user['id'],store_id=store_id)
        except (ValueError,OverflowError):
            abort(400)
        return redirect(url_for('sale'))
    with closing(get_db_connection()) as conn:
        choices=[dict(row) for row in conn.execute("SELECT s.id,s.name FROM stores s JOIN store_memberships m ON m.store_id=s.id WHERE m.user_id=? AND m.role='owner' AND s.kind!='demo' ORDER BY s.name,s.id LIMIT 100",(g.user['id'],))]
    return render_template('store_picker.html',choices=choices)


@bp.post("/logout")
def logout():
    revoke_session(session.get("login_token"))
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.post("/demo")
def demo():
    with closing(get_db_connection()) as conn:
        store = conn.execute("SELECT id FROM stores WHERE kind='demo'").fetchone()
    if store is None:
        abort(404)
    revoke_session(session.get("login_token"))
    session.clear()
    session["demo_mode"] = True
    return redirect(url_for("dashboard"))


@bp.get("/account")
def account():
    return render_template("account.html")
