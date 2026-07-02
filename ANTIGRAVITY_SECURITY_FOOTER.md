# ANTIGRAVITY PROMPT — Dokushoka: Security Hardening + Footer Pages

Read `AGENTS.md` completely before writing a single line of code.
Execute the steps below in order. After each step, list every file you
modified and briefly explain what changed.

> ⚠️ LANGUAGE RULE: All code, comments, variable names, and documentation
> must be in English. All user-facing UI strings (flash messages, button
> labels, page titles, error messages) must remain in Turkish, consistent
> with the existing codebase style.

---

## STEP 1 — SECURITY: Rate Limiting on Auth Routes

Install `Flask-Limiter` and add it to `requirements.txt`.

Configure it in `app.py`:

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)
```

Apply limits to the following routes only (do not apply a global limit):

```python
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    ...

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    ...
```

Add a custom error handler so the rate-limit response returns a
Turkish flash message instead of a raw HTTP 429:

```python
@app.errorhandler(429)
def ratelimit_error(e):
    flash('Çok fazla istek gönderdiniz. Lütfen bir dakika bekleyin.', 'danger')
    return redirect(url_for('login')), 429
```

**Acceptance criterion:** Sending more than 10 POST requests to `/login`
within 60 seconds results in a 429 response with the Turkish flash
message above.

---

## STEP 2 — SECURITY: Fix Email Enumeration on Registration

In `register()`, the current response leaks whether an email is already
registered. Change the flash message to a generic one that reveals
nothing about existing accounts:

```python
# BEFORE (leaks information):
flash('Bu e-posta zaten kayıtlı.', 'danger')

# AFTER (generic, reveals nothing):
flash('Kayıt tamamlanamadı. Lütfen bilgilerinizi kontrol edin.', 'danger')
```

Do not change the registration logic itself — only the user-facing
message when a duplicate email is detected.

---

## STEP 3 — SECURITY: Session Cookie Security Flags

Add the following to `app.py` immediately after `app.config` is set up:

```python
app.config.update(
    SESSION_COOKIE_SECURE=True,      # only transmit over HTTPS
    SESSION_COOKIE_HTTPONLY=True,    # inaccessible to JavaScript
    SESSION_COOKIE_SAMESITE='Lax',  # CSRF mitigation at browser level
)
```

**Note:** `SESSION_COOKIE_SECURE=True` will cause sessions to break on
plain HTTP (local development without TLS). To avoid this, read it from
the environment:

```python
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', 'production') != 'development'
```

Add `FLASK_ENV=development` to `.env.example` with a comment explaining
this flag. The production server's `.env` must NOT set this variable
(so it defaults to `'production'` and the Secure flag is active).

---

## STEP 4 — SECURITY: HTTP Security Headers

Install `Flask-Talisman` and add it to `requirements.txt`.

Add it to `app.py` after all `app.config` setup is complete:

```python
from flask_talisman import Talisman

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
    content_type_options=True,   # X-Content-Type-Options: nosniff
)
```

After adding Talisman, verify all pages still load correctly (CSS,
fonts, and JavaScript should not be blocked by the CSP). If any
resource is blocked, add its origin to the appropriate CSP directive.

**Acceptance criterion:** A response from any page must include the
headers `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
and a `Content-Security-Policy` header.

---

## STEP 5 — SECURITY: Rating Value Validation

In `kitap_detay()`, the rating value from the form is cast to `int`
but never validated against the allowed range. Add an explicit check:

```python
rating = int(request.form.get('rating', 0))
if not (1 <= rating <= 5):
    flash('Geçersiz puan değeri.', 'danger')
    return redirect(url_for('kitap_detay', book_id=book_id))
```

---

## STEP 6 — SECURITY: Input Length Limits

Add server-side length validation to all user-submitted text fields.
Do this consistently across `register()`, `login()`, `profile()`,
`kitap_ekle()`, and `kitap_detay()` (comment submission):

```python
# Example pattern — adapt field names to match the actual form fields:
if len(request.form.get('first_name', '')) > 50:
    flash('İsim en fazla 50 karakter olabilir.', 'danger')
    return redirect(url_for('register'))
```

Recommended limits:
- `first_name`, `last_name`: 50 characters
- `email`: 254 characters (RFC 5321 maximum)
- `phone`: 20 characters
- `comment`: 1000 characters
- Book `title`: 200 characters
- Book `author`: 100 characters
- Book `description`: 5000 characters

---

## STEP 7 — FOOTER: Create Missing Static Pages

Currently, the footer links for "Hakkımızda", "İletişim", and
"Gizlilik Politikası" all point to `#` (nowhere). Create real pages
and routes for each.

### 7.1 About Page (`/hakkimizda`)

Create `templates/hakkimizda.html` extending `base.html`.

The page must include:
- A heading: "Hakkımızda"
- A short paragraph about Burak Kırlak and the Dokushoka project:
  write it as a brief, professional paragraph in Turkish explaining
  who built this platform and what it is for (Japanese-literature-
  themed book discovery and review platform, built as a personal
  project). Keep it concise — 2–3 short paragraphs.
