from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import sqlite3, hashlib, re
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'pebbles_v2_2024'

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def current_user():
    if 'user_id' not in session: return None
    conn = get_db()
    u = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
    conn.close()
    return u

def init_db():
    conn = get_db()

    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY,
        name       TEXT NOT NULL,
        email      TEXT UNIQUE NOT NULL,
        password   TEXT NOT NULL,
        phone      TEXT,
        flat_no    TEXT,
        tower      TEXT,
        role       TEXT DEFAULT 'resident'
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS vehicles (
        id         INTEGER PRIMARY KEY,
        user_id    INTEGER NOT NULL,
        plate      TEXT NOT NULL,
        label      TEXT,
        type       TEXT DEFAULT 'car',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS parking_slots (
        id          INTEGER PRIMARY KEY,
        slot_number TEXT UNIQUE NOT NULL,
        tower       TEXT,
        zone        TEXT,
        slot_type   TEXT DEFAULT 'car',
        floor_level TEXT DEFAULT 'Ground',
        status      TEXT DEFAULT 'free',
        maintenance INTEGER DEFAULT 0,
        booked_by   INTEGER,
        vehicle_no  TEXT,
        booked_at   TEXT,
        expires_at  TEXT,
        FOREIGN KEY(booked_by) REFERENCES users(id)
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER,
        slot_id     INTEGER,
        slot_number TEXT,
        tower       TEXT,
        zone        TEXT,
        slot_type   TEXT,
        vehicle_no  TEXT,
        booked_at   TEXT,
        expires_at  TEXT,
        released_at TEXT,
        status      TEXT DEFAULT 'active',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # Admin
    try:
        conn.execute("INSERT INTO users (name,email,password,role,flat_no,tower,phone) VALUES (?,?,?,?,?,?,?)",
                     ('Admin','admin@pebbles.com',hash_pw('admin123'),'admin','Office','Admin','9999999999'))
    except: pass

    # Guard
    try:
        conn.execute("INSERT INTO users (name,email,password,role,flat_no,tower,phone) VALUES (?,?,?,?,?,?,?)",
                     ('Guard','guard@pebbles.com',hash_pw('guard123'),'guard','Gate','Common','9999999998'))
    except: pass

    # Seed slots
    count = conn.execute('SELECT COUNT(*) FROM parking_slots').fetchone()[0]
    if count == 0:
        slots = []
        for t in ['A','B','C','D','E','F','G']:
            for i in range(1,9):
                slots.append((f'{t}-G{i}', t, f'Tower {t} Ground', 'car', 'Ground'))
            for i in range(1,5):
                slots.append((f'{t}-P{i}', t, f'Tower {t} Podium', 'car', 'Podium'))
        for i in range(1,9): slots.append((f'EV-{i:02d}','Common','EV Charging','ev','Ground'))
        for i in range(1,13): slots.append((f'TW-{i:02d}','Common','Two-Wheeler','tw','Basement'))
        for i in range(1,9): slots.append((f'VIS-{i:02d}','Common','Visitor','visitor','Ground'))
        for i in range(1,5): slots.append((f'HC-{i:02d}','Common','Accessible','hc','Ground'))
        conn.executemany(
            'INSERT INTO parking_slots (slot_number,tower,zone,slot_type,floor_level) VALUES (?,?,?,?,?)',
            slots)

    conn.commit()
    conn.close()

# ── Auto-expire bookings ───────────────────────────────────────────────────────
def auto_expire():
    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    expired = conn.execute(
        'SELECT * FROM bookings WHERE status="active" AND expires_at < ?', (now,)).fetchall()
    for b in expired:
        conn.execute('UPDATE parking_slots SET status="free",booked_by=NULL,vehicle_no=NULL,booked_at=NULL,expires_at=NULL WHERE id=?',
                     (b['slot_id'],))
        conn.execute('UPDATE bookings SET status="expired",released_at=? WHERE id=?', (now, b['id']))
    conn.commit()
    conn.close()
    return len(expired)

# ── Pages ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    u = current_user()
    if not u: return redirect(url_for('login'))
    if u['role'] == 'admin': return redirect(url_for('admin'))
    if u['role'] == 'guard': return redirect(url_for('guard'))
    return redirect(url_for('map_view'))

@app.route('/login')
def login():
    if current_user(): return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/signup')
def signup():
    if current_user(): return redirect(url_for('index'))
    return render_template('signup.html')

@app.route('/map')
def map_view():
    u = current_user()
    if not u: return redirect(url_for('login'))
    auto_expire()
    return render_template('map.html', user=dict(u))

@app.route('/history')
def history():
    u = current_user()
    if not u: return redirect(url_for('login'))
    return render_template('history.html', user=dict(u))

@app.route('/profile')
def profile():
    u = current_user()
    if not u: return redirect(url_for('login'))
    return render_template('profile.html', user=dict(u))

@app.route('/guard')
def guard():
    u = current_user()
    if not u: return redirect(url_for('login'))
    if u['role'] not in ('guard','admin'): return redirect(url_for('map_view'))
    return render_template('guard.html', user=dict(u))

@app.route('/admin')
def admin():
    u = current_user()
    if not u or u['role'] != 'admin': return redirect(url_for('login'))
    auto_expire()
    return render_template('admin.html', user=dict(u))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ── Auth APIs ─────────────────────────────────────────────────────────────────
@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json
    conn = get_db()
    u = conn.execute('SELECT * FROM users WHERE email=? AND password=?',
                     (d['email'], hash_pw(d['password']))).fetchone()
    conn.close()
    if not u: return jsonify({'success':False,'message':'Invalid email or password'})
    session['user_id'] = u['id']
    return jsonify({'success':True,'role':u['role']})

@app.route('/api/signup', methods=['POST'])
def api_signup():
    d = request.json
    if not d.get('name') or not d.get('email') or not d.get('password'):
        return jsonify({'success':False,'message':'All fields required'})
    if len(d['password']) < 6:
        return jsonify({'success':False,'message':'Password must be 6+ characters'})
    try:
        conn = get_db()
        conn.execute('INSERT INTO users (name,email,password,phone,flat_no,tower) VALUES (?,?,?,?,?,?)',
                     (d['name'],d['email'],hash_pw(d['password']),
                      d.get('phone',''),d.get('flat',''),d.get('tower','')))
        conn.commit()
        u = conn.execute('SELECT * FROM users WHERE email=?',(d['email'],)).fetchone()
        session['user_id'] = u['id']
        conn.close()
        return jsonify({'success':True})
    except:
        return jsonify({'success':False,'message':'Email already registered'})

# ── Vehicle APIs ──────────────────────────────────────────────────────────────
@app.route('/api/vehicles', methods=['GET'])
def api_get_vehicles():
    u = current_user()
    if not u: return jsonify([])
    conn = get_db()
    vs = conn.execute('SELECT * FROM vehicles WHERE user_id=?',(u['id'],)).fetchall()
    conn.close()
    return jsonify([dict(v) for v in vs])

@app.route('/api/vehicles', methods=['POST'])
def api_add_vehicle():
    u = current_user()
    if not u: return jsonify({'success':False,'message':'Not logged in'})
    d = request.json
    plate = d.get('plate','').strip().upper()
    if not plate: return jsonify({'success':False,'message':'Plate required'})
    # Validate Indian number plate format loosely
    if not re.match(r'^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}$', plate.replace(' ','')):
        return jsonify({'success':False,'message':'Invalid plate format (e.g. MH12AB1234)'})
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM vehicles WHERE user_id=?',(u['id'],)).fetchone()[0]
    if count >= 4:
        conn.close()
        return jsonify({'success':False,'message':'Max 4 vehicles per resident'})
    try:
        conn.execute('INSERT INTO vehicles (user_id,plate,label,type) VALUES (?,?,?,?)',
                     (u['id'],plate,d.get('label',''),d.get('type','car')))
        conn.commit()
        vid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return jsonify({'success':True,'id':vid,'plate':plate})
    except:
        return jsonify({'success':False,'message':'Plate already registered'})

@app.route('/api/vehicles/<int:vid>', methods=['DELETE'])
def api_del_vehicle(vid):
    u = current_user()
    if not u: return jsonify({'success':False})
    conn = get_db()
    conn.execute('DELETE FROM vehicles WHERE id=? AND user_id=?',(vid,u['id']))
    conn.commit()
    conn.close()
    return jsonify({'success':True})

# ── Slot APIs ─────────────────────────────────────────────────────────────────
@app.route('/api/slots')
def api_slots():
    auto_expire()
    conn = get_db()
    slots = conn.execute('SELECT * FROM parking_slots ORDER BY slot_number').fetchall()
    conn.close()
    return jsonify([dict(s) for s in slots])

@app.route('/api/book', methods=['POST'])
def api_book():
    u = current_user()
    if not u: return jsonify({'success':False,'message':'Not logged in'})
    d = request.json
    vehicle_no = d.get('vehicle_no','').strip().upper()
    if not vehicle_no: return jsonify({'success':False,'message':'Vehicle number required'})
    hours = int(d.get('hours', 12))
    if hours < 1 or hours > 48: hours = 12

    conn = get_db()
    slot = conn.execute('SELECT * FROM parking_slots WHERE id=?',(d['slot_id'],)).fetchone()
    if not slot:
        conn.close()
        return jsonify({'success':False,'message':'Slot not found'})
    if slot['status'] == 'occupied':
        conn.close()
        return jsonify({'success':False,'message':'Slot already taken!'})
    if slot['maintenance']:
        conn.close()
        return jsonify({'success':False,'message':'Slot is under maintenance'})

    now       = datetime.now()
    expires   = now + timedelta(hours=hours)
    now_str   = now.strftime('%Y-%m-%d %H:%M:%S')
    exp_str   = expires.strftime('%Y-%m-%d %H:%M:%S')
    exp_label = expires.strftime('%d %b, %I:%M %p')

    conn.execute('''UPDATE parking_slots SET status="occupied",booked_by=?,
                    vehicle_no=?,booked_at=?,expires_at=? WHERE id=?''',
                 (u['id'],vehicle_no,now_str,exp_str,d['slot_id']))
    conn.execute('''INSERT INTO bookings
                    (user_id,slot_id,slot_number,tower,zone,slot_type,vehicle_no,booked_at,expires_at)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                 (u['id'],d['slot_id'],slot['slot_number'],slot['tower'],
                  slot['zone'],slot['slot_type'],vehicle_no,now_str,exp_str))
    conn.commit()
    conn.close()
    return jsonify({'success':True,
                    'message':f"✅ {slot['slot_number']} booked! Expires {exp_label}"})

@app.route('/api/release', methods=['POST'])
def api_release():
    u = current_user()
    if not u: return jsonify({'success':False,'message':'Not logged in'})
    d = request.json
    conn = get_db()
    slot = conn.execute('SELECT * FROM parking_slots WHERE id=?',(d['slot_id'],)).fetchone()
    if not slot:
        conn.close()
        return jsonify({'success':False,'message':'Slot not found'})
    is_admin  = u['role'] in ('admin','guard')
    is_owner  = slot['booked_by'] is not None and int(slot['booked_by']) == int(u['id'])
    if not (is_admin or is_owner):
        conn.close()
        return jsonify({'success':False,'message':'Not your slot!'})
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''UPDATE parking_slots SET status="free",booked_by=NULL,
                    vehicle_no=NULL,booked_at=NULL,expires_at=NULL WHERE id=?''',
                 (d['slot_id'],))
    conn.execute('''UPDATE bookings SET status="completed",released_at=?
                    WHERE slot_id=? AND status="active"''', (now, d['slot_id']))
    conn.commit()
    conn.close()
    return jsonify({'success':True,'message':'🔓 Slot released!'})

# ── Admin Slot Management ─────────────────────────────────────────────────────
@app.route('/api/admin/slot', methods=['POST'])
def api_admin_slot():
    u = current_user()
    if not u or u['role'] != 'admin': return jsonify({'success':False})
    d = request.json
    action = d.get('action')
    conn = get_db()

    if action == 'toggle_maintenance':
        slot = conn.execute('SELECT * FROM parking_slots WHERE id=?',(d['slot_id'],)).fetchone()
        new_val = 0 if slot['maintenance'] else 1
        conn.execute('UPDATE parking_slots SET maintenance=? WHERE id=?',(new_val,d['slot_id']))
        conn.commit()
        conn.close()
        return jsonify({'success':True,'maintenance':new_val})

    if action == 'add_slot':
        snum = d.get('slot_number','').strip().upper()
        if not snum:
            conn.close()
            return jsonify({'success':False,'message':'Slot number required'})
        try:
            conn.execute('''INSERT INTO parking_slots (slot_number,tower,zone,slot_type,floor_level)
                            VALUES (?,?,?,?,?)''',
                         (snum, d.get('tower','Common'), d.get('zone','Common Area'),
                          d.get('slot_type','car'), d.get('floor_level','Ground')))
            conn.commit()
            conn.close()
            return jsonify({'success':True,'message':f'Slot {snum} added'})
        except:
            conn.close()
            return jsonify({'success':False,'message':'Slot number already exists'})

    if action == 'delete_slot':
        slot = conn.execute('SELECT * FROM parking_slots WHERE id=?',(d['slot_id'],)).fetchone()
        if slot and slot['status'] == 'occupied':
            conn.close()
            return jsonify({'success':False,'message':'Cannot delete occupied slot'})
        conn.execute('DELETE FROM parking_slots WHERE id=?',(d['slot_id'],))
        conn.commit()
        conn.close()
        return jsonify({'success':True})

    conn.close()
    return jsonify({'success':False,'message':'Unknown action'})

# ── Guard API ─────────────────────────────────────────────────────────────────
@app.route('/api/guard/lookup')
def api_guard_lookup():
    plate = request.args.get('plate','').strip().upper()
    if not plate: return jsonify({'found':False})
    conn = get_db()
    slot = conn.execute(
        'SELECT ps.*, u.name, u.flat_no, u.tower as user_tower FROM parking_slots ps '
        'LEFT JOIN users u ON ps.booked_by=u.id '
        'WHERE ps.vehicle_no=? AND ps.status="occupied"', (plate,)).fetchone()
    conn.close()
    if not slot: return jsonify({'found':False,'plate':plate})
    return jsonify({'found':True,'slot':dict(slot)})

@app.route('/api/guard/visitor-slots')
def api_visitor_slots():
    conn = get_db()
    slots = conn.execute(
        'SELECT * FROM parking_slots WHERE slot_type="visitor" ORDER BY slot_number').fetchall()
    conn.close()
    return jsonify([dict(s) for s in slots])

# ── Stats & History ───────────────────────────────────────────────────────────
@app.route('/api/stats')
def api_stats():
    conn = get_db()
    def q(sql): return conn.execute(sql).fetchone()[0]
    data = {
        'total':    q('SELECT COUNT(*) FROM parking_slots WHERE maintenance=0'),
        'occupied': q('SELECT COUNT(*) FROM parking_slots WHERE status="occupied"'),
        'free':     q('SELECT COUNT(*) FROM parking_slots WHERE status="free" AND maintenance=0'),
        'maintenance': q('SELECT COUNT(*) FROM parking_slots WHERE maintenance=1'),
        'ev_free':  q('SELECT COUNT(*) FROM parking_slots WHERE slot_type="ev" AND status="free"'),
        'tw_free':  q('SELECT COUNT(*) FROM parking_slots WHERE slot_type="tw" AND status="free"'),
        'vis_free': q('SELECT COUNT(*) FROM parking_slots WHERE slot_type="visitor" AND status="free"'),
        'hc_free':  q('SELECT COUNT(*) FROM parking_slots WHERE slot_type="hc" AND status="free"'),
        'users':    q('SELECT COUNT(*) FROM users WHERE role="resident"'),
        'bookings': q('SELECT COUNT(*) FROM bookings'),
        'active':   q('SELECT COUNT(*) FROM bookings WHERE status="active"'),
    }
    conn.close()
    return jsonify(data)

@app.route('/api/my-bookings')
def api_my_bookings():
    u = current_user()
    if not u: return jsonify([])
    conn = get_db()
    rows = conn.execute('SELECT * FROM bookings WHERE user_id=? ORDER BY id DESC',(u['id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/all-bookings')
def api_all_bookings():
    u = current_user()
    if not u or u['role'] != 'admin': return jsonify([])
    conn = get_db()
    rows = conn.execute('''SELECT b.*, u.name, u.email, u.flat_no, u.tower as user_tower
        FROM bookings b JOIN users u ON b.user_id=u.id
        ORDER BY b.id DESC LIMIT 200''').fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/profile', methods=['POST'])
def api_update_profile():
    u = current_user()
    if not u: return jsonify({'success':False})
    d = request.json
    conn = get_db()
    conn.execute('UPDATE users SET name=?,phone=?,flat_no=?,tower=? WHERE id=?',
                 (d.get('name',u['name']),d.get('phone',u['phone']),
                  d.get('flat',u['flat_no']),d.get('tower',u['tower']),u['id']))
    if d.get('new_password') and len(d['new_password']) >= 6:
        conn.execute('UPDATE users SET password=? WHERE id=?',
                     (hash_pw(d['new_password']),u['id']))
    conn.commit()
    conn.close()
    return jsonify({'success':True,'message':'Profile updated!'})

if __name__ == '__main__':
    import os
    init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
