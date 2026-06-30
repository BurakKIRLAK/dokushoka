# Dokushoka

**Dokushoka** is a Japanese-literature-themed book discovery and review platform built with Flask (Python). Users can browse books, write comments, give star ratings, and request author status. A 4-tier role system (user → author → admin → owner) controls access to all write operations.

> **UI language:** Turkish (all user-facing text, flash messages, and labels are in Turkish).

---

## Features

- User registration and login (email/password + Google OAuth)
- Secure password hashing (Werkzeug pbkdf2)
- 4-tier role system: `user`, `author`, `admin`, and `owner`
- "Become an Author" request flow with admin approval
- Book listing with pagination (12 per page)
- Book detail pages with comments and 1–5 star ratings
- Book add/edit/delete with file upload for cover images
- Search with autocomplete (title and author name)
- User profile page with profile picture upload
- Admin panel (author request management, author revocation)
- Owner-only all-users panel (promote/demote admin)
- CSRF protection on all forms (Flask-WTF)
- Custom 404 and 500 error pages

---

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask 3.1.x |
| Frontend | Jinja2 templates, Vanilla CSS, Vanilla JS |
| Database | SQLite (`database.db`, file-based) |
| Auth | Flask session + Werkzeug password hashing + Google OAuth (Authlib) |
| CSRF | Flask-WTF CSRFProtect |
| File uploads | Werkzeug `secure_filename`, saved to `static/uploads/` |
| Deployment | Gunicorn (production), Docker / docker-compose |
| Dev tooling | python-dotenv, venv |

---

## Role / Permission System

| Role | Stored as | Permissions |
|---|---|---|
| **Guest** | No session | View books and search |
| **user** | `users.role = 'user'` | View + comment + star rating + request author status |
| **author** | `users.role = 'author'` | user perms + add/edit/delete **own** books |
| **admin** | `users.role = 'admin'` | author perms + edit/delete **any** book + delete **any** comment + manage author requests |
| **owner** | Not in DB — matched against `OWNER_EMAIL` env var | All admin perms + promote/demote admin (owner-only panel) |

> **Owner is NOT a database role.** Owner status is computed dynamically on every request by comparing the logged-in user's email (case-insensitive) against the `OWNER_EMAIL` environment variable. It cannot be changed or lost via any UI action.

---

## Database Schema

Tables are created automatically by `init_db()` on app startup. Old `is_admin=1` records are migrated to `role='admin'` idempotently.

```sql
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT UNIQUE NOT NULL,
    phone       TEXT,
    password    TEXT NOT NULL,       -- Werkzeug pbkdf2 hash
    is_admin    INTEGER DEFAULT 0,   -- Legacy; do not use for new logic
    profile_pic TEXT DEFAULT '/static/images/default.jpg',
    role        TEXT DEFAULT 'user' CHECK(role IN ('user','author','admin'))
);

CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    author       TEXT NOT NULL,
    description  TEXT,
    image_url    TEXT,
    added_by     INTEGER REFERENCES users(id),
    genre        TEXT,
    publish_year INTEGER,
    page_count   INTEGER,
    publisher    TEXT,
    isbn         TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER,
    user_id INTEGER,
    comment TEXT,
    FOREIGN KEY(book_id) REFERENCES books(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ratings (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    rating  INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(book_id) REFERENCES books(id),
    UNIQUE(user_id, book_id)
);

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

---

## Environment Variables

Copy `.env.example` to `.env` and fill in real values. **Never commit `.env`.**

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session signing key. Use a long random string in production. |
| `GOOGLE_CLIENT_ID` | Yes | Google OAuth 2.0 client ID (from Google Cloud Console). |
| `GOOGLE_CLIENT_SECRET` | Yes | Google OAuth 2.0 client secret. |
| `FLASK_DEBUG` | No | Set to `"true"` for local development only. **Must be `"False"` in production.** |
| `OWNER_EMAIL` | Yes | The email address that permanently holds owner status. |

> `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` have **no hardcoded fallbacks**. Google OAuth will be unavailable if these are missing.

---

## Local Setup (virtualenv)

```bash
# 1. Clone the repository
git clone https://github.com/BurakKIRLAK/dokushoka.git
cd dokushoka

