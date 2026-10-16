"""Private settings; global authentication, demo and CSRF guards apply."""

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from services.money import calculate_tax_cents
from services.settings import PERIODS, VIEWS, get_settings, save_settings

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.get("")
def overview():
    settings = get_settings()
    return render_template(
        "settings.html",
        settings=settings,
        periods=PERIODS,
        views=VIEWS,
        preview_tax=calculate_tax_cents(1000, settings["tax_rate"]),
    )


@bp.post("/<section>")
def save(section):
    # Identity/ownership fields are never accepted, even if a client submits them.
    if any(key in request.form for key in ("store_id", "user_id", "role")):
        return render_template("error.html", message="Ownership fields cannot be changed."), 400
    if not g.user:
        return render_template("error.html", message="Sign in to manage settings."), 403
    try:
        save_settings(section, request.form)
    except ValueError as error:
        flash(str(error), "error")
        return render_template(
            "settings.html",
            settings=get_settings(),
            periods=PERIODS,
            views=VIEWS,
            preview_tax=calculate_tax_cents(1000, get_settings()["tax_rate"]),
        ), 400
    flash("Store settings saved.", "success")
    return redirect(url_for("settings.overview", _anchor=section))
