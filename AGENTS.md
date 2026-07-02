# 🤖 AGENTS.md — Dokushoka AI Agent Guidance

> This file is the **single source of truth** for any AI coding assistant (Cursor, GitHub Copilot, Gemini, Claude, etc.) working on this project.
> Read this file **completely** before making any changes to the codebase.

---

## 1. Project Summary

**Dokushoka** is a Japanese-literature-themed book discovery and review platform built with **Flask (Python)**. Users can register, log in (including via Google OAuth), browse books, write comments, and give star ratings. A 4-tier role system controls who can add/edit/delete books and manage users.

| Property        | Value                                         |
|-----------------|-----------------------------------------------|
| Language        | Python 3.12                                   |
| Framework       | Flask 3.1.x                                   |
| Database        | SQLite (`database.db`, file-based)            |
| Frontend        | Jinja2 templates, Vanilla CSS, Vanilla JS     |
| Auth            | Flask session + Werkzeug hashing + Google OAuth (Authlib) |
| Deployment      | Gunicorn (production), Docker / docker-compose |
| UI Language     | Turkish (flash messages, nav labels, form labels) |

---

## 2. Repository Layout

```
dokushoka/
├── app.py                  # ALL routes and business logic (single-file backend)
├── update_db.py            # One-off DB migration script (ALTER TABLE)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Production image (python:3.12-slim + Gunicorn)
├── docker-compose.yml      # Dev overrides (Flask debug server, volume mounts)
├── .env                    # Secret env vars — NEVER commit this file
├── .env.example            # Template for .env (safe to commit, contains only placeholders)
├── .dockerignore           # Files excluded from Docker build context
├── database.db             # SQLite database file (runtime artifact)
├── AGENTS.md               # Single source of truth for AI agents (this file)
├── README.md               # Project documentation for external developers
├── static/
│   ├── style.css           # ALL CSS — single stylesheet, CSS custom properties
│   ├── images/             # Logo, google_logo, default.jpg, static book images
│   └── uploads/            # User profile pictures + book covers (runtime, created automatically)
│       └── books/          # Book cover images uploaded via the form
└── templates/
    ├── base.html           # Master layout (header with role-aware nav, footer, flash messages, JS)
    ├── index.html          # Homepage (hero section + featured books)
    ├── kitaplar.html       # Book listing with pagination
    ├── kitap_detay.html    # Book detail, comments, star rating, edit/delete buttons (role-aware)
    ├── kitap_ekle.html     # Add/Edit book form (shared via is_edit flag, author+ role required)
    ├── admin.html          # Admin panel (pending author requests + current authors list)
    ├── kullanicilar.html   # Owner-only panel (all users list with promote/demote admin)
    ├── login.html          # Email/password login + Google OAuth button
    ├── register.html       # User registration form
    ├── profile.html        # Profile view/edit + profile picture upload + Yazar Ol button
    ├── arama_sonuc.html    # Search results page
    ├── hakkimizda.html     # About page (/hakkimizda)
    ├── iletisim.html       # Contact page (/iletisim)
    ├── gizlilik.html       # Privacy policy page (/gizlilik)
    ├── 404.html            # Custom 404 error page
    └── 500.html            # Custom 500 error page
```

---

## 3. Database Schema

`init_db()` in `app.py` creates all tables automatically on startup and runs idempotent migrations:

```sql
-- Users (role column added — replaces is_admin for new logic)
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    phone       TEXT,
    password    TEXT NOT NULL,       -- Werkzeug pbkdf2 hash
    is_admin    INTEGER DEFAULT 0,   -- LEGACY: kept for backward compat; never write new logic against it
    profile_pic TEXT DEFAULT '/static/images/default.jpg',
    role        TEXT DEFAULT 'user' CHECK(role IN ('user','author','admin'))
);

-- Books (added_by + extra metadata columns)
CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    author       TEXT NOT NULL,
    description  TEXT,
    image_url    TEXT,
    added_by     INTEGER REFERENCES users(id),  -- NULL = legacy book (admin/owner only can edit)
    genre        TEXT,
    publish_year INTEGER,
    page_count   INTEGER,
    publisher    TEXT,
    isbn         TEXT
);

-- Comments
CREATE TABLE IF NOT EXISTS comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER,
    user_id INTEGER,
    comment TEXT,
    FOREIGN KEY(book_id) REFERENCES books(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Ratings (1–5 stars, one per user per book)
CREATE TABLE IF NOT EXISTS ratings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    rating  INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(book_id) REFERENCES books(id),
    UNIQUE(user_id, book_id)
);

-- Author requests (users requesting to become an author)
CREATE TABLE IF NOT EXISTS author_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    status      TEXT DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_by INTEGER,
    reviewed_at TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(reviewed_by) REFERENCES users(id)
);
```

