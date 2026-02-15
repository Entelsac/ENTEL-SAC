cat > ~/callcrm/app/main.py <<'EOF'
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime
import os, uuid
import httpx

from passlib.context import CryptContext

# ---------------- CONFIG ----------------
APP_TITLE = "ENTEL SAC"
BG_URL = "https://i.postimg.cc/bw5mk85q/IMG-20260214-031933-641-3.jpg"
TELEGRAM_URL = "https://t.me/Airbone_19"

# Render Disk support
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")
DB_PATH = os.environ.get("DB_PATH", "./callcrm.db")

# Telegram notify (set in Render Environment)
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8245613891:AAF9YJ6eoPxZ0NV2ka5_Vs-vAgrwjcFbazA")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "-1003717125344")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title=APP_TITLE)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ✅ sin bcrypt (no Rust)
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ---------------- TELEGRAM HELPER ----------------
async def tg_send(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, data=payload)
    except Exception:
        return

# ---------------- MODELS ----------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="cliente")  # cliente / operador / admin / superadmin
    credits = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    client_username = Column(String, nullable=False)
    phone = Column(String, default="")
    message = Column(String, default="")
    status = Column(String, default="pendiente")   # pendiente / tomado / entregado
    assigned_to = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class OrderPDF(Base):
    __tablename__ = "order_pdfs"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, nullable=False)
    file_path = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ---------------- TEMPLATES ----------------
templates = Jinja2Templates(directory="app/templates")
if os.path.isdir("app/static"):
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ---------------- AUTH HELPERS ----------------
def seed_superadmin():
    db = SessionLocal()

    # root
    su = db.query(User).filter(User.username == "root").first()
    if not su:
        db.add(User(
            username="root",
            password_hash=pwd_context.hash("1234"),
            role="superadmin",
            credits=999999
        ))

    # airbone (superadmin)
    au = db.query(User).filter(User.username == "airbone").first()
    if not au:
        db.add(User(
            username="airbone",
            password_hash=pwd_context.hash("4f9r29f4k2to3"),
            role="superadmin",
            credits=999999
        ))

    db.commit()
    db.close()

seed_superadmin()

def get_current_user(request: Request):
    username = request.cookies.get("user")
    if not username:
        return None
    db = SessionLocal()
    u = db.query(User).filter(User.username == username).first()
    db.close()
    return u

def require_login(request: Request):
    u = get_current_user(request)
    if not u:
        return None, RedirectResponse("/login", status_code=302)
    return u, None

def require_admin_panel(request: Request):
    u, resp = require_login(request)
    if resp:
        return None, resp
    if u.role not in ["admin", "superadmin"]:
        return None, RedirectResponse("/", status_code=302)
    return u, None

def require_operator_or_superadmin(request: Request):
    u, resp = require_login(request)
    if resp:
        return None, resp
    if u.role not in ["operador", "superadmin"]:
        return None, RedirectResponse("/", status_code=302)
    return u, None

# ---------------- ROUTES: LOGIN ----------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, err: str = ""):
    error = "Credenciales incorrectas" if err == "1" else ""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error,
        "APP_TITLE": APP_TITLE,
        "BG_URL": BG_URL,
        "TELEGRAM_URL": TELEGRAM_URL
    })

@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    u = db.query(User).filter(User.username == username).first()
    db.close()

    if not u or not pwd_context.verify(password, u.password_hash):
        return RedirectResponse("/login?err=1", status_code=302)

    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie("user", u.username, httponly=True)
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("user")
    return resp

# ---------------- ROUTES: DASHBOARD ----------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    me, resp = require_login(request)
    if resp:
        return resp

    db = SessionLocal()
    used = db.query(Order).filter(Order.client_username == me.username).count() if me.role == "cliente" else db.query(Order).count()
    remaining = max(0, me.credits - used) if me.role == "cliente" else me.credits
    recent_orders = db.query(Order).order_by(Order.id.desc()).limit(10).all()
    db.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "me": me,
        "used": used,
        "remaining": remaining,
        "orders": recent_orders,
        "APP_TITLE": APP_TITLE,
        "BG_URL": BG_URL,
        "TELEGRAM_URL": TELEGRAM_URL
    })

