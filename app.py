from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, abort
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
import sqlite3
import os
import secrets
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from authlib.integrations.flask_client import OAuth

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "gizli_anahtar_123")
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

# Session / cookie security hardening
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Set Secure=True only in non-development environments (avoids issues over plain HTTP)
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', 'production') != 'development'

csrf = CSRFProtect(app)

# ---------------------------------------------------------------------------
# Rate limiting (Step 1)
# ---------------------------------------------------------------------------

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)

# ---------------------------------------------------------------------------
# HTTP Security Headers via Talisman (Step 4)
# ---------------------------------------------------------------------------

csp = {
    'default-src': ["'self'"],
    'style-src': [
        "'self'",
        "'unsafe-inline'",       # required for Jinja2-rendered inline styles
        'fonts.googleapis.com',
        'cdnjs.cloudflare.com',
    ],
    'font-src': [
        "'self'",
        'fonts.gstatic.com',
    ],
    'script-src': [
        "'self'",
        "'unsafe-inline'",       # required for existing inline JS in templates
        'cdnjs.cloudflare.com',
    ],
    'img-src': ["'self'", 'data:', 'lh3.googleusercontent.com'],  # Google profile pics
}

Talisman(
    app,
    content_security_policy=csp,
    force_https=False,           # nginx handles HTTPS termination — do not let Talisman redirect
    strict_transport_security=True,
    strict_transport_security_max_age=31536000,
    frame_options='DENY',        # X-Frame-Options: DENY (clickjacking protection)
    x_content_type_options=True, # X-Content-Type-Options: nosniff
)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
PER_PAGE = 12

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),          # Must be set in .env — no hardcoded fallback
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),  # Must be set in .env — no hardcoded fallback
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db_connection():
    return sqlite3.connect('database.db')