> **Never drop or recreate tables** without first checking for existing data in `database.db`.
> Use `ALTER TABLE` or `CREATE TABLE IF NOT EXISTS` for safe migrations.
> `init_db()` automatically migrates old `is_admin=1` records → `role='admin'`.

---

## 4. Role Hierarchy

| Role | Where stored | Permissions |
|---|---|---|
| **Guest** | No session | View only |
| **user** | `users.role = 'user'` | View + comment + request to become author |
| **author** | `users.role = 'author'` | user perms + add/edit/delete **only their own** books |
| **admin** | `users.role = 'admin'` | author perms + edit/delete **any** book + delete **any** comment + manage author requests |
| **owner** | **Not in DB** — computed from `OWNER_EMAIL` env var | all admin perms + promote/demote admin (owner-only "All Users" panel) |

### Critical rule: Owner is NOT a DB role
The `role` column only holds `'user'`, `'author'`, or `'admin'`. Owner status is computed on every request by comparing the logged-in user's email (case-insensitive) against `OWNER_EMAIL` from `.env`. It cannot be changed or lost via any UI action.

---

## 5. Route Map

| Route | Methods | Auth Required | Description |
|---|---|---|---|
| `/` | GET | No | Homepage |
| `/kitaplar` | GET | No | Paginated book listing (12/page) |
| `/kitap/<int:book_id>` | GET, POST | POST needs login | Book detail, comments, ratings |
| `/kitap_ekle` | GET, POST | `author`+ | Add new book (two-column form, live preview) |
| `/kitap/<id>/duzenle` | GET, POST | `can_edit_book()` | Edit existing book |
| `/kitap/<id>/sil` | POST | `can_edit_book()` | Delete book + cascade comments/ratings |
| `/kitap/<id>/yorum/<cid>/sil` | POST | `admin`+ | Delete a comment |
| `/arama_sonuc` | GET | No | Search results (title or author) |
| `/autocomplete` | GET | No | JSON search suggestions |
| `/register` | GET, POST | No | User registration |
| `/login` | GET, POST | No | Email/password login |
| `/login/google` | GET | No | Initiate Google OAuth flow |
| `/login/google/callback` | GET | No | Google OAuth callback |
| `/profile` | GET, POST | Login | View and update user profile |
| `/logout` | GET | Login | Clear session and redirect |
| `/yazar-ol` | POST | `user`+ | Submit "become an author" request |
| `/admin` | GET | `admin`+ | Admin panel: pending requests + author list |
| `/admin/yazar-talebi/<id>/onayla` | POST | `admin`+ | Approve author request |
| `/admin/yazar-talebi/<id>/reddet` | POST | `admin`+ | Reject author request |
| `/admin/yazar/<user_id>/geri-al` | POST | `admin`+ | Revoke author status |
| `/admin/kullanicilar` | GET | `owner` only | All users panel (owner-exclusive) |
| `/admin/kullanicilar/<user_id>/rol-degistir` | POST | `owner` only | Promote/demote admin |
| `/hakkimizda` | GET | No | About page |
| `/iletisim` | GET | No | Contact page |
| `/gizlilik` | GET | No | Privacy policy page |

---

## 6. Key Functions in `app.py`

| Function | Purpose |
|---|---|
| `init_db()` | Creates/migrates all DB tables on startup |
| `get_db_connection()` | Opens and returns a raw `sqlite3.Connection` to `database.db` |
| `allowed_file(filename)` | Validates image extensions (`png`, `jpg`, `jpeg`, `gif`, `webp`) |
| `set_user_session(row)` | Writes user data into Flask `session` from a DB row (index 0–5: id, first_name, last_name, is_admin, email, role) |
| `load_user_from_db(id)` | Fetches user from DB and calls `set_user_session()` |
| `load_current_user()` | `@app.before_request` hook — populates `g.user` dict for every request |
| `is_owner(email)` | Returns True if email matches `OWNER_EMAIL` env var (case-insensitive) |
| `get_effective_role(user)` | Returns `'guest'`, `'user'`, `'author'`, `'admin'`, or `'owner'` |
| `role_required(min_role)` | Decorator that enforces a minimum role level |
| `can_edit_book(user, book)` | Returns True if user may edit/delete the given book |