# 2. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\activate       # Windows PowerShell
# source venv/bin/activate    # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
copy .env.example .env        # Windows
# cp .env.example .env        # macOS / Linux
# Edit .env and fill in all required values (especially OWNER_EMAIL and Google OAuth credentials)

# 5. Run the development server
python app.py
# → Open http://127.0.0.1:5000
```

> **Production note:** Set `FLASK_DEBUG=False` in `.env`. The `SESSION_COOKIE_SECURE` flag is automatically enabled when `FLASK_DEBUG` is not `"true"`, so the app must be served over HTTPS in production.

---

## Docker Setup

### Development (with hot-reload)

```bash
cp .env.example .env
# Edit .env with real values

docker compose up
# → Open http://127.0.0.1:5000

docker compose down  # to stop
```

### Production

For production, edit `docker-compose.yml` to remove the `command:` and `volumes:` overrides so the Dockerfile's Gunicorn `CMD` is used instead:

```bash
docker compose up -d
```

The production image uses `python:3.12-slim` with Gunicorn as the WSGI server.

---

## File Upload Details

| Upload type | Save path | Web path stored in DB |
|---|---|---|
| Profile picture | `static/uploads/user_<id>_<filename>` | `/static/uploads/<filename>` |
| Book cover | `static/uploads/books/book_<id>_<filename>` | `/static/uploads/books/<filename>` |

- Max file size: **5 MB** (`MAX_CONTENT_LENGTH`)
- Allowed extensions: `png`, `jpg`, `jpeg`, `gif`, `webp`
- All filenames are sanitized with `secure_filename()` before saving

---

## Security Notes

- All POST forms include a CSRF token (Flask-WTF `CSRFProtect`)
- Session cookies are configured with `HttpOnly=True`, `SameSite=Lax`, and `Secure=True` (in production)
- Passwords are hashed with Werkzeug's pbkdf2 implementation
- All SQL queries use parameterized `?` placeholders (no string interpolation)
- Permission checks are enforced server-side on every route, not just in the UI
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` must be provided via environment — no hardcoded fallbacks exist

---

## Project Structure

```
dokushoka/
├── app.py                 # All routes and business logic (single-file backend)
├── requirements.txt       # Python dependencies
├── Dockerfile             # Production image
├── docker-compose.yml     # Development and production compose config
├── .env.example           # Environment variable template (safe to commit)
├── AGENTS.md              # AI agent guidance (single source of truth for AI tools)
├── static/
│   ├── style.css          # All CSS (CSS custom properties, Japanese aesthetic)
│   ├── images/            # Static images (logo, defaults)
│   └── uploads/           # Runtime upload directory (created automatically)
│       └── books/         # Book cover uploads
└── templates/             # Jinja2 HTML templates
    ├── base.html          # Master layout
    ├── index.html         # Homepage
    ├── kitaplar.html      # Paginated book listing
    ├── kitap_detay.html   # Book detail + comments + ratings
    ├── kitap_ekle.html    # Add / edit book form
    ├── admin.html         # Admin panel
    ├── kullanicilar.html  # Owner-only all-users panel
    ├── login.html         # Login page
    ├── register.html      # Registration page
    ├── profile.html       # User profile
    ├── arama_sonuc.html   # Search results
    ├── 404.html           # Custom 404 error page
    └── 500.html           # Custom 500 error page
```

---

## Design System

The UI follows a **Japanese / East Asian aesthetic** with the following design tokens:

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

**Typography:**
- Headings: `Shippori Mincho` (Google Fonts)
- Body: `Noto Sans JP` (Google Fonts)

**Icons:** Font Awesome 6.5 (CDN)

---

## Developer Notes

- All business logic is in `app.py` (single-file backend). Do not split into blueprints without a compelling reason.
- Do not introduce PostgreSQL, MySQL, or an ORM. The project uses raw `sqlite3` with `?` placeholders throughout.
- All new code must use `get_effective_role(g.user)` for permission checks — never check `g.user['is_admin']` directly.
- See `AGENTS.md` for full AI agent guidance and architecture documentation.

---

## AI Assistance Disclosure

This project was developed with AI coding assistance for architecture design, debugging, and code review. All code has been reviewed, understood, and adapted for the project's specific requirements by the developer. The overall architecture, data flow, and security design decisions were made consciously.

---

*Developed by Burak Kırlak — Computer Engineering Student*  
*GitHub: https://github.com/BurakKIRLAK*