# ---------------------------------------------------------------------------
# Database init
# ---------------------------------------------------------------------------

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    # --- books ---
    c.execute('''CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    description TEXT,
                    image_url TEXT,
                    added_by INTEGER REFERENCES users(id),
                    genre TEXT,
                    publish_year INTEGER,
                    page_count INTEGER,
                    publisher TEXT,
                    isbn TEXT)''')

    # --- comments ---
    c.execute('''CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER,
                    user_id INTEGER,
                    comment TEXT,
                    FOREIGN KEY(book_id) REFERENCES books(id),
                    FOREIGN KEY(user_id) REFERENCES users(id))''')

    # --- users ---
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    phone TEXT,
                    password TEXT NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    profile_pic TEXT DEFAULT '/static/images/default.jpg',
                    role TEXT DEFAULT 'user' CHECK(role IN ('user','author','admin')))''')

    # --- ratings ---
    c.execute('''CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    book_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(book_id) REFERENCES books(id),
                    UNIQUE(user_id, book_id))''')

    # --- author_requests ---
    c.execute('''CREATE TABLE IF NOT EXISTS author_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by INTEGER,
                    reviewed_at TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(reviewed_by) REFERENCES users(id))''')

    # --- Idempotent migrations for existing databases ---
    for col_def in [
        ("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'",),
        ("ALTER TABLE books ADD COLUMN added_by INTEGER REFERENCES users(id)",),
        ("ALTER TABLE books ADD COLUMN genre TEXT",),
        ("ALTER TABLE books ADD COLUMN publish_year INTEGER",),
        ("ALTER TABLE books ADD COLUMN page_count INTEGER",),
        ("ALTER TABLE books ADD COLUMN publisher TEXT",),
        ("ALTER TABLE books ADD COLUMN isbn TEXT",),
    ]:
        try:
            c.execute(col_def[0])
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Migrate old is_admin=1 records → role='admin'
    c.execute("UPDATE users SET role='admin' WHERE is_admin=1 AND (role='user' OR role IS NULL)")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def is_owner(user_email):
    """Check if the given email matches OWNER_EMAIL in .env (case-insensitive)."""
    owner_email = os.getenv("OWNER_EMAIL", "")
    return bool(user_email and owner_email and user_email.lower() == owner_email.lower())


def get_effective_role(user):
    """Returns 'guest', 'user', 'author', 'admin', or 'owner'."""
    if not user:
        return 'guest'
    if is_owner(user.get('email')):
        return 'owner'
    return user.get('role', 'user')


ROLE_HIERARCHY = {'guest': 0, 'user': 1, 'author': 2, 'admin': 3, 'owner': 4}


def role_required(min_role):
    """Decorator that enforces a minimum role level."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            current_role = get_effective_role(g.user)
            if ROLE_HIERARCHY.get(current_role, 0) < ROLE_HIERARCHY.get(min_role, 99):
                flash('Bu işlem için yetkiniz yok.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return wrapped
    return decorator


def can_edit_book(user, book):
    """Returns True if user may edit or delete the given book (dict or sqlite Row)."""
    role = get_effective_role(user)
    if role in ('admin', 'owner'):
        return True
    if role == 'author':
        # book may be a dict or a sqlite3.Row-like object
        added_by = book.get('added_by') if isinstance(book, dict) else book['added_by']
        user_id = user.get('id') if isinstance(user, dict) else None
        if added_by is not None and user_id is not None and added_by == user_id:
            return True
    return False


# ---------------------------------------------------------------------------
# Session / user loading  (g.user is now a dict)
# ---------------------------------------------------------------------------

def set_user_session(user_row):
    """Store user data in session. user_row must have columns:
       id, first_name, last_name, is_admin, email, role (indices 0-5)."""
    session['user_id'] = user_row[0]
    session['first_name'] = user_row[1]
    session['last_name'] = user_row[2]
    session['is_admin'] = user_row[3]
    session['email'] = user_row[4]
    session['role'] = user_row[5] or 'user'


def load_user_from_db(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "SELECT id, first_name, last_name, is_admin, email, role FROM users WHERE id=?",
        (user_id,)
    )
    user = c.fetchone()
    conn.close()
    if user:
        set_user_session(user)
    return user


@app.before_request
def load_current_user():
    g.user = None
    if 'user_id' not in session:
        return

    required_keys = ('first_name', 'last_name', 'is_admin', 'email', 'role')
    # Require email to be a non-empty string — old sessions may have '' or missing key
    if all(k in session for k in required_keys) and session.get('email', ''):
        g.user = {
            'id': session['user_id'],
            'first_name': session['first_name'],
            'last_name': session['last_name'],
            'is_admin': session['is_admin'],
            'email': session['email'],
            'role': session['role'],
        }
        return

    user = load_user_from_db(session['user_id'])
    if user:
        g.user = {
            'id': user[0],
            'first_name': user[1],
            'last_name': user[2],
            'is_admin': user[3],
            'email': user[4],
            'role': user[5] or 'user',
        }


# Make helpers available in all templates
@app.context_processor
def inject_role_helpers():
    return dict(
        get_effective_role=get_effective_role,
        can_edit_book=can_edit_book,
        ROLE_HIERARCHY=ROLE_HIERARCHY,
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    if request.method == 'POST':
        first_name = request.form.get('first_name', '')
        last_name = request.form.get('last_name', '')
        email = request.form.get('email', '')
        phone = request.form.get('phone', '')
        password = request.form.get('password', '')

        # Step 6 — Input length validation
        if len(first_name) > 50:
            flash('İsim en fazla 50 karakter olabilir.', 'danger')
            return render_template('register.html')
        if len(last_name) > 50:
            flash('Soyisim en fazla 50 karakter olabilir.', 'danger')
            return render_template('register.html')
        if len(email) > 254:
            flash('E-posta adresi en fazla 254 karakter olabilir.', 'danger')
            return render_template('register.html')
        if len(phone) > 20:
            flash('Telefon numarası en fazla 20 karakter olabilir.', 'danger')
            return render_template('register.html')

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute(
                "INSERT INTO users (first_name, last_name, email, phone, password) VALUES (?, ?, ?, ?, ?)",
                (first_name, last_name, email, phone, hashed_password)
            )
            conn.commit()
            flash('Kayıt başarılı!', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            # Step 2 — Generic message to prevent email enumeration
            flash('Kayıt tamamlanamadı. Lütfen bilgilerinizi kontrol edin.', 'danger')
        finally:
            conn.close()

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        email = request.form.get('email', '')
        password = request.form.get('password', '')

        # Step 6 — Input length validation
        if len(email) > 254:
            flash('E-posta adresi en fazla 254 karakter olabilir.', 'danger')
            return render_template('login.html')
        if len(password) > 256:
            flash('Şifre çok uzun.', 'danger')
            return render_template('login.html')

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, password FROM users WHERE email=?", (email,))
        user = c.fetchone()
        conn.close()

        password_valid = False
        if user:
            try:
                password_valid = check_password_hash(user[1], password)
            except (ValueError, TypeError):
                password_valid = False

        if user and password_valid:
            load_user_from_db(user[0])
            flash('Giriş başarılı!', 'success')
            return redirect(url_for('index'))

        flash('E-posta veya şifre hatalı!', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Başarıyla çıkış yaptınız.', 'success')
    return redirect(url_for('index'))


@app.route('/login/google')
def login_google():
    redirect_uri = url_for('authorize_google', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/login/google/callback')
def authorize_google():
    token = google.authorize_access_token()
    resp = google.get('https://www.googleapis.com/oauth2/v1/userinfo')
    user_info = resp.json()

    email = user_info['email']
    first_name = user_info.get('given_name', '')
    last_name = user_info.get('family_name', '')

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email=?", (email,))
    user = c.fetchone()
    if not user:
        random_password = generate_password_hash(secrets.token_hex(32))
        c.execute(
            "INSERT INTO users (first_name, last_name, email, phone, password) VALUES (?, ?, ?, ?, ?)",
            (first_name, last_name, email, '', random_password)
        )
        conn.commit()
        user_id = c.lastrowid
    else:
        user_id = user[0]
    conn.close()

    load_user_from_db(user_id)
    flash('Google hesabınız ile giriş yapıldı.', 'success')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        flash('Lütfen önce giriş yapın.', 'warning')
        return redirect(url_for('login'))

    conn = get_db_connection()
    c = conn.cursor()

    if request.method == 'POST':
        first_name = request.form.get('first_name', '')
        last_name = request.form.get('last_name', '')
        phone = request.form.get('phone', '')

        # Step 6 — Input length validation
        if len(first_name) > 50:
            flash('İsim en fazla 50 karakter olabilir.', 'danger')
            return redirect(url_for('profile'))
        if len(last_name) > 50:
            flash('Soyisim en fazla 50 karakter olabilir.', 'danger')
            return redirect(url_for('profile'))
        if len(phone) > 20:
            flash('Telefon numarası en fazla 20 karakter olabilir.', 'danger')
            return redirect(url_for('profile'))

        profile_pic_file = request.files.get('profile_pic')
        profile_pic_path = None
        if profile_pic_file and profile_pic_file.filename != '':
            if not allowed_file(profile_pic_file.filename):
                flash('Geçersiz dosya türü. Sadece resim dosyaları yüklenebilir.', 'danger')
            else:
                safe_name = secure_filename(profile_pic_file.filename)
                filename = f"user_{session['user_id']}_{safe_name}"
                upload_dir = os.path.join('static', 'uploads')
                os.makedirs(upload_dir, exist_ok=True)
                upload_path = os.path.join(upload_dir, filename)
                profile_pic_file.save(upload_path)
                # Always store as a web-accessible relative path
                profile_pic_path = f"/static/uploads/{filename}"

        if profile_pic_path:
            c.execute(
                "UPDATE users SET first_name=?, last_name=?, phone=?, profile_pic=? WHERE id=?",
                (first_name, last_name, phone, profile_pic_path, session['user_id'])
            )
        else:
            c.execute(
                "UPDATE users SET first_name=?, last_name=?, phone=? WHERE id=?",
                (first_name, last_name, phone, session['user_id'])
            )

        conn.commit()
        session['first_name'] = first_name
        session['last_name'] = last_name
        flash('Profil güncellendi!', 'success')

    c.execute(
        "SELECT first_name, last_name, email, phone, profile_pic FROM users WHERE id=?",
        (session['user_id'],)
    )
    user = c.fetchone()

    # Check for a pending author request
    pending_request = None
    if g.user and g.user.get('role') == 'user':
        c.execute(
            "SELECT status FROM author_requests WHERE user_id=? AND status='pending'",
            (session['user_id'],)
        )
        pending_request = c.fetchone()

    conn.close()

    return render_template('profile.html', user=user, pending_request=pending_request)


# ---------------------------------------------------------------------------
# "Yazar Ol" (Become an Author) request — STEP C
# ---------------------------------------------------------------------------

@app.route('/yazar-ol', methods=['POST'])
@role_required('user')
def yazar_ol_talebi():
    conn = get_db_connection()
    c = conn.cursor()
    try:
        # Block if a pending request already exists
        c.execute(
            "SELECT id FROM author_requests WHERE user_id=? AND status='pending'",
            (g.user['id'],)
        )
        if c.fetchone():
            flash('Zaten bekleyen bir talebiniz var.', 'warning')
            return redirect(url_for('profile'))

        # Also block if user is already author or higher
        if get_effective_role(g.user) in ('author', 'admin', 'owner'):
            flash('Zaten yazar veya daha yüksek bir role sahipsiniz.', 'warning')
            return redirect(url_for('profile'))

        c.execute(
            "INSERT INTO author_requests (user_id) VALUES (?)",
            (g.user['id'],)
        )
        conn.commit()
        flash('Talebiniz admin onayına gönderildi.', 'success')
    finally:
        conn.close()
    return redirect(url_for('profile'))


# ---------------------------------------------------------------------------
# Admin panel — STEP D
# ---------------------------------------------------------------------------

@app.route('/admin')
@role_required('admin')
def admin_panel():
    conn = get_db_connection()
    c = conn.cursor()

    # Pending author requests
    c.execute("""
        SELECT ar.id, ar.user_id, ar.created_at,
               u.first_name || ' ' || u.last_name AS full_name,
               u.email
        FROM author_requests ar
        JOIN users u ON ar.user_id = u.id
        WHERE ar.status = 'pending'
        ORDER BY ar.created_at ASC
    """)
    pending_requests = c.fetchall()

    # Current authors with book count
    c.execute("""
        SELECT u.id, u.first_name || ' ' || u.last_name AS full_name,
               u.email,
               COUNT(b.id) AS book_count
        FROM users u
        LEFT JOIN books b ON b.added_by = u.id
        WHERE u.role = 'author'
        GROUP BY u.id
        ORDER BY full_name
    """)
    authors = c.fetchall()

    conn.close()
    return render_template('admin.html', pending_requests=pending_requests, authors=authors)


@app.route('/admin/yazar-talebi/<int:req_id>/onayla', methods=['POST'])
@role_required('admin')
def yazar_talebi_onayla(req_id):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT user_id FROM author_requests WHERE id=? AND status='pending'", (req_id,))
        row = c.fetchone()
        if not row:
            flash('Talep bulunamadı veya zaten işlenmiş.', 'danger')
            return redirect(url_for('admin_panel'))
        user_id = row[0]
        c.execute("UPDATE users SET role='author' WHERE id=?", (user_id,))
        c.execute(
            "UPDATE author_requests SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
            (g.user['id'], req_id)
        )
        conn.commit()
        flash('Kullanıcı yazarlığa onaylandı.', 'success')
    finally:
        conn.close()
    return redirect(url_for('admin_panel'))


@app.route('/admin/yazar-talebi/<int:req_id>/reddet', methods=['POST'])
@role_required('admin')
def yazar_talebi_reddet(req_id):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT id FROM author_requests WHERE id=? AND status='pending'", (req_id,))
        if not c.fetchone():
            flash('Talep bulunamadı veya zaten işlenmiş.', 'danger')
            return redirect(url_for('admin_panel'))
        c.execute(
            "UPDATE author_requests SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
            (g.user['id'], req_id)
        )
        conn.commit()
        flash('Talep reddedildi.', 'success')
    finally:
        conn.close()
    return redirect(url_for('admin_panel'))


@app.route('/admin/yazar/<int:user_id>/geri-al', methods=['POST'])
@role_required('admin')
def yazar_geri_al(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("UPDATE users SET role='user' WHERE id=? AND role='author'", (user_id,))
        conn.commit()
        flash('Yazarlık yetkisi geri alındı.', 'success')
    finally:
        conn.close()
    return redirect(url_for('admin_panel'))


# ---------------------------------------------------------------------------
# Owner panel — STEP E
# ---------------------------------------------------------------------------

@app.route('/admin/kullanicilar')
def tum_kullanicilar():
    if get_effective_role(g.user) != 'owner':
        flash('Bu sayfaya yalnızca site sahibi erişebilir.', 'danger')
        return redirect(url_for('index'))

    search = request.args.get('q', '').strip()
    conn = get_db_connection()
    c = conn.cursor()

    like = f"%{search}%"
    c.execute("""
        SELECT u.id, u.first_name || ' ' || u.last_name AS full_name,
               u.email, u.role,
               (SELECT COUNT(*) FROM books WHERE added_by = u.id) AS book_count,
               (SELECT COUNT(*) FROM comments WHERE user_id = u.id) AS comment_count
        FROM users u
        WHERE u.first_name || ' ' || u.last_name LIKE ? OR u.email LIKE ?
        ORDER BY full_name
    """, (like, like))
    users = c.fetchall()
    conn.close()

    # Pass only the lowercased owner email for identity comparison in the template;
    # avoid exposing it as raw data — templates use it only to decide which row gets an Owner badge
    owner_email_lower = os.getenv("OWNER_EMAIL", "").lower()
    return render_template('kullanicilar.html', users=users, search=search, owner_email=owner_email_lower)


@app.route('/admin/kullanicilar/<int:user_id>/rol-degistir', methods=['POST'])
def kullanici_rol_degistir(user_id):
    if get_effective_role(g.user) != 'owner':
        flash('Bu işlemi yalnızca site sahibi yapabilir.', 'danger')
        return redirect(url_for('index'))

    target_role = request.form.get('target_role', '')
    if target_role not in ('admin', 'user'):
        flash('Geçersiz rol.', 'danger')
        return redirect(url_for('tum_kullanicilar'))

    conn = get_db_connection()
    c = conn.cursor()
    try:
        # Safety: prevent owner from modifying themselves (they have no DB role anyway)
        c.execute("SELECT email FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if row and is_owner(row[0]):
            flash('Site sahibinin rolü değiştirilemez.', 'danger')
            return redirect(url_for('tum_kullanicilar'))

        c.execute("UPDATE users SET role=? WHERE id=?", (target_role, user_id))
        conn.commit()
        flash('Kullanıcı rolü güncellendi.', 'success')
    finally:
        conn.close()
    return redirect(url_for('tum_kullanicilar'))


# ---------------------------------------------------------------------------
# Main pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/kitaplar')
def kitaplar():
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM books")
    total = c.fetchone()[0]
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PER_PAGE
    c.execute("SELECT * FROM books LIMIT ? OFFSET ?", (PER_PAGE, offset))
    books = c.fetchall()
    conn.close()

    return render_template(
        'kitaplar.html',
        books=books,
        page=page,
        total_pages=total_pages,
        total=total
    )


@app.route('/kitap/<int:book_id>', methods=['GET', 'POST'])
def kitap_detay(book_id):
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        user_id = session.get('user_id', None)

        if request.method == 'POST':
            if "yorum" in request.form:
                yorum = request.form.get("yorum", "")
                # Step 6 — Comment length validation
                if len(yorum) > 1000:
                    flash('Yorum en fazla 1000 karakter olabilir.', 'danger')
                    return redirect(url_for('kitap_detay', book_id=book_id))
                if user_id and yorum.strip():
                    c.execute(
                        "INSERT INTO comments (book_id, user_id, comment) VALUES (?, ?, ?)",
                        (book_id, user_id, yorum.strip())
                    )
                    conn.commit()
                    return redirect(url_for('kitap_detay', book_id=book_id))

            if "rating" in request.form:
                try:
                    rating = int(request.form.get('rating', 0))
                except (ValueError, TypeError):
                    rating = 0
                # Step 5 — Explicit rating bounds validation with Turkish flash
                if not (1 <= rating <= 5):
                    flash('Geçersiz puan değeri.', 'danger')
                    return redirect(url_for('kitap_detay', book_id=book_id))
                if user_id:
                    c.execute("""
                        INSERT INTO ratings (user_id, book_id, rating)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, book_id) DO UPDATE SET rating=excluded.rating
                    """, (user_id, book_id, rating))
                    conn.commit()
                    return redirect(url_for('kitap_detay', book_id=book_id))

        c.execute("SELECT * FROM books WHERE id=?", (book_id,))
        book = c.fetchone()

        if not book:
            flash('Kitap bulunamadı.', 'danger')
            return redirect(url_for('kitaplar'))

        c.execute("""
            SELECT comments.id, comments.comment,
                   users.first_name || ' ' || users.last_name AS username,
                   users.profile_pic
            FROM comments
            LEFT JOIN users ON comments.user_id = users.id
            WHERE comments.book_id=?
            ORDER BY comments.id DESC
        """, (book_id,))
        comments = c.fetchall()

        user_rating = 0
        if user_id:
            c.execute("SELECT rating FROM ratings WHERE book_id=? AND user_id=?", (book_id, user_id))
            r = c.fetchone()
            if r:
                user_rating = r[0]

        c.execute("SELECT AVG(rating) FROM ratings WHERE book_id=?", (book_id,))
        avg_rating = c.fetchone()[0]
        avg_rating = round(avg_rating, 1) if avg_rating else 0

        # Convert book row to dict for template helpers
        book_dict = dict(book)

        return render_template(
            'kitap_detay.html',
            book=book_dict,
            comments=comments,
            user_rating=user_rating,
            avg_rating=avg_rating
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Book add — STEP G (now requires 'author' role)
# ---------------------------------------------------------------------------

@app.route('/kitap_ekle', methods=['GET', 'POST'])
@role_required('author')
def kitap_ekle():
    if request.method == 'POST':
        baslik = request.form.get('baslik', '').strip()
        yazar = request.form.get('yazar', '').strip()
        aciklama = request.form.get('aciklama', '').strip()
        genre = request.form.get('genre', '')
        publish_year = request.form.get('publish_year') or None
        page_count = request.form.get('page_count') or None
        publisher = request.form.get('publisher', '').strip()
        isbn = request.form.get('isbn', '').strip()

        if not baslik or not yazar:
            flash('Başlık ve yazar alanları zorunludur.', 'danger')
            return render_template('kitap_ekle.html', is_edit=False)

        # Step 6 — Input length validation
        if len(baslik) > 200:
            flash('Kitap başlığı en fazla 200 karakter olabilir.', 'danger')
            return render_template('kitap_ekle.html', is_edit=False)
        if len(yazar) > 100:
            flash('Yazar adı en fazla 100 karakter olabilir.', 'danger')
            return render_template('kitap_ekle.html', is_edit=False)
        if len(aciklama) > 5000:
            flash('Açıklama en fazla 5000 karakter olabilir.', 'danger')
            return render_template('kitap_ekle.html', is_edit=False)

        resim = ''
        cover_file = request.files.get('kapak')
        uploaded_cover = False
        safe_name = None
        temp_path = None

        if cover_file and cover_file.filename != '':
            if not allowed_file(cover_file.filename):
                flash('Geçersiz dosya türü. Sadece resim dosyaları yüklenebilir.', 'danger')
                return render_template('kitap_ekle.html', is_edit=False)
            safe_name = secure_filename(cover_file.filename)
            upload_dir = os.path.join('static', 'uploads', 'books')
            os.makedirs(upload_dir, exist_ok=True)
            temp_path = os.path.join(upload_dir, f"book_temp_{safe_name}")
            cover_file.save(temp_path)
            resim = f"/static/uploads/books/book_temp_{safe_name}"
            uploaded_cover = True

        conn = get_db_connection()
        c = conn.cursor()
        try:
            c.execute(
                """INSERT INTO books (title, author, description, image_url, added_by,
                                     genre, publish_year, page_count, publisher, isbn)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (baslik, yazar, aciklama, resim, g.user['id'],
                 genre, publish_year, page_count, publisher, isbn)
            )
            book_id = c.lastrowid

            if uploaded_cover and temp_path and safe_name:
                final_name = f"book_{book_id}_{safe_name}"
                upload_dir = os.path.join('static', 'uploads', 'books')
                final_path = os.path.join(upload_dir, final_name)
                os.rename(temp_path, final_path)
                resim = f"/static/uploads/books/{final_name}"
                c.execute("UPDATE books SET image_url=? WHERE id=?", (resim, book_id))

            conn.commit()
            flash('Kitap başarıyla eklendi.', 'success')
            return redirect(url_for('kitap_detay', book_id=book_id))
        finally:
            conn.close()

    return render_template('kitap_ekle.html', is_edit=False)


# ---------------------------------------------------------------------------
# Book edit — STEP G
# ---------------------------------------------------------------------------

@app.route('/kitap/<int:book_id>/duzenle', methods=['GET', 'POST'])
def kitap_duzenle(book_id):
    if not g.user:
        flash('Lütfen önce giriş yapın.', 'warning')
        return redirect(url_for('login'))

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        c.execute("SELECT * FROM books WHERE id=?", (book_id,))
        book = c.fetchone()
        if not book:
            flash('Kitap bulunamadı.', 'danger')
            return redirect(url_for('kitaplar'))

        book_dict = dict(book)
        if not can_edit_book(g.user, book_dict):
            flash('Bu kitabı düzenleme yetkiniz yok.', 'danger')
            return redirect(url_for('kitap_detay', book_id=book_id))

        if request.method == 'POST':
            baslik = request.form.get('baslik', '').strip()
            yazar = request.form.get('yazar', '').strip()
            aciklama = request.form.get('aciklama', '').strip()
            genre = request.form.get('genre', '')
            publish_year = request.form.get('publish_year') or None
            page_count = request.form.get('page_count') or None
            publisher = request.form.get('publisher', '').strip()
            isbn = request.form.get('isbn', '').strip()

            if not baslik or not yazar:
                flash('Başlık ve yazar alanları zorunludur.', 'danger')
                return render_template('kitap_ekle.html', is_edit=True, book=book_dict)

            # Handle new cover upload
            resim = book_dict.get('image_url', '')
            cover_file = request.files.get('kapak')
            if cover_file and cover_file.filename != '':
                if not allowed_file(cover_file.filename):
                    flash('Geçersiz dosya türü. Sadece resim dosyaları yüklenebilir.', 'danger')
                    return render_template('kitap_ekle.html', is_edit=True, book=book_dict)
                safe_name = secure_filename(cover_file.filename)
                final_name = f"book_{book_id}_{safe_name}"
                upload_dir = os.path.join('static', 'uploads', 'books')
                os.makedirs(upload_dir, exist_ok=True)
                final_path = os.path.join(upload_dir, final_name)
                cover_file.save(final_path)
                resim = f"/static/uploads/books/{final_name}"

            c.execute("""
                UPDATE books SET title=?, author=?, description=?, image_url=?,
                                 genre=?, publish_year=?, page_count=?, publisher=?, isbn=?
                WHERE id=?
            """, (baslik, yazar, aciklama, resim, genre, publish_year, page_count, publisher, isbn, book_id))
            conn.commit()
            flash('Kitap güncellendi.', 'success')
            return redirect(url_for('kitap_detay', book_id=book_id))

        return render_template('kitap_ekle.html', is_edit=True, book=book_dict)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Book delete — STEP H
# ---------------------------------------------------------------------------

@app.route('/kitap/<int:book_id>/sil', methods=['POST'])
def kitap_sil(book_id):
    if not g.user:
        flash('Lütfen önce giriş yapın.', 'warning')
        return redirect(url_for('login'))

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM books WHERE id=?", (book_id,))
        book = c.fetchone()
        if not book:
            flash('Kitap bulunamadı.', 'danger')
            return redirect(url_for('kitaplar'))

        book_dict = dict(book)
        if not can_edit_book(g.user, book_dict):
            flash('Bu kitabı silme yetkiniz yok.', 'danger')
            return redirect(url_for('kitap_detay', book_id=book_id))

        # Clean up related rows (no CASCADE on SQLite by default)
        c.execute("DELETE FROM comments WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM ratings WHERE book_id=?", (book_id,))
        c.execute("DELETE FROM books WHERE id=?", (book_id,))
        conn.commit()
        flash('Kitap silindi.', 'success')
    finally:
        conn.close()
    return redirect(url_for('kitaplar'))


# ---------------------------------------------------------------------------
# Comment delete — STEP H
# ---------------------------------------------------------------------------

@app.route('/kitap/<int:book_id>/yorum/<int:comment_id>/sil', methods=['POST'])
def yorum_sil(book_id, comment_id):
    if get_effective_role(g.user) not in ('admin', 'owner'):
        flash('Yorum silme yetkiniz yok.', 'danger')
        return redirect(url_for('kitap_detay', book_id=book_id))

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("DELETE FROM comments WHERE id=? AND book_id=?", (comment_id, book_id))
        conn.commit()
        flash('Yorum silindi.', 'success')
    finally:
        conn.close()
    return redirect(url_for('kitap_detay', book_id=book_id))


# ---------------------------------------------------------------------------
# Search & autocomplete
# ---------------------------------------------------------------------------

@app.route('/arama_sonuc')
def arama_sonuc():
    query = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1

    conn = get_db_connection()
    c = conn.cursor()
    like_query = f"%{query}%"

    c.execute(
        "SELECT COUNT(*) FROM books WHERE title LIKE ? OR author LIKE ?",
        (like_query, like_query)
    )
    total = c.fetchone()[0]
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PER_PAGE
    c.execute(
        "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? LIMIT ? OFFSET ?",
        (like_query, like_query, PER_PAGE, offset)
    )
    books = c.fetchall()
    conn.close()

    return render_template(
        'arama_sonuc.html',
        books=books,
        query=query,
        page=page,
        total_pages=total_pages,
        total=total
    )


@app.route('/autocomplete')
def autocomplete():
    term = request.args.get('term', '')
    conn = get_db_connection()
    c = conn.cursor()
    like_term = f"%{term}%"
    c.execute(
        "SELECT id, title, author, image_url FROM books WHERE title LIKE ? OR author LIKE ?",
        (like_term, like_term)
    )
    results = c.fetchall()
    suggestions = []
    for r in results:
        suggestions.append({
            'id': r[0],
            'title': r[1],
            'author': r[2],
            'image_url': r[3] if r[3] else "https://via.placeholder.com/50x70?text=No+Image"
        })
    conn.close()
    return jsonify(suggestions)


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500


@app.errorhandler(429)
def ratelimit_error(e):
    # Step 1 — Turkish-language rate limit response
    flash('Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin.', 'danger')
    return redirect(url_for('login')), 429


# ---------------------------------------------------------------------------
# Footer static pages (Step 7)
# ---------------------------------------------------------------------------

@app.route('/hakkimizda')
def hakkimizda():
    return render_template('hakkimizda.html')


@app.route('/iletisim')
def iletisim():
    return render_template('iletisim.html')


@app.route('/gizlilik')
def gizlilik():
    return render_template('gizlilik.html')


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

init_db()

if __name__ == '__main__':
    debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug_mode)
