"""Explicit identity fixtures; never bypass application authentication guards."""

from services.auth import assign_legacy_owner, register_user

PASSWORD = "a fictional testing passphrase"
EMAIL = "qa@example.com"


def login(client, email=EMAIL, password=PASSWORD):
    client.get("/login")
    with client.session_transaction() as state:
        token = state["csrf_token"]
    response = client.post(
        "/login", data={"csrf_token": token, "email": email, "password": password}
    )
    assert response.status_code == 302
    client.get("/")


def legacy_owner(client):
    register_user(EMAIL, PASSWORD, PASSWORD, "Regression test store")
    assign_legacy_owner(EMAIL)
    login(client)