---

## 7. `g.user` Shape

`g.user` is now a **dict** (not a tuple). Its keys:

```python
g.user = {
    'id':         int,   # user primary key
    'first_name': str,
    'last_name':  str,
    'is_admin':   int,   # LEGACY field (0 or 1) — do not use for auth logic
    'email':      str,
    'role':       str,   # 'user' | 'author' | 'admin'
}
```

To get the effective role (including `'owner'`), **always** call `get_effective_role(g.user)`.  
**Never** check `g.user['is_admin']` or `g.user[3]` in new code.

In templates, `get_effective_role` and `can_edit_book` are injected via `@app.context_processor` and available everywhere.

---

## 8. Environment Variables

Copy `.env.example` to `.env` and fill in real values. **Never commit `.env`.**

| Variable | Description | Default (fallback) |
|---|---|---|
| `SECRET_KEY` | Flask session signing key | `gizli_anahtar_123` (change in production) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 client ID | **None — must be set; no hardcoded fallback** |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 client secret | **None — must be set; no hardcoded fallback** |
| `FLASK_DEBUG` | `"true"` enables debug mode | `"False"` |
| `FLASK_ENV` | Set to `"development"` locally to disable `SESSION_COOKIE_SECURE` (plain HTTP) | `"production"` (Secure flag active) |
| `OWNER_EMAIL` | The email address that gets permanent owner status | `""` (no owner if blank) |

> ⚠️ `OWNER_EMAIL` **must** be set in `.env` on the server. Without it, no one has owner access.
> ⚠️ In production, `FLASK_DEBUG` **must** be `"False"`.
> ⚠️ `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` have **no hardcoded fallbacks**. The Google OAuth flow will fail if these are not set.

---

## 9. Running the Project

### Local (virtualenv)

```bash
# 1. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\activate        # Windows PowerShell
# source venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
copy .env.example .env        # then edit .env — especially OWNER_EMAIL

# 4. Run
python app.py
# → http://127.0.0.1:5000
```

### Docker (Development)

```bash
# Start (uses Flask debug server with hot-reload)
docker compose up

# Stop
docker compose down
```

### Docker (Production)

Remove the `command:` and `volumes:` lines from `docker-compose.yml` so the Dockerfile's Gunicorn `CMD` takes effect:

```bash
docker compose up -d
```

---

## 10. Architecture Decisions & Constraints

### Single-file backend
All routes, helpers, and DB logic live in `app.py`. Do **not** refactor into blueprints or packages without a clear reason — it adds complexity for a project of this size.

### SQLite only
The project intentionally uses SQLite. Do **not** introduce PostgreSQL, MySQL, or any ORM (SQLAlchemy, etc.) unless explicitly requested. Raw `sqlite3` queries with `?` placeholders are used throughout.

### CSRF protection
`Flask-WTF`'s `CSRFProtect` is active globally. Every `<form method="POST">` template **must** include:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

### Session-based auth
There is no JWT or token-based auth. User identity lives entirely in `session` (server-side). `g.user` is now a **dict** — see Section 7.

### File uploads
- Profile pictures → `static/uploads/user_<id>_<safe_name>` — stored as `/static/uploads/<filename>` (web-relative)
- Book covers → `static/uploads/books/book_<id>_<safe_name>` — stored as `/static/uploads/books/<filename>` (web-relative)
- Always validate with `allowed_file()` and sanitize with `secure_filename()`.
- Max upload size: **5 MB** (`MAX_CONTENT_LENGTH`).

### DB connection lifecycle
Open connections with `get_db_connection()` and always close them in a `try/finally` block. Do **not** leave connections open across early `return` statements.

### Row factory
`kitap_detay()`, `kitap_duzenle()`, and `kitap_sil()` set `conn.row_factory = sqlite3.Row` so the book object is dict-accessible in templates (`book.title`, `book['image_url']`, etc.). Other routes still return plain tuples and use integer indexing.

---

## 11. Design System

The UI follows a **Japanese / East Asian aesthetic**. All styling is in `static/style.css` using CSS custom properties.

### Color Palette

```css
:root {
  --color-sumi:   #1C1C1C;  /* Main text (ink black) */
  --color-washi:  #F7F2E7;  /* Background (rice paper cream) */
  --color-ai:     #1F3A5F;  /* Header, links, primary buttons (indigo) */
  --color-shu:    #BC4434;  /* CTA buttons (vermilion / torii red) */
  --color-sakura: #F1C6C6;  /* Hover states, badges, accents */
  --color-matcha: #6B8E5A;  /* Category labels, approve buttons */
}
```

