import uuid

import pytest


async def test_signup_creates_tenant_and_returns_tokens(client, unique_email, unique_name):
    response = await client.post(
        "/auth/signup",
        json={"name": unique_name, "email": unique_email, "password": "Str0ngPassw0rd!"},
    )
    assert response.status_code == 201
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body


async def test_signup_duplicate_email_rejected(client, unique_email, unique_name):
    payload = {"name": unique_name, "email": unique_email, "password": "Str0ngPassw0rd!"}
    first = await client.post("/auth/signup", json=payload)
    assert first.status_code == 201

    second = await client.post(
        "/auth/signup",
        json={**payload, "name": unique_name + " Again"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "duplicate_email"


async def test_login_with_correct_credentials(client, unique_email, unique_name):
    password = "Str0ngPassw0rd!"
    await client.post(
        "/auth/signup", json={"name": unique_name, "email": unique_email, "password": password}
    )

    response = await client.post("/auth/login", json={"email": unique_email, "password": password})
    assert response.status_code == 200
    assert "access_token" in response.json()


async def test_login_with_wrong_password_rejected(client, unique_email, unique_name):
    await client.post(
        "/auth/signup",
        json={"name": unique_name, "email": unique_email, "password": "Str0ngPassw0rd!"},
    )

    response = await client.post("/auth/login", json={"email": unique_email, "password": "WrongPassword!"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


async def test_refresh_then_logout(client, unique_email, unique_name):
    signup = await client.post(
        "/auth/signup",
        json={"name": unique_name, "email": unique_email, "password": "Str0ngPassw0rd!"},
    )
    refresh_token = signup.json()["refresh_token"]

    refreshed = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    new_refresh_token = refreshed.json()["refresh_token"]

    # old refresh token is now revoked
    reused = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert reused.status_code == 401

    logout = await client.post("/auth/logout", json={"refresh_token": new_refresh_token})
    assert logout.status_code == 204


async def test_signup_retries_name_on_schema_collision(client, unique_email):
    """Two signups with the same first name should get sundar_dss then sundar1_dss
    (or whatever numeric suffix is next free) rather than colliding.
    """
    first_name = f"Collide{uuid.uuid4().hex[:6]}"

    first = await client.post(
        "/auth/signup",
        json={"name": first_name, "email": unique_email, "password": "Str0ngPassw0rd!"},
    )
    assert first.status_code == 201

    second_email = f"second-{uuid.uuid4().hex[:12]}@example.com"
    second = await client.post(
        "/auth/signup",
        json={"name": first_name, "email": second_email, "password": "Str0ngPassw0rd!"},
    )
    assert second.status_code == 201
