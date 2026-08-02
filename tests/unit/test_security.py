from app.security import decrypt_secret, encrypt_secret, hash_password, verify_password


def test_password_hash():
    h=hash_password('Secret123!')
    assert verify_password('Secret123!',h)
    assert not verify_password('wrong',h)

def test_secret_encryption():
    token=encrypt_secret('api-key-1234')
    assert token!='api-key-1234'
    assert decrypt_secret(token)=='api-key-1234'


def test_refresh_rejects_disabled_user(client, auth):
    from app.database import SessionLocal
    from app.models import User

    created = client.post(
        '/api/v1/users',
        headers=auth,
        json={'email': 'disabled@example.test', 'password': 'StrongPass123!', 'roles': ['VIEWER']},
    )
    assert created.status_code == 201, created.text
    login = client.post('/api/v1/auth/login', json={'email': 'disabled@example.test', 'password': 'StrongPass123!'})
    assert login.status_code == 200

    db = SessionLocal()
    try:
        user = db.get(User, created.json()['id'])
        user.is_active = False
        db.commit()
    finally:
        db.close()

    refresh = client.post('/api/v1/auth/refresh', json={'refresh_token': login.json()['refresh_token']})
    assert refresh.status_code == 401