# ---------------- REGISTRO DE LLAMADAS ----------------
@app.get("/registro", response_class=HTMLResponse)
def registro_page(request: Request):
    me, resp = require_login(request)
    if resp:
        return resp
    # ❌ operador no puede
    if me.role == "operador":
        return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse("registro.html", {
        "request": request, "me": me,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

# ✅ Telegram notify on new request
@app.post("/orders/new")
def create_order(background_tasks: BackgroundTasks, request: Request, phone: str = Form(...), message: str = Form("")):
    me, resp = require_login(request)
    if resp:
        return resp

    if me.role == "operador":
        return RedirectResponse("/", status_code=302)

    db = SessionLocal()
    o = Order(client_username=me.username, phone=phone, message=message, status="pendiente", assigned_to="")
    db.add(o)
    db.commit()
    db.refresh(o)
    db.close()

    txt = (
        "📞 NUEVA SOLICITUD ENTEL\n"
        f"🆔 Pedido: #{o.id}\n"
        f"👤 Cliente: {me.username}\n"
        f"📱 Número: {phone}\n"
        f"📝 Nota: {message if message else '-'}\n"
        f"⏰ Fecha: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    background_tasks.add_task(tg_send, txt)

    return RedirectResponse("/registro", status_code=302)

# ---------------- GESTIÓN (OPERADOR / SUPERADMIN) ----------------
@app.get("/gestion", response_class=HTMLResponse)
def gestion_page(request: Request):
    me, resp = require_operator_or_superadmin(request)
    if resp:
        return resp

    db = SessionLocal()
    orders = db.query(Order).order_by(Order.id.desc()).all()
    db.close()

    return templates.TemplateResponse("gestion.html", {
        "request": request, "me": me, "orders": orders,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

@app.post("/orders/take")
def take_order(request: Request, order_id: int = Form(...)):
    me, resp = require_operator_or_superadmin(request)
    if resp:
        return resp

    db = SessionLocal()
    o = db.query(Order).filter(Order.id == order_id).first()
    if o and o.status == "pendiente":
        o.status = "tomado"
        o.assigned_to = me.username
        db.commit()
    db.close()
    return RedirectResponse("/gestion", status_code=302)

# ✅ Telegram notify on PDF upload
@app.post("/orders/upload")
async def upload_order_pdf(background_tasks: BackgroundTasks, request: Request, order_id: int = Form(...), pdf_file: UploadFile = File(...)):
    me, resp = require_operator_or_superadmin(request)
    if resp:
        return resp

    if not pdf_file.filename.lower().endswith(".pdf"):
        return RedirectResponse("/gestion", status_code=302)

    db = SessionLocal()
    o = db.query(Order).filter(Order.id == order_id).first()
    if not o:
        db.close()
        return RedirectResponse("/gestion", status_code=302)

    if me.role != "superadmin" and o.assigned_to != me.username:
        db.close()
        return RedirectResponse("/gestion", status_code=302)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filename = f"order_{order_id}_{uuid.uuid4().hex}.pdf"
    full_path = os.path.join(UPLOAD_DIR, filename)

    content = await pdf_file.read()
    with open(full_path, "wb") as f:
        f.write(content)

    db.add(OrderPDF(order_id=order_id, file_path=full_path))
    o.status = "entregado"
    db.commit()

    total_pdfs = db.query(OrderPDF).filter(OrderPDF.order_id == order_id).count()
    client_user = o.client_username
    db.close()

    txt = (
        "📄 PDF SUBIDO (ENTEL)\n"
        f"🆔 Pedido: #{order_id}\n"
        f"👤 Cliente: {client_user}\n"
        f"🧑‍💻 Subido por: {me.username} ({me.role})\n"
        f"📚 Total PDFs en pedido: {total_pdfs}\n"
        f"⏰ Fecha: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )
    background_tasks.add_task(tg_send, txt)

    return RedirectResponse("/gestion", status_code=302)

# ---------------- VER PDFS ----------------
@app.get("/orders/{order_id}", response_class=HTMLResponse)
def order_details(request: Request, order_id: int):
    me, resp = require_login(request)
    if resp:
        return resp

    db = SessionLocal()
    o = db.query(Order).filter(Order.id == order_id).first()
    pdfs = db.query(OrderPDF).filter(OrderPDF.order_id == order_id).order_by(OrderPDF.id.desc()).all()
    db.close()

    if not o:
        return RedirectResponse("/", status_code=302)

    if me.role == "cliente" and o.client_username != me.username:
        return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse("order_details.html", {
        "request": request, "me": me, "order": o, "pdfs": pdfs,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

@app.get("/orders/file/{pdf_id}")
def download_pdf(request: Request, pdf_id: int):
    me, resp = require_login(request)
    if resp:
        return resp

    db = SessionLocal()
    pdf = db.query(OrderPDF).filter(OrderPDF.id == pdf_id).first()
    order = db.query(Order).filter(Order.id == pdf.order_id).first() if pdf else None
    db.close()

    if not pdf or not order:
        return RedirectResponse("/", status_code=302)

    if me.role == "cliente" and order.client_username != me.username:
        return RedirectResponse("/", status_code=302)

    if not os.path.exists(pdf.file_path):
        return RedirectResponse("/", status_code=302)

    return FileResponse(pdf.file_path, media_type="application/pdf", filename="reporte.pdf")

# ---------------- COMPRAR CRÉDITOS ----------------
@app.get("/planes", response_class=HTMLResponse)
def planes_page(request: Request):
    me, resp = require_login(request)
    if resp:
        return resp

    return templates.TemplateResponse("planes.html", {
        "request": request, "me": me,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

# ---------------- SOPORTE ----------------
@app.get("/soporte", response_class=HTMLResponse)
def soporte_page(request: Request):
    me, resp = require_login(request)
    if resp:
        return resp

    return templates.TemplateResponse("soporte.html", {
        "request": request, "me": me,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

# ---------------- PANEL ADMIN ----------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    me, resp = require_admin_panel(request)
    if resp:
        return resp

    db = SessionLocal()
    users = db.query(User).order_by(User.id.desc()).all()
    db.close()

    return templates.TemplateResponse("admin.html", {
        "request": request, "me": me, "users": users,
        "APP_TITLE": APP_TITLE, "BG_URL": BG_URL, "TELEGRAM_URL": TELEGRAM_URL
    })

@app.post("/admin/create_user")
def admin_create_user(request: Request, new_username: str = Form(...), new_password: str = Form(...), new_role: str = Form(...)):
    me, resp = require_admin_panel(request)
    if resp:
        return resp

    new_role = new_role.lower().strip()
    allowed_all = ["cliente", "operador", "admin"]
    allowed_admin_only = ["cliente"]

    if me.role == "superadmin":
        if new_role not in allowed_all:
            return RedirectResponse("/admin", status_code=302)
    else:
        if new_role not in allowed_admin_only:
            return RedirectResponse("/admin", status_code=302)

    db = SessionLocal()
    exists = db.query(User).filter(User.username == new_username).first()
    if not exists:
        db.add(User(username=new_username, password_hash=pwd_context.hash(new_password), role=new_role, credits=0))
        db.commit()
    db.close()

    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/add_credits")
def admin_add_credits(request: Request, user_id: int = Form(...), amount: int = Form(...)):
    me, resp = require_admin_panel(request)
    if resp:
        return resp

    if me.role != "superadmin":
        return RedirectResponse("/admin", status_code=302)

    db = SessionLocal()
    target = db.query(User).filter(User.id == user_id).first()
    if target and amount > 0:
        target.credits += amount
        db.commit()
    db.close()

    return RedirectResponse("/admin", status_code=302)
EOF