### Typography

- **Headings & logo:** `Shippori Mincho` (Google Fonts)
- **Body & UI:** `Noto Sans JP` (Google Fonts)
- Both fonts are loaded in `base.html`'s `<head>`.
- Ensure correct rendering of Turkish characters: **ş, ı, ğ, ü, ö, ç**.

### Icon Strategy

- **Library:** Font Awesome 6.5 (CDN, loaded in `base.html`)
- **Custom SVGs:** Torii gate in the logo area; brush-stroke `<hr>` dividers.
- Use `aria-hidden="true"` on all decorative icons.

---

## 12. AI Agent Working Rules

Follow these rules strictly when modifying this project:

### Before writing any code
1. Read `app.py` in full.
2. Read every file in `templates/` that your change will affect.
3. Check `static/style.css` for existing class names before adding new ones.

### Python / Backend
- Keep all business logic in `app.py`. Do not create extra modules.
- Use `?` placeholders for **all** SQL queries — no f-string or `.format()` interpolation in SQL.
- Always close DB connections with `try/finally` or a context manager.
- Never use `debug=True` literally — always read from `os.getenv("FLASK_DEBUG", "False")`.
- Preserve all existing Turkish flash messages (e.g., `'Giriş başarılı!'`, `'Kayıt başarıyla oluşturuldu.'`).
- **Never check `g.user['is_admin']` or `g.user[3]` for permissions** — always use `get_effective_role(g.user)` or `role_required()` or `can_edit_book()`.

### HTML / Templates
- All templates extend `base.html` using `{% extends 'base.html' %}` and `{% block content %}`.
- Every `<form method="POST">` must have `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Use `url_for()` for all internal links and static file references — never hardcode paths.
- Do not break existing Jinja2 block structure or `url_for()` calls.
- `get_effective_role` and `can_edit_book` are available in all templates via context processor.

### CSS
- Use CSS custom properties (`var(--color-*)`) from `:root` — do not introduce hex colors inline.
- Add new styles to `static/style.css`. Do not create additional CSS files unless explicitly requested.
- Keep mobile responsiveness intact — test all changes at ≥ 320 px viewport width.

### Security — Never regress these
- `secure_filename()` must wrap every user-provided filename.
- `allowed_file()` must validate every upload.
- `generate_password_hash()` / `check_password_hash()` must be used for all passwords.
- Do not echo raw user input into HTML without Jinja2 auto-escaping.
- All permission checks must be enforced **server-side** (not just hidden in the UI).

### Git / File hygiene
- Never commit `.env` (it is in `.gitignore`).
- Never commit `database.db` to version control.
- Do not modify `get-pip.py` — it is a vendored pip bootstrapper.

---

## 13. Known Issues & Status

| Priority | Issue | Status |
|---|---|---|
| ✅ Fixed | `ratings` table missing → `/kitap/<id>` returned 500 | Fixed |
| ✅ Fixed | `debug=True` hardcoded → RCE risk in production | Now reads `FLASK_DEBUG` env var |
| ✅ Fixed | `secure_filename()` missing in profile picture upload | Fixed |
| ✅ Fixed | Google OAuth users created with empty password hash | Fixed with `secrets.token_hex(32)` |
| ✅ Fixed | CSRF protection absent on all forms | `Flask-WTF` CSRFProtect added |
| ✅ Fixed | DB connection leak in `kitap_detay()` early returns | Wrapped in `try/finally` |
| ✅ Fixed | No pagination on `/kitaplar` and `/arama_sonuc` | `LIMIT/OFFSET` with 12 items/page |
| ✅ Fixed | Book cover upload was URL-only text input | File upload added to `kitap_ekle` |
| ✅ Fixed | `before_request` hit DB on every request | Session cache used, DB only as fallback |
| ✅ Fixed | Single `is_admin` flag — no role system | 4-tier role system implemented (`user`, `author`, `admin`, `owner`) |
| ✅ Fixed | No way to grant/revoke roles from UI | Admin panel + Owner panel implemented |
| ✅ Fixed | `g.user` was a tuple with no email/role | Converted to a **dict** with `id`, `first_name`, `last_name`, `is_admin`, `email`, `role` |
| ✅ Fixed | No permission checks on book edit/delete | `can_edit_book()` enforced both server-side and in UI |
| ✅ Fixed | No comment deletion | Admin/owner can delete comments via `/kitap/<id>/yorum/<cid>/sil` |
| ✅ Fixed | Uploaded files stored with OS filesystem paths | Now stored as web-accessible `/static/uploads/...` paths |
| ✅ Fixed | Book covers stored in `static/images/` (mixed with app images) | Now in `static/uploads/books/` |
| ✅ Fixed | No book edit page | `/kitap/<id>/duzenle` + shared `kitap_ekle.html` template with `is_edit` flag |
| ✅ Fixed | No book delete route | `/kitap/<id>/sil` with cascade delete of comments and ratings |
| ✅ Fixed | No "Become an Author" flow | `/yazar-ol` route + `author_requests` table + profile UI |
| ✅ Fixed | Hardcoded Google OAuth credentials in source code | Removed — `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` must be set in `.env` |
| ✅ Fixed | No session cookie security flags | `HttpOnly`, `Secure` (env-aware), `SameSite=Lax` configured in `app.config` |
| ✅ Fixed | No server-side rating bounds validation | Explicit `1 <= rating <= 5` check with Turkish flash message |
| ✅ Fixed | No custom error pages | `404.html` and `500.html` templates + handlers added |
| ✅ Fixed | No rate limiting on auth routes | `Flask-Limiter`: 10/min on `/login`, 5/min on `/register`, Turkish 429 flash |
| ✅ Fixed | Email enumeration on registration | Duplicate-email flash changed to generic message |
| ✅ Fixed | HTTP security headers missing | `Flask-Talisman` adds CSP, X-Frame-Options: DENY, HSTS, X-Content-Type-Options |
| ✅ Fixed | No input length limits | Server-side max-length validation on all user-submitted text fields |
| ✅ Fixed | Footer links pointing to `#` | `/hakkimizda`, `/iletisim`, `/gizlilik` pages created; footer links updated |

