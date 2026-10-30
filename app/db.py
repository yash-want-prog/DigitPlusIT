import sqlite3, os, json
PATH = lambda: os.getenv("DB_PATH", "data/app.db")

def conn():
    os.makedirs(os.path.dirname(PATH()) or ".", exist_ok=True)
    c = sqlite3.connect(PATH()); c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); return c

def q(sql, a=()):
    with conn() as c: return [dict(r) for r in c.execute(sql, a).fetchall()]

def x(sql, a=()):
    with conn() as c: return c.execute(sql, a).lastrowid

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings(id INTEGER PRIMARY KEY, title TEXT, kind TEXT, agenda TEXT,
 participants TEXT, absent TEXT, raw_notes TEXT, summary TEXT, parent_id INTEGER, recapped INTEGER DEFAULT 0,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, meeting_id INTEGER, text TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, meeting_id INTEGER, origin_id INTEGER, title TEXT,
 owner TEXT, due TEXT, status TEXT DEFAULT 'open', outcome TEXT, slips INTEGER DEFAULT 0, kb TEXT DEFAULT '[]',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS item_events(id INTEGER PRIMARY KEY, item_id INTEGER, event TEXT, detail TEXT,
 at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS kb(id INTEGER PRIMARY KEY, title TEXT, content TEXT, tags TEXT);
"""

def init():
    with conn() as c: c.executescript(SCHEMA)
    if not q("SELECT 1 FROM kb LIMIT 1"): seed()

def seed():
    from datetime import date, timedelta
    d = lambda n: (date.today() + timedelta(days=n)).isoformat()
    for t, c, tg in [
        ("Release Process", "Releases ship every second Thursday. QA sign-off by Priya is mandatory. Rollback plan must be in the ticket.", "release,qa,deploy"),
        ("Vendor Onboarding Policy", "New vendors need security review, legal sign-off and a named business owner before any contract signature.", "vendor,security,legal"),
        ("On-call Handbook", "On-call rotates weekly. Handover note must list open incidents, pending alerts and known flaky services.", "oncall,incident,handover")]:
        x("INSERT INTO kb(title,content,tags) VALUES(?,?,?)", (t, c, tg))
    m = x("INSERT INTO meetings(title,kind,agenda,participants,absent,raw_notes,summary,recapped) VALUES(?,?,?,?,?,?,?,1)",
          ("Sprint 41 Planning", "meeting", "Release scope, vendor risk", json.dumps(["Ayush", "Priya", "Sparsh", "Riddhi"]),
           json.dumps(["Riddhi"]), "", "Team agreed to freeze scope for the Thursday release and start vendor review.", ))
    x("INSERT INTO decisions(meeting_id,text) VALUES(?,?)", (m, "Scope frozen for the next release"))
    x("INSERT INTO decisions(meeting_id,text) VALUES(?,?)", (m, "Vendor X goes through security review first"))
    for t, o, du, kb in [("Complete security review for Vendor X", "Sparsh", d(-3), [2]), ("Write rollback plan for release", "Priya", d(2), [1]),
                         ("Update on-call handover template", "", d(7), [3])]:
        i = x("INSERT INTO items(meeting_id,title,owner,due,kb) VALUES(?,?,?,?,?)", (m, t, o, du, json.dumps(kb)))
        x("INSERT INTO item_events(item_id,event,detail) VALUES(?,?,?)", (i, "created", "seed"))
