from conftest import login


async def test_users_and_last_admin(admin_client):
    users = (await admin_client.get("/api/users")).json()
    assert (
        await admin_client.patch("/api/users/" + users[0]["id"], json={"role": "user"})
    ).status_code == 409
    response = await admin_client.post(
        "/api/users", json={"username": "member", "password": "member-password", "role": "user"}
    )
    assert response.status_code == 201
    assert (
        await admin_client.post(
            "/api/users", json={"username": "MEMBER", "password": "member-password"}
        )
    ).status_code == 409
    await admin_client.post("/api/logout")
    assert (await login(admin_client, "member", "member-password")).status_code == 200
    assert (await admin_client.get("/api/users")).status_code == 403
    assert (await admin_client.get("/app/users")).status_code == 403