---

## 14. Dependencies (`requirements.txt`)

| Package | Purpose |
|---|---|
| `Flask==3.1.2` | Web framework |
| `Flask-WTF` | CSRF protection (`CSRFProtect`) |
| `Flask-Limiter` | Rate limiting on auth routes (`/login`, `/register`) |
| `Flask-Talisman` | HTTP security headers (CSP, X-Frame-Options, HSTS, etc.) |
| `Werkzeug` | Password hashing, `secure_filename`, WSGI utilities |
| `authlib` | Google OAuth 2.0 (`OAuth` client) |
| `requests` | HTTP client (used internally by authlib) |
| `itsdangerous` | Session signing (Flask dependency) |
| `Jinja2` | Templating engine (Flask dependency) |
| `click` | CLI utilities (Flask dependency) |
| `python-dotenv` | Loads `.env` into `os.getenv()` |
| `gunicorn` | Production WSGI server |

---

## 15. Verification Checklist

After any change, mentally (or actually) verify:

- [ ] `http://127.0.0.1:5000/` renders without errors
- [ ] `http://127.0.0.1:5000/kitaplar` shows book cards with pagination
- [ ] `http://127.0.0.1:5000/kitap/1` opens without 500 error; ratings and comments work
- [ ] `/login` accepts valid credentials and rejects invalid ones
- [ ] `/register` creates a new user and redirects to `/login`
- [ ] `/profile` updates name/phone; profile picture upload works; role badge shown
- [ ] Standard user sees "Yazar Ol" button on profile; after clicking, sees "Talebiniz inceleniyor"
- [ ] `/admin` is accessible for admin/owner; blocked for user/author/guest
- [ ] Pending author request shows in `/admin`; Approve/Reject work correctly
- [ ] After approval, user's role becomes `author` and they can add books
- [ ] `/admin/kullanicilar` is accessible for owner only; admin gets flash error
- [ ] Owner can promote/demote admin from users panel; owner row shows no action button
- [ ] Author can edit/delete their own book; blocked from editing another author's book
- [ ] Admin can edit/delete any book; Admin can delete any comment
- [ ] Author cannot see comment delete button; direct POST to comment-delete route is blocked
- [ ] `/kitap_ekle` requires `author`+ role; redirects with flash for lower roles
- [ ] Book add form: live cover preview works, char counter works, genre select works
- [ ] Uploaded book covers render as actual images (not raw paths)
- [ ] Uploaded profile pictures render as actual images
- [ ] All POST forms include CSRF token and are rejected without it
- [ ] No raw SQL string formatting (no f-strings in queries)
- [ ] `FLASK_DEBUG` is read from environment, not hardcoded
- [ ] Mobile layout is not broken (≥ 320 px)

---

*Last updated: 2026-07-02 — Dokushoka*
