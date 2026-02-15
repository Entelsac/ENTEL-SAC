from app.main import SessionLocal, User, pwd_context

USERNAME = "airbone"
PASSWORD = "4f9r29f4k2to3"

db = SessionLocal()

u = db.query(User).filter(User.username == USERNAME).first()
if u:
    u.password_hash = pwd_context.hash(PASSWORD)
    u.role = "superadmin"
    if u.credits is None:
        u.credits = 999999
    else:
        u.credits = max(u.credits, 999999)
    db.commit()
    print("✅ Usuario actualizado:", USERNAME)
else:
    u = User(
        username=USERNAME,
        password_hash=pwd_context.hash(PASSWORD),
        role="superadmin",
        credits=999999
    )
    db.add(u)
    db.commit()
    print("✅ Usuario creado:", USERNAME)

db.close()
