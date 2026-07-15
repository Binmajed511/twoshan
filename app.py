# app.py
import os
import re
import sqlite3
import uuid
import threading
import time
import urllib.request
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, jsonify, abort, g)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, 
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
app.secret_key = os.environ.get('SECRET_KEY', 'twoshan-secret-key-2024')
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

ALLOWED_IMAGE = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_VIDEO = {'mp4', 'mov', 'avi', 'webm'}
ALLOWED_MEDIA = ALLOWED_IMAGE | ALLOWED_VIDEO

DATABASE = os.path.join(BASE_DIR, 'twoshan.db')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db:
        db.close()

def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv

def mutate(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

# ─── Schema ──────────────────────────────────────────────────────────────────

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        handle TEXT NOT NULL UNIQUE,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        bio TEXT DEFAULT '',
        avatar TEXT DEFAULT '',
        banner TEXT DEFAULT '',
        verified INTEGER DEFAULT 0,
        vip INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        is_blocked INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS twoshans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        media_url TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        is_pinned INTEGER DEFAULT 0,
        reply_to_id INTEGER DEFAULT NULL,
        retwoshan_from_id INTEGER DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(reply_to_id) REFERENCES twoshans(id),
        FOREIGN KEY(retwoshan_from_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS likes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        twoshan_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, twoshan_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(twoshan_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        twoshan_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, twoshan_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(twoshan_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS retwoshans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        twoshan_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, twoshan_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(twoshan_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS follows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        follower_id INTEGER NOT NULL,
        followed_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(follower_id, followed_id),
        FOREIGN KEY(follower_id) REFERENCES users(id),
        FOREIGN KEY(followed_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        content TEXT DEFAULT '',
        media_url TEXT DEFAULT '',
        media_type TEXT DEFAULT '',
        is_read INTEGER DEFAULT 0,
        is_deleted_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(sender_id) REFERENCES users(id),
        FOREIGN KEY(receiver_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        twoshan_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        expires_at TEXT DEFAULT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(twoshan_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS poll_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        FOREIGN KEY(poll_id) REFERENCES polls(id)
    );
    CREATE TABLE IF NOT EXISTS poll_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        poll_option_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, poll_option_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(poll_option_id) REFERENCES poll_options(id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        from_user_id INTEGER NOT NULL,
        twoshan_id INTEGER DEFAULT NULL,
        message TEXT DEFAULT '',
        is_read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(from_user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL,
        twoshan_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(reporter_id) REFERENCES users(id),
        FOREIGN KEY(twoshan_id) REFERENCES twoshans(id)
    );
    CREATE TABLE IF NOT EXISTS blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        blocker_id INTEGER NOT NULL,
        blocked_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(blocker_id, blocked_id),
        FOREIGN KEY(blocker_id) REFERENCES users(id),
        FOREIGN KEY(blocked_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS mutes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        muter_id INTEGER NOT NULL,
        muted_id INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(muter_id, muted_id),
        FOREIGN KEY(muter_id) REFERENCES users(id),
        FOREIGN KEY(muted_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS hashtags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag TEXT NOT NULL UNIQUE,
        use_count INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS twoshan_hashtags (
        twoshan_id INTEGER NOT NULL,
        hashtag_id INTEGER NOT NULL,
        PRIMARY KEY(twoshan_id, hashtag_id)
    );
    CREATE TABLE IF NOT EXISTS twoshan_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        twoshan_id INTEGER NOT NULL,
        viewer_id INTEGER,
        viewer_ip TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(twoshan_id, viewer_id, viewer_ip)
    );
    CREATE TABLE IF NOT EXISTS contact_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT,
        message TEXT NOT NULL,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db.commit()
    # أضف الأعمدة الجديدة إذا لم تكن موجودة
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if 'ban_reason' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT NULL")
    if 'ban_scheduled_at' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN ban_scheduled_at TEXT DEFAULT NULL")
    db.commit()
    # أضف عمود last_seen إذا لم يكن موجوداً (للقواعد القديمة)
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if 'last_seen' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN last_seen TEXT DEFAULT NULL")
        db.commit()

    # Seed admin
    existing = db.execute("SELECT id FROM users WHERE handle='admin'").fetchone()
    if not existing:
        ph = generate_password_hash('Admin@Twoshan2024!')
        db.execute("""INSERT INTO users (username,handle,email,password_hash,bio,verified,is_admin)
                      VALUES (?,?,?,?,?,1,1)""",
                   ('مشرف توشن', 'admin', 'admin@twoshan.com', ph,
                    'حساب الإدارة الرسمي لمنصة توشن'))
        db.commit()

# ─── Online status ───────────────────────────────────────────────────────────

ONLINE_THRESHOLD = 1 * 60  # دقيقة واحدة

def is_online(last_seen_str):
    if not last_seen_str:
        return False
    try:
        ls = datetime.strptime(last_seen_str, '%Y-%m-%d %H:%M:%S')
        return (datetime.utcnow() - ls).total_seconds() < ONLINE_THRESHOLD
    except Exception:
        return False

@app.before_request
def update_last_seen():
    if 'user_id' in session and request.endpoint not in ('static', 'record_view', 'keepalive_ping', '_track_activity'):
        uid = session['user_id']
        mutate("UPDATE users SET last_seen=datetime('now') WHERE id=?", [uid])
        # تنفيذ الحظر المجدول إذا حان وقته
        u = query("SELECT is_blocked, ban_scheduled_at FROM users WHERE id=?", [uid], one=True)
        if u and not u['is_blocked'] and u['ban_scheduled_at']:
            try:
                scheduled = datetime.strptime(u['ban_scheduled_at'], '%Y-%m-%d %H:%M:%S')
                if datetime.utcnow() >= scheduled:
                    mutate("UPDATE users SET is_blocked=1, ban_scheduled_at=NULL WHERE id=?", [uid])
                    session.clear()
            except Exception:
                pass

# ─── Auth helpers ─────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            flash('يرجى تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('login'))
        return f(*a, **kw)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        u = query("SELECT is_admin FROM users WHERE id=?", [session['user_id']], one=True)
        if not u or not u['is_admin']:
            abort(403)
        return f(*a, **kw)
    return dec

def current_user():
    if 'user_id' in session:
        return query("SELECT * FROM users WHERE id=?", [session['user_id']], one=True)
    return None

@app.context_processor
def inject_globals():
    u = current_user()
    unread = 0
    if u:
        row = query("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0", [u['id']], one=True)
        unread = row['c'] if row else 0
    trends = query("""SELECT h.tag, h.use_count FROM hashtags h
                      ORDER BY h.use_count DESC LIMIT 8""")
    return dict(current_user=u, unread_count=unread, trends=trends)

# ─── Utilities ───────────────────────────────────────────────────────────────

def allowed_file(filename, types=None):
    t = types or ALLOWED_MEDIA
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in t

def save_upload(file):
    if not file or file.filename == '':
        return None, None
    if not allowed_file(file.filename):
        return None, None
    ext = file.filename.rsplit('.', 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], fname))
    mtype = 'video' if ext in ALLOWED_VIDEO else 'image'
    return fname, mtype

def process_hashtags(content, twoshan_id):
    tags = set(re.findall(r'#(\w+)', content))
    for tag in tags:
        tag_lower = tag.lower()
        existing = query("SELECT id FROM hashtags WHERE tag=?", [tag_lower], one=True)
        if existing:
            mutate("UPDATE hashtags SET use_count=use_count+1 WHERE id=?", [existing['id']])
            hid = existing['id']
        else:
            hid = mutate("INSERT INTO hashtags (tag, use_count) VALUES (?,1)", [tag_lower])
        try:
            mutate("INSERT INTO twoshan_hashtags VALUES (?,?)", [twoshan_id, hid])
        except Exception:
            pass

def linkify(content):
    content = re.sub(r'#(\w+)', r'<a href="/hashtag/\1" class="hashtag-link">#\1</a>', content)
    content = re.sub(r'@(\w+)', r'<a href="/profile/\1" class="mention-link">@\1</a>', content)
    return content

def add_notification(user_id, ntype, from_user_id, twoshan_id=None, message=''):
    if user_id == from_user_id:
        return
    mutate("""INSERT INTO notifications (user_id,type,from_user_id,twoshan_id,message)
              VALUES (?,?,?,?,?)""", [user_id, ntype, from_user_id, twoshan_id, message])

def get_twoshan_extras(twoshan_id, viewer_id):
    likes = query("SELECT COUNT(*) as c FROM likes WHERE twoshan_id=?", [twoshan_id], one=True)['c']
    reposts = query("SELECT COUNT(*) as c FROM retwoshans WHERE twoshan_id=?", [twoshan_id], one=True)['c']
    replies = query("SELECT COUNT(*) as c FROM twoshans WHERE reply_to_id=?", [twoshan_id], one=True)['c']
    views = query("SELECT COUNT(*) as c FROM twoshan_views WHERE twoshan_id=?", [twoshan_id], one=True)['c']
    liked = False
    bookmarked = False
    retwoshan_ed = False
    if viewer_id:
        liked = bool(query("SELECT 1 FROM likes WHERE user_id=? AND twoshan_id=?", [viewer_id, twoshan_id], one=True))
        bookmarked = bool(query("SELECT 1 FROM bookmarks WHERE user_id=? AND twoshan_id=?", [viewer_id, twoshan_id], one=True))
        retwoshan_ed = bool(query("SELECT 1 FROM retwoshans WHERE user_id=? AND twoshan_id=?", [viewer_id, twoshan_id], one=True))
    return dict(likes=likes, reposts=reposts, replies=replies, views=views,
                liked=liked, bookmarked=bookmarked, retwoshan_ed=retwoshan_ed)

def enrich_twoshans(rows, viewer_id):
    results = []
    for t in rows:
        t = dict(t)
        extras = get_twoshan_extras(t['id'], viewer_id)
        t.update(extras)
        t['content_html'] = linkify(t['content'])
        # author
        author = query("SELECT * FROM users WHERE id=?", [t['user_id']], one=True)
        t['author'] = dict(author) if author else {}
        t['author']['online'] = is_online(t['author'].get('last_seen'))
        # if retwoshan
        if t.get('retwoshan_from_id'):
            orig = query("""SELECT t.*, u.username, u.handle, u.avatar, u.verified, u.vip
                            FROM twoshans t JOIN users u ON t.user_id=u.id
                            WHERE t.id=?""", [t['retwoshan_from_id']], one=True)
            t['original'] = dict(orig) if orig else None
        # poll
        poll = query("SELECT * FROM polls WHERE twoshan_id=?", [t['id']], one=True)
        if poll:
            options = query("SELECT po.*, (SELECT COUNT(*) FROM poll_votes WHERE poll_option_id=po.id) as votes FROM poll_options po WHERE po.poll_id=?", [poll['id']])
            total_votes = sum(o['votes'] for o in options)
            voted = None
            if viewer_id:
                v = query("""SELECT pv.poll_option_id FROM poll_votes pv 
                             JOIN poll_options po ON pv.poll_option_id=po.id 
                             WHERE pv.user_id=? AND po.poll_id=?""", [viewer_id, poll['id']], one=True)
                voted = v['poll_option_id'] if v else None
            t['poll'] = dict(poll)
            t['poll_options'] = [dict(o) for o in options]
            t['poll_total'] = total_votes
            t['poll_voted'] = voted
        results.append(t)
    return results

# ─── Routes: Auth ─────────────────────────────────────────────────────────────

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        handle = request.form.get('handle', '').strip().lstrip('@')
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if not all([username, handle, email, password]):
            flash('يرجى ملء جميع الحقول', 'danger')
            return render_template('register.html')
        if password != confirm:
            flash('كلمتا المرور غير متطابقتين', 'danger')
            return render_template('register.html')
        if len(password) < 6:
            flash('كلمة المرور يجب أن تكون 6 أحرف على الأقل', 'danger')
            return render_template('register.html')
        if not re.match(r'^[a-zA-Z0-9_]{3,20}$', handle):
            flash('المعرف يجب أن يكون من 3-20 حرف (أحرف، أرقام، _)', 'danger')
            return render_template('register.html')
        if query("SELECT id FROM users WHERE handle=?", [handle], one=True):
            flash('هذا المعرف مستخدم بالفعل', 'danger')
            return render_template('register.html')
        if query("SELECT id FROM users WHERE email=?", [email], one=True):
            flash('هذا البريد الإلكتروني مستخدم بالفعل', 'danger')
            return render_template('register.html')
        uid = mutate(
            "INSERT INTO users (username,handle,email,password_hash) VALUES (?,?,?,?)",
            [username, handle, email, generate_password_hash(password)]
        )
        session['user_id'] = uid
        flash(f'مرحباً بك في توشن يا {username}! 🎉', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        identifier = request.form.get('identifier', '').strip()
        password = request.form.get('password', '')
        u = query("SELECT * FROM users WHERE handle=? OR email=?", [identifier, identifier], one=True)
        if u and check_password_hash(u['password_hash'], password):
            if u['is_blocked']:
                reason = u['ban_reason'] or 'مخالفة سياسات المنصة'
                flash(f'حسابك محظور. السبب: {reason}', 'danger')
                return render_template('login.html')
            session['user_id'] = u['id']
            flash(f'أهلاً بعودتك {u["username"]}!', 'success')
            return redirect(url_for('index'))
        flash('بيانات الدخول غير صحيحة', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── Routes: Timeline ─────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    u = current_user()
    feed_type = request.args.get('feed', 'global')
    page = int(request.args.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page

    if feed_type == 'following':
        rows = query("""
            SELECT t.* FROM twoshans t
            JOIN follows f ON t.user_id = f.followed_id
            WHERE f.follower_id=? AND t.reply_to_id IS NULL
              AND t.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id=?)
              AND t.user_id NOT IN (SELECT muted_id FROM mutes WHERE muter_id=?)
            ORDER BY t.is_pinned DESC, t.created_at DESC
            LIMIT ? OFFSET ?""", [u['id'], u['id'], u['id'], per_page, offset])
    else:
        rows = query("""
            SELECT t.* FROM twoshans t
            WHERE t.reply_to_id IS NULL
              AND t.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id=?)
              AND t.user_id NOT IN (SELECT muted_id FROM mutes WHERE muter_id=?)
            ORDER BY t.is_pinned DESC, t.created_at DESC
            LIMIT ? OFFSET ?""", [u['id'], u['id'], per_page, offset])

    twoshans = enrich_twoshans(rows, u['id'])
    return render_template('index.html', twoshans=twoshans, feed_type=feed_type,
                           page=page, per_page=per_page)

# ─── Routes: Twoshan CRUD ─────────────────────────────────────────────────────

@app.route('/post', methods=['POST'])
@login_required
def post_twoshan():
    u = current_user()
    content = request.form.get('content', '').strip()
    reply_to = request.form.get('reply_to_id') or None
    poll_question = request.form.get('poll_question', '').strip()
    poll_options_raw = request.form.getlist('poll_option')
    poll_options = [o.strip() for o in poll_options_raw if o.strip()]

    if not content:
        flash('لا يمكن نشر توشة فارغة', 'danger')
        return redirect(request.referrer or url_for('index'))

    file = request.files.get('media')
    media_fname, media_type = save_upload(file)

    tid = mutate("""INSERT INTO twoshans (user_id, content, media_url, media_type, reply_to_id)
                    VALUES (?,?,?,?,?)""",
                 [u['id'], content, media_fname or '', media_type or '', reply_to])

    process_hashtags(content, tid)

    # mentions
    mentions = re.findall(r'@(\w+)', content)
    for handle in mentions:
        mu = query("SELECT id FROM users WHERE handle=?", [handle], one=True)
        if mu:
            add_notification(mu['id'], 'mention', u['id'], tid)

    if reply_to:
        orig = query("SELECT user_id FROM twoshans WHERE id=?", [reply_to], one=True)
        if orig:
            add_notification(orig['user_id'], 'reply', u['id'], tid)
        return redirect(url_for('twoshan_detail', tid=reply_to))

    if poll_question and len(poll_options) >= 2:
        pid = mutate("INSERT INTO polls (twoshan_id, question) VALUES (?,?)", [tid, poll_question])
        for opt in poll_options:
            mutate("INSERT INTO poll_options (poll_id, text) VALUES (?,?)", [pid, opt])

    flash('تم نشر توشتك! 🎉', 'success')
    return redirect(url_for('index'))

@app.route('/delete/<int:tid>', methods=['POST'])
@login_required
def delete_twoshan(tid):
    u = current_user()
    t = query("SELECT * FROM twoshans WHERE id=?", [tid], one=True)
    if not t:
        abort(404)
    if t['user_id'] != u['id'] and not u['is_admin']:
        abort(403)
    mutate("DELETE FROM twoshans WHERE id=?", [tid])
    flash('تم حذف التوشة', 'info')
    return redirect(request.referrer or url_for('index'))

@app.route('/twoshan/<int:tid>')
@login_required
def twoshan_detail(tid):
    u = current_user()
    t = query("SELECT * FROM twoshans WHERE id=?", [tid], one=True)
    if not t:
        abort(404)
    enriched = enrich_twoshans([t], u['id'])[0]
    replies_raw = query("""SELECT t.* FROM twoshans t
                           WHERE t.reply_to_id=?
                           ORDER BY t.created_at ASC""", [tid])
    replies = enrich_twoshans(replies_raw, u['id'])
    return render_template('twoshan_detail.html', twoshan=enriched, replies=replies)

@app.route('/report/<int:tid>', methods=['POST'])
@login_required
def report_twoshan(tid):
    u = current_user()
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('يرجى ذكر سبب التبليغ', 'warning')
        return redirect(request.referrer or url_for('index'))
    existing = query("SELECT id FROM reports WHERE reporter_id=? AND twoshan_id=?", [u['id'], tid], one=True)
    if not existing:
        mutate("INSERT INTO reports (reporter_id, twoshan_id, reason) VALUES (?,?,?)",
               [u['id'], tid, reason])
    flash('تم إرسال التبليغ للإدارة', 'success')
    return redirect(request.referrer or url_for('index'))

# ─── Routes: Likes / Bookmark / Retwoshan ─────────────────────────────────────

@app.route('/contact', methods=['POST'])
def contact_admin():
    name    = request.form.get('name', '').strip()
    email   = request.form.get('email', '').strip()
    message = request.form.get('message', '').strip()
    if not name or not message:
        return jsonify(ok=False, error='الاسم والرسالة مطلوبان'), 400
    mutate("INSERT INTO contact_messages (name,email,message) VALUES (?,?,?)",
           [name, email, message])
    return jsonify(ok=True)

@app.route('/admin/contact-messages')
@admin_required
def admin_contact_messages():
    msgs = query("SELECT * FROM contact_messages ORDER BY created_at DESC")
    mutate("UPDATE contact_messages SET is_read=1")
    return render_template('admin/contact_messages.html', msgs=msgs)

@app.route('/admin/contact-delete/<int:mid>', methods=['POST'])
@admin_required
def admin_contact_delete(mid):
    mutate("DELETE FROM contact_messages WHERE id=?", [mid])
    return redirect(url_for('admin_contact_messages'))

@app.route('/view/<int:tid>', methods=['POST'])
def record_view(tid):
    viewer_id = session.get('user_id')
    ip = request.remote_addr
    if viewer_id:
        mutate("INSERT OR IGNORE INTO twoshan_views (twoshan_id,viewer_id,viewer_ip) VALUES (?,?,?)",
               [tid, viewer_id, ip])
    else:
        mutate("INSERT OR IGNORE INTO twoshan_views (twoshan_id,viewer_ip) VALUES (?,?)",
               [tid, ip])
    count = query("SELECT COUNT(*) as c FROM twoshan_views WHERE twoshan_id=?", [tid], one=True)['c']
    return jsonify(ok=True, count=count)

@app.route('/like/<int:tid>', methods=['POST'])
@login_required
def like(tid):
    u = current_user()
    existing = query("SELECT id FROM likes WHERE user_id=? AND twoshan_id=?", [u['id'], tid], one=True)
    if existing:
        mutate("DELETE FROM likes WHERE user_id=? AND twoshan_id=?", [u['id'], tid])
        liked = False
    else:
        mutate("INSERT OR IGNORE INTO likes (user_id, twoshan_id) VALUES (?,?)", [u['id'], tid])
        liked = True
        t = query("SELECT user_id FROM twoshans WHERE id=?", [tid], one=True)
        if t:
            add_notification(t['user_id'], 'like', u['id'], tid)
    count = query("SELECT COUNT(*) as c FROM likes WHERE twoshan_id=?", [tid], one=True)['c']
    return jsonify(liked=liked, count=count)

@app.route('/bookmark/<int:tid>', methods=['POST'])
@login_required
def bookmark(tid):
    u = current_user()
    existing = query("SELECT id FROM bookmarks WHERE user_id=? AND twoshan_id=?", [u['id'], tid], one=True)
    if existing:
        mutate("DELETE FROM bookmarks WHERE user_id=? AND twoshan_id=?", [u['id'], tid])
        bookmarked = False
    else:
        mutate("INSERT OR IGNORE INTO bookmarks (user_id, twoshan_id) VALUES (?,?)", [u['id'], tid])
        bookmarked = True
    count = query("SELECT COUNT(*) as c FROM bookmarks WHERE twoshan_id=?", [tid], one=True)['c']
    return jsonify(bookmarked=bookmarked, count=count)

@app.route('/retwoshan/<int:tid>', methods=['POST'])
@login_required
def retwoshan(tid):
    u = current_user()
    existing = query("SELECT id FROM retwoshans WHERE user_id=? AND twoshan_id=?", [u['id'], tid], one=True)
    if existing:
        mutate("DELETE FROM retwoshans WHERE user_id=? AND twoshan_id=?", [u['id'], tid])
        # احذف توشة إعادة النشر من الفيد أيضاً
        mutate("DELETE FROM twoshans WHERE user_id=? AND retwoshan_from_id=?", [u['id'], tid])
        retwoshan_ed = False
    else:
        mutate("INSERT OR IGNORE INTO retwoshans (user_id, twoshan_id) VALUES (?,?)", [u['id'], tid])
        retwoshan_ed = True
        orig_content = query("SELECT content, user_id FROM twoshans WHERE id=?", [tid], one=True)
        if orig_content:
            mutate("""INSERT INTO twoshans (user_id, content, retwoshan_from_id)
                      VALUES (?,?,?)""", [u['id'], orig_content['content'], tid])
            add_notification(orig_content['user_id'], 'retwoshan', u['id'], tid)
    count = query("SELECT COUNT(*) as c FROM retwoshans WHERE twoshan_id=?", [tid], one=True)['c']
    return jsonify(retwoshan_ed=retwoshan_ed, count=count)

@app.route('/vote/<int:option_id>', methods=['POST'])
@login_required
def vote_poll(option_id):
    u = current_user()
    opt = query("SELECT * FROM poll_options WHERE id=?", [option_id], one=True)
    if not opt:
        return jsonify(error='خيار غير موجود'), 404
    existing = query("""SELECT 1 FROM poll_votes pv 
                        JOIN poll_options po ON pv.poll_option_id=po.id 
                        WHERE pv.user_id=? AND po.poll_id=?""", [u['id'], opt['poll_id']], one=True)
    if existing:
        return jsonify(error='لقد صوّتت بالفعل'), 400
    mutate("INSERT INTO poll_votes (user_id, poll_option_id) VALUES (?,?)", [u['id'], option_id])
    options = query("""SELECT po.id, po.text, 
                       (SELECT COUNT(*) FROM poll_votes WHERE poll_option_id=po.id) as votes
                       FROM poll_options po WHERE po.poll_id=?""", [opt['poll_id']])
    total = sum(o['votes'] for o in options)
    return jsonify(success=True, options=[dict(o) for o in options], total=total)

# ─── Routes: Follow ───────────────────────────────────────────────────────────

@app.route('/follow/<int:uid>', methods=['POST'])
@login_required
def follow(uid):
    u = current_user()
    if uid == u['id']:
        return jsonify(error='لا يمكنك متابعة نفسك'), 400
    existing = query("SELECT id FROM follows WHERE follower_id=? AND followed_id=?", [u['id'], uid], one=True)
    if existing:
        mutate("DELETE FROM follows WHERE follower_id=? AND followed_id=?", [u['id'], uid])
        following = False
    else:
        mutate("INSERT OR IGNORE INTO follows (follower_id, followed_id) VALUES (?,?)", [u['id'], uid])
        following = True
        add_notification(uid, 'follow', u['id'])
    count = query("SELECT COUNT(*) as c FROM follows WHERE followed_id=?", [uid], one=True)['c']
    return jsonify(following=following, followers=count)

@app.route('/block/<int:uid>', methods=['POST'])
@login_required
def block_user(uid):
    u = current_user()
    existing = query("SELECT id FROM blocks WHERE blocker_id=? AND blocked_id=?", [u['id'], uid], one=True)
    if existing:
        mutate("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", [u['id'], uid])
        blocked = False
        msg = 'تم إلغاء الحظر'
    else:
        mutate("INSERT OR IGNORE INTO blocks (blocker_id, blocked_id) VALUES (?,?)", [u['id'], uid])
        blocked = True
        msg = 'تم حظر المستخدم'
    flash(msg, 'info')
    return redirect(request.referrer or url_for('index'))

@app.route('/mute/<int:uid>', methods=['POST'])
@login_required
def mute_user(uid):
    u = current_user()
    existing = query("SELECT id FROM mutes WHERE muter_id=? AND muted_id=?", [u['id'], uid], one=True)
    if existing:
        mutate("DELETE FROM mutes WHERE muter_id=? AND muted_id=?", [u['id'], uid])
        msg = 'تم إلغاء الكتم'
    else:
        mutate("INSERT OR IGNORE INTO mutes (muter_id, muted_id) VALUES (?,?)", [u['id'], uid])
        msg = 'تم كتم المستخدم'
    flash(msg, 'info')
    return redirect(request.referrer or url_for('index'))

# ─── Routes: Profile ──────────────────────────────────────────────────────────

@app.route('/profile/<handle>')
@login_required
def profile(handle):
    u = current_user()
    target = query("SELECT * FROM users WHERE handle=?", [handle], one=True)
    if not target:
        abort(404)
    tab = request.args.get('tab', 'twoshans')
    if tab == 'likes':
        rows = query("""SELECT t.* FROM twoshans t
                        JOIN likes l ON t.id=l.twoshan_id
                        WHERE l.user_id=? ORDER BY l.created_at DESC""", [target['id']])
    elif tab == 'bookmarks' and (target['id'] == u['id'] or u['is_admin']):
        rows = query("""SELECT t.* FROM twoshans t
                        JOIN bookmarks b ON t.id=b.twoshan_id
                        WHERE b.user_id=? ORDER BY b.created_at DESC""", [target['id']])
    elif tab == 'retwoshans':
        rows = query("""SELECT t.* FROM twoshans t
                        JOIN retwoshans r ON t.id=r.twoshan_id
                        WHERE r.user_id=? ORDER BY r.created_at DESC""", [target['id']])
    else:
        rows = query("""SELECT * FROM twoshans WHERE user_id=? AND reply_to_id IS NULL
                        ORDER BY created_at DESC""", [target['id']])
    twoshans = enrich_twoshans(rows, u['id'])
    followers = query("SELECT COUNT(*) as c FROM follows WHERE followed_id=?", [target['id']], one=True)['c']
    following = query("SELECT COUNT(*) as c FROM follows WHERE follower_id=?", [target['id']], one=True)['c']
    is_following = bool(query("SELECT 1 FROM follows WHERE follower_id=? AND followed_id=?",
                              [u['id'], target['id']], one=True))
    is_blocked = bool(query("SELECT 1 FROM blocks WHERE blocker_id=? AND blocked_id=?",
                             [u['id'], target['id']], one=True))
    is_muted = bool(query("SELECT 1 FROM mutes WHERE muter_id=? AND muted_id=?",
                           [u['id'], target['id']], one=True))
    target_dict = dict(target)
    target_dict['online'] = is_online(target_dict.get('last_seen'))
    return render_template('profile.html', target=target_dict, twoshans=twoshans,
                           followers=followers, following=following,
                           is_following=is_following, is_blocked=is_blocked,
                           is_muted=is_muted, tab=tab)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    u = current_user()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        bio = request.form.get('bio', '').strip()
        new_handle = request.form.get('handle', '').strip().lstrip('@')

        if new_handle and new_handle != u['handle']:
            if not re.match(r'^[a-zA-Z0-9_]{3,20}$', new_handle):
                flash('المعرف غير صالح', 'danger')
                return redirect(url_for('settings'))
            if query("SELECT id FROM users WHERE handle=? AND id!=?", [new_handle, u['id']], one=True):
                flash('هذا المعرف مستخدم بالفعل', 'danger')
                return redirect(url_for('settings'))

        avatar_fname = u['avatar']
        banner_fname = u['banner']
        av = request.files.get('avatar')
        bn = request.files.get('banner')
        if av and av.filename:
            f, _ = save_upload(av)
            if f:
                avatar_fname = f
        if bn and bn.filename:
            f, _ = save_upload(bn)
            if f:
                banner_fname = f

        mutate("""UPDATE users SET username=?, bio=?, handle=?, avatar=?, banner=? WHERE id=?""",
               [username or u['username'], bio, new_handle or u['handle'],
                avatar_fname, banner_fname, u['id']])
        flash('تم تحديث الملف الشخصي', 'success')
        return redirect(url_for('profile', handle=new_handle or u['handle']))
    return render_template('settings.html', user=dict(u))

# ─── Routes: Notifications ────────────────────────────────────────────────────

@app.route('/notifications')
@login_required
def notifications():
    u = current_user()
    notifs = query("""
        SELECT n.*, usr.username, usr.handle, usr.avatar, usr.verified
        FROM notifications n
        JOIN users usr ON n.from_user_id=usr.id
        WHERE n.user_id=? ORDER BY n.created_at DESC LIMIT 50""", [u['id']])
    mutate("UPDATE notifications SET is_read=1 WHERE user_id=?", [u['id']])
    return render_template('notifications.html', notifs=notifs)

# ─── Routes: Messages (DMs) ───────────────────────────────────────────────────

@app.route('/messages')
@login_required
def messages():
    u = current_user()
    convs = query("""
        SELECT
          other_id,
          MAX(created_at) as last_time,
          (SELECT content FROM messages m2
           WHERE ((m2.sender_id=t.other_id AND m2.receiver_id=?)
               OR (m2.sender_id=? AND m2.receiver_id=t.other_id))
             AND m2.is_deleted_admin=0
           ORDER BY m2.created_at DESC LIMIT 1) as last_msg
        FROM (
          SELECT
            CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END AS other_id,
            created_at
          FROM messages
          WHERE (sender_id=? OR receiver_id=?) AND is_deleted_admin=0
        ) t
        GROUP BY other_id
        ORDER BY last_time DESC""",
        [u['id'], u['id'], u['id'], u['id'], u['id']])
    conv_list = []
    for c in convs:
        other = query("SELECT * FROM users WHERE id=?", [c['other_id']], one=True)
        if other:
            unread = query("""SELECT COUNT(*) as cnt FROM messages 
                              WHERE sender_id=? AND receiver_id=? AND is_read=0""",
                           [c['other_id'], u['id']], one=True)['cnt']
            conv_list.append({'user': dict(other), 'last_msg': c['last_msg'],
                               'last_time': c['last_time'], 'unread': unread})
    return render_template('messages.html', convs=conv_list)

@app.route('/messages/<handle>', methods=['GET', 'POST'])
@login_required
def dm_conversation(handle):
    u = current_user()
    other = query("SELECT * FROM users WHERE handle=?", [handle], one=True)
    if not other:
        abort(404)
    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        file = request.files.get('media')
        media_fname, media_type = save_upload(file)
        if content or media_fname:
            mutate("""INSERT INTO messages (sender_id,receiver_id,content,media_url,media_type)
                      VALUES (?,?,?,?,?)""",
                   [u['id'], other['id'], content, media_fname or '', media_type or ''])
        return redirect(url_for('dm_conversation', handle=handle))
    mutate("UPDATE messages SET is_read=1 WHERE sender_id=? AND receiver_id=?",
           [other['id'], u['id']])
    msgs = query("""SELECT m.*, us.username, us.avatar FROM messages m
                    JOIN users us ON m.sender_id=us.id
                    WHERE ((m.sender_id=? AND m.receiver_id=?) OR (m.sender_id=? AND m.receiver_id=?))
                    AND m.is_deleted_admin=0
                    ORDER BY m.created_at ASC""",
                 [u['id'], other['id'], other['id'], u['id']])
    return render_template('dm_conversation.html', other=dict(other), msgs=msgs)

# ─── Routes: Search ───────────────────────────────────────────────────────────

@app.route('/search')
@login_required
def search():
    u = current_user()
    q = request.args.get('q', '').strip()
    stype = request.args.get('type', 'users')
    results_users = []
    results_twoshans = []
    if q:
        if stype == 'users':
            results_users = query("""SELECT * FROM users
                                     WHERE (username LIKE ? OR handle LIKE ?)
                                     AND id != ? AND is_blocked=0
                                     LIMIT 20""",
                                  [f'%{q}%', f'%{q}%', u['id']])
        else:
            rows = query("""SELECT t.* FROM twoshans t
                            WHERE t.content LIKE ?
                            ORDER BY t.created_at DESC LIMIT 20""", [f'%{q}%'])
            results_twoshans = enrich_twoshans(rows, u['id'])
    return render_template('search.html', q=q, stype=stype,
                           results_users=results_users,
                           results_twoshans=results_twoshans)

@app.route('/hashtag/<tag>')
@login_required
def hashtag(tag):
    u = current_user()
    rows = query("""SELECT t.* FROM twoshans t
                    JOIN twoshan_hashtags th ON t.id=th.twoshan_id
                    JOIN hashtags h ON th.hashtag_id=h.id
                    WHERE h.tag=?
                    ORDER BY t.created_at DESC LIMIT 50""", [tag.lower()])
    twoshans = enrich_twoshans(rows, u['id'])
    return render_template('hashtag.html', tag=tag, twoshans=twoshans)

# ─── Routes: Admin ────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    u = current_user()
    stats = {
        'users': query("SELECT COUNT(*) as c FROM users", one=True)['c'],
        'twoshans': query("SELECT COUNT(*) as c FROM twoshans", one=True)['c'],
        'messages': query("SELECT COUNT(*) as c FROM messages", one=True)['c'],
        'reports': query("SELECT COUNT(*) as c FROM reports WHERE status='pending'", one=True)['c'],
        'likes': query("SELECT COUNT(*) as c FROM likes", one=True)['c'],
        'follows': query("SELECT COUNT(*) as c FROM follows", one=True)['c'],
    }
    recent_users = query("SELECT * FROM users ORDER BY created_at DESC LIMIT 10")
    recent_twoshans_raw = query("SELECT * FROM twoshans ORDER BY created_at DESC LIMIT 10")
    recent_twoshans = enrich_twoshans(recent_twoshans_raw, u['id'])
    return render_template('admin/dashboard.html', stats=stats,
                           recent_users=recent_users,
                           recent_twoshans=recent_twoshans)

@app.route('/admin/users')
@admin_required
def admin_users():
    users = query("SELECT * FROM users ORDER BY created_at DESC")
    return render_template('admin/users.html', users=users)

@app.route('/admin/reports')
@admin_required
def admin_reports():
    reports = query("""
        SELECT r.*, 
               ru.username as reporter_name, ru.handle as reporter_handle,
               t.content as twoshan_content,
               tu.username as twoshan_author, tu.handle as twoshan_handle
        FROM reports r
        JOIN users ru ON r.reporter_id=ru.id
        JOIN twoshans t ON r.twoshan_id=t.id
        JOIN users tu ON t.user_id=tu.id
        WHERE r.status='pending'
        ORDER BY r.created_at DESC""")
    return render_template('admin/reports.html', reports=reports)

@app.route('/admin/messages')
@admin_required
def admin_messages():
    u = current_user()
    msgs = query("""
        SELECT m.*, 
               s.username as sender_name, s.handle as sender_handle,
               r.username as receiver_name, r.handle as receiver_handle
        FROM messages m
        JOIN users s ON m.sender_id=s.id
        JOIN users r ON m.receiver_id=r.id
        ORDER BY m.created_at DESC LIMIT 100""")
    return render_template('admin/messages.html', msgs=msgs)

@app.route('/admin/action', methods=['POST'])
@admin_required
def admin_action():
    action = request.form.get('action')
    target_id = request.form.get('target_id')

    if action == 'pin_twoshan':
        t = query("SELECT is_pinned FROM twoshans WHERE id=?", [target_id], one=True)
        if t:
            mutate("UPDATE twoshans SET is_pinned=? WHERE id=?", [0 if t['is_pinned'] else 1, target_id])
    elif action == 'delete_twoshan':
        mutate("DELETE FROM twoshans WHERE id=?", [target_id])
    elif action == 'delete_message':
        mutate("UPDATE messages SET is_deleted_admin=1 WHERE id=?", [target_id])
    elif action == 'verify_user':
        mutate("UPDATE users SET verified=1 WHERE id=?", [target_id])
    elif action == 'unverify_user':
        mutate("UPDATE users SET verified=0 WHERE id=?", [target_id])
    elif action == 'vip_user':
        mutate("UPDATE users SET vip=1 WHERE id=?", [target_id])
    elif action == 'unvip_user':
        mutate("UPDATE users SET vip=0 WHERE id=?", [target_id])
    elif action == 'block_user':
        ban_reason = request.form.get('ban_reason', '').strip() or 'مخالفة سياسات المنصة'
        # جدول الحظر بعد 5 دقائق مع تنبيه
        mutate("""UPDATE users SET ban_reason=?, ban_scheduled_at=datetime('now','+5 minutes')
                  WHERE id=?""", [ban_reason, target_id])
        # أرسل إشعاراً للمستخدم
        mutate("""INSERT INTO notifications (user_id,type,from_user_id,message)
                  VALUES (?,?,?,?)""", [target_id, 'system', 1,
                  f'⚠️ تحذير: سيتم حظر حسابك خلال 5 دقائق. السبب: {ban_reason}'])
    elif action == 'unblock_user':
        mutate("UPDATE users SET is_blocked=0, ban_reason=NULL, ban_scheduled_at=NULL WHERE id=?", [target_id])
    elif action == 'resolve_report':
        mutate("UPDATE reports SET status='resolved' WHERE id=?", [target_id])
    elif action == 'dismiss_report':
        mutate("UPDATE reports SET status='dismissed' WHERE id=?", [target_id])

    flash('تم تنفيذ الإجراء بنجاح', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/admin/promote/<int:uid>', methods=['POST'])
@admin_required
def promote_admin(uid):
    mutate("UPDATE users SET is_admin=1 WHERE id=?", [uid])
    flash('تم ترقية المستخدم لمشرف', 'success')
    return redirect(url_for('admin_users'))

# ─── Keepalive ───────────────────────────────────────────────────────────────
# يحافظ على الموقع حياً طالما كان هناك نشاط خلال آخر 24 ساعة

_last_activity = time.time()
INACTIVITY_LIMIT = 24 * 60 * 60   # 24 ساعة بالثواني
PING_INTERVAL   = 4 * 60          # نبضة كل 4 دقائق

def _record_activity():
    global _last_activity
    _last_activity = time.time()

@app.before_request
def _track_activity():
    # نتجاهل طلبات الـ keepalive نفسها حتى لا تُعاد بشكل لا نهائي
    if request.path != '/_keepalive':
        _record_activity()

@app.route('/_keepalive')
def keepalive_ping():
    return jsonify(ok=True), 200

def _keepalive_loop():
    # انتظر قليلاً حتى يصحى الـ server
    time.sleep(15)
    port = int(os.environ.get('PORT', 5000))
    url  = f'http://127.0.0.1:{port}/_keepalive'
    while True:
        time.sleep(PING_INTERVAL)
        inactive_for = time.time() - _last_activity
        if inactive_for < INACTIVITY_LIMIT:
            try:
                urllib.request.urlopen(url, timeout=10)
            except Exception:
                pass  # الـ server سيتعامل مع الأخطاء بنفسه

_keepalive_thread = threading.Thread(target=_keepalive_loop, daemon=True)
_keepalive_thread.start()

# ─── Startup ─────────────────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