- The page should match the existing Japanese design aesthetic
  (washi background, Shippori Mincho headings, correct color palette).

Add the route to `app.py`:
```python
@app.route('/hakkimizda')
def hakkimizda():
    return render_template('hakkimizda.html')
```

### 7.2 Contact Page (`/iletisim`)

Create `templates/iletisim.html` extending `base.html`.

The page must include:
- A heading: "İletişim"
- The following contact details, each as a clickable link:
  - Email: burakhankirlak@outlook.com → `<a href="mailto:burakhankirlak@outlook.com">`
  - GitHub: https://github.com/BurakKIRLAK
  - Instagram: https://www.instagram.com/hankirlak/
  - LinkedIn: https://www.linkedin.com/in/burak-kirlak-6a8022372/
- Use appropriate Font Awesome 6.5 icons for each link
  (fa-envelope, fa-brands fa-github, fa-brands fa-instagram,
  fa-brands fa-linkedin), consistent with the existing icon strategy
  in AGENTS.md.
- No Twitter/X link anywhere on this page or in the footer.

Add the route to `app.py`:
```python
@app.route('/iletisim')
def iletisim():
    return render_template('iletisim.html')
```

### 7.3 Privacy Policy Page (`/gizlilik`)

Create `templates/gizlilik.html` extending `base.html`.

The page must include:
- A heading: "Gizlilik Politikası"
- A clear, honest privacy policy written in Turkish covering:
  - What data is collected (name, email, profile picture, comments,
    ratings)
  - How it is stored (SQLite database, on a private server)
  - Whether it is shared with third parties (it is not, except for
    Google OAuth which is used for login and is governed by Google's
    own privacy policy)
  - User rights (they can delete their account by contacting
    burakhankirlak@outlook.com)
  - Cookie usage (only session cookies, no tracking cookies)
- Keep it legally honest but not unnecessarily long.

Add the route to `app.py`:
```python
@app.route('/gizlilik')
def gizlilik():
    return render_template('gizlilik.html')
```

---

## STEP 8 — FOOTER: Update Footer Links and Social Icons

In `base.html` (or wherever the footer is defined), update the footer
section so that:

1. "Hakkımızda" links to `{{ url_for('hakkimizda') }}`
2. "İletişim" links to `{{ url_for('iletisim') }}`
3. "Gizlilik Politikası" links to `{{ url_for('gizlilik') }}`
4. The Twitter/X icon and link are **completely removed**
5. The social media icons (if present in the footer) are updated to:
   - GitHub: https://github.com/BurakKIRLAK
   - Instagram: https://www.instagram.com/hankirlak/
   - LinkedIn: https://www.linkedin.com/in/burak-kirlak-6a8022372/
6. All footer links use `url_for()` — no hardcoded paths.

---

## STEP 9 — UPDATE AGENTS.md

After completing all steps above, update `AGENTS.md` to reflect:
- New routes added: `/hakkimizda`, `/iletisim`, `/gizlilik`
- New dependencies added: `Flask-Limiter`, `Flask-Talisman`
- New environment variable: `FLASK_ENV`
- Move the following items from "open" to "Fixed" in the Known Issues
  table (or add them as new Fixed rows):
  - Rate limiting on auth routes
  - Email enumeration on registration
  - Session cookie security flags
  - HTTP security headers (CSP, X-Frame-Options, etc.)
  - Rating value range validation
  - Input length limits
  - Footer links pointing to `#`

---

## ACCEPTANCE CHECKLIST

- [ ] POST to `/login` more than 10 times/minute results in 429 + Turkish flash message
- [ ] POST to `/register` more than 5 times/minute results in 429
- [ ] Registering with an existing email shows a generic error (no "already registered" wording)
- [ ] All response headers include `X-Frame-Options: DENY` and `X-Content-Type-Options: nosniff`
- [ ] A `Content-Security-Policy` header is present on all responses
- [ ] All pages still load CSS, fonts, and JS correctly after Talisman is added
- [ ] Submitting a rating outside 1–5 is rejected with a Turkish flash message
- [ ] Submitting a comment over 1000 characters is rejected
- [ ] `/hakkimizda` renders correctly with content about Burak Kırlak and Dokushoka
- [ ] `/iletisim` renders correctly with email, GitHub, Instagram, LinkedIn links (no Twitter/X)
- [ ] `/gizlilik` renders correctly with a honest Turkish privacy policy
- [ ] All three footer links now navigate to real pages (not `#`)
- [ ] Twitter/X icon and link are completely gone from the footer
- [ ] All social links in footer point to the correct URLs listed in Step 8
- [ ] AGENTS.md is updated with new routes, dependencies, and fixed issues
- [ ] `requirements.txt` includes `Flask-Limiter` and `Flask-Talisman`
- [ ] No existing tests or functionality is broken by these changes

Verify each item individually and report the results.
