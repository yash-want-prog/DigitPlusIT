import json, time, uuid, logging, os, hmac
from datetime import date
from typing import List, Literal, Optional
from pathlib import Path
from collections import deque
from fastapi import Header
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from . import db, ai
from .log import setup, request_id_var

# Load local .env values before logging/database/application setup.
load_dotenv()

LOG_PATH = setup()
db.init()
log = logging.getLogger("handoffiq.app")
app = FastAPI(title="HandoffIQ")


@app.on_event("startup")
async def log_startup():
    log.info("application_started", extra={"ctx": {"log_file": str(LOG_PATH)}})


@app.on_event("shutdown")
async def log_shutdown():
    log.info("application_stopped")

@app.middleware("http")
async def reqid(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    t = time.perf_counter()
    try:
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("unhandled_exception", extra={"ctx": {"path": request.url.path}})
            resp = JSONResponse({"error": "internal error", "request_id": rid}, 500)
        resp.headers["x-request-id"] = rid
        log.info("http_request", extra={"ctx": {
            "method": request.method,
            "path": request.url.path,
            "status": resp.status_code,
            "latency_ms": round((time.perf_counter() - t) * 1000, 2),
        }})
        return resp
    finally:
        request_id_var.reset(token)

class MeetingIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    kind: Literal["meeting", "handoff"] = "meeting"
    agenda: str = Field("", max_length=4000)
    participants: List[str] = []
    absent: List[str] = []          # people who missed it / are taking over -> they get a brief
    raw_notes: str = Field("", max_length=20000)

class ItemPatch(BaseModel):
    status: Optional[Literal["open", "in_progress", "done", "dropped"]] = None
    owner: Optional[str] = Field(None, max_length=100)
    due: Optional[str] = Field(None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    outcome: Optional[str] = Field(None, max_length=1000)

class KbIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=2, max_length=10000)
    tags: str = ""

def risk(i):
    """Slip Radar score 0-100: how likely this item is to silently die."""
    s, why = 0, []
    if i["status"] not in ("open", "in_progress"): return 0, []
    if i["due"]:
        days = (date.fromisoformat(i["due"]) - date.today()).days
        if days < 0: s += 40; why.append(f"overdue {-days}d")
        elif days <= 2: s += 15; why.append("due soon")
    else: s += 10; why.append("no due date")
    if not i["owner"]: s += 25; why.append("no owner")
    if i["slips"]: s += min(i["slips"], 3) * 12; why.append(f"carried {i['slips']}x")
    if i["status"] == "open" and i["slips"]: s += 5
    return min(s, 100), why

def enrich(i):
    i["risk"], i["risk_reasons"] = risk(i); i["kb"] = json.loads(i["kb"] or "[]"); return i

def get_meeting(mid):
    r = db.q("SELECT * FROM meetings WHERE id=?", (mid,))
    if not r: raise HTTPException(404, "meeting not found")
    m = r[0]
    for k in ("participants", "absent"): m[k] = json.loads(m[k] or "[]")
    m["decisions"] = [d["text"] for d in db.q("SELECT text FROM decisions WHERE meeting_id=?", (mid,))]
    m["items"] = [enrich(i) for i in db.q("SELECT * FROM items WHERE meeting_id=?", (mid,))]
    return m

def ev(item_id, event, detail=""):
    db.x("INSERT INTO item_events(item_id,event,detail) VALUES(?,?,?)", (item_id, event, detail))
    log.info(f"item_{event}", extra={"ctx": {"item_id": item_id, "detail": detail}})

@app.post("/api/meetings", status_code=201)
def create_meeting(m: MeetingIn):
    mid = db.x("INSERT INTO meetings(title,kind,agenda,participants,absent,raw_notes) VALUES(?,?,?,?,?,?)",
               (m.title, m.kind, m.agenda, json.dumps(m.participants), json.dumps(m.absent), m.raw_notes))
    log.info("meeting_created", extra={"ctx": {"meeting_id": mid, "kind": m.kind}}); return get_meeting(mid)

@app.get("/api/meetings")
def list_meetings():
    rows = db.q("SELECT id,title,kind,summary,parent_id,recapped,created_at FROM meetings ORDER BY id DESC")
    for r in rows: r["open_items"] = db.q("SELECT COUNT(*) c FROM items WHERE meeting_id=? AND status IN ('open','in_progress')", (r["id"],))[0]["c"]
    return rows

@app.get("/api/meetings/{mid}")
def one(mid: int): return get_meeting(mid)

@app.post("/api/meetings/{mid}/recap")
def recap(mid: int, force: bool = False):
    m = get_meeting(mid)
    if not m["raw_notes"].strip(): raise HTTPException(422, "meeting has no raw notes to recap")
    if m["recapped"] and not force: raise HTTPException(409, "already recapped; use force=true")
    if force:
        db.x("DELETE FROM decisions WHERE meeting_id=?", (mid,))
        db.x("DELETE FROM items WHERE meeting_id=? AND origin_id IS NULL", (mid,))
    r = ai.recap(m["raw_notes"], m["participants"], db.q("SELECT * FROM kb"))
    db.x("UPDATE meetings SET summary=?, recapped=1 WHERE id=?", (r["summary"], mid))
    for d in r["decisions"]: db.x("INSERT INTO decisions(meeting_id,text) VALUES(?,?)", (mid, d))
    for i in r["items"]:
        due = i["due"] if len(i["due"]) == 10 else ""
        iid = db.x("INSERT INTO items(meeting_id,title,owner,due,kb) VALUES(?,?,?,?,?)", (mid, i["title"], i["owner"], due, json.dumps(i["kb_ids"])))
        ev(iid, "created", f"recap ai={r['ai_used']}")
    out = get_meeting(mid); out["ai_used"] = r["ai_used"]; return out

@app.post("/api/meetings/{mid}/carry-forward", status_code=201)
def carry(mid: int, title: Optional[str] = None):
    m = get_meeting(mid)
    open_items = [i for i in m["items"] if i["status"] in ("open", "in_progress")]
    if not open_items: raise HTTPException(422, "no open items to carry forward")
    nid = db.x("INSERT INTO meetings(title,kind,agenda,participants,absent,parent_id) VALUES(?,?,?,?,?,?)",
               (title or f"Follow-up: {m['title']}", m["kind"], "Carried-over open items: " + "; ".join(i["title"] for i in open_items),
                json.dumps(m["participants"]), "[]", mid))
    for i in open_items:
        n = db.x("INSERT INTO items(meeting_id,origin_id,title,owner,due,slips,kb) VALUES(?,?,?,?,?,?,?)",
                 (nid, i.get("origin_id") or i["id"], i["title"], i["owner"], i["due"], i["slips"] + 1, json.dumps(i["kb"])))
        db.x("UPDATE items SET status='carried' WHERE id=?", (i["id"],))
        ev(i["id"], "carried_forward", f"to meeting {nid}"); ev(n, "created", f"carried from item {i['id']}")
    return get_meeting(nid)

@app.patch("/api/items/{iid}")
def patch_item(iid: int, p: ItemPatch):
    r = db.q("SELECT * FROM items WHERE id=?", (iid,))
    if not r: raise HTTPException(404, "item not found")
    old = r[0]
    if p.status in ("done", "dropped") and not (p.outcome or old["outcome"]):
        raise HTTPException(422, "record an outcome when closing an item")
    for f, label in (("status", "status_changed"), ("owner", "reassigned"), ("due", "due_changed"), ("outcome", "outcome_recorded")):
        v = getattr(p, f)
        if v is not None and v != old[f]:
            db.x(f"UPDATE items SET {f}=? WHERE id=?", (v, iid))
            ev(iid, "closed" if f == "status" and v in ("done", "dropped") else label, f"{old[f]} -> {v}")
    return enrich(db.q("SELECT * FROM items WHERE id=?", (iid,))[0])

@app.get("/api/items/{iid}/history")
def history(iid: int): return db.q("SELECT event,detail,at FROM item_events WHERE item_id=? ORDER BY id", (iid,))

@app.get("/api/radar")
def radar():
    rows = [enrich(i) for i in db.q("SELECT i.*, m.title meeting FROM items i JOIN meetings m ON m.id=i.meeting_id WHERE i.status IN ('open','in_progress')")]
    return sorted(rows, key=lambda i: -i["risk"])

class Transfer(BaseModel):
    from_person: str = Field(min_length=1); to_person: str = Field(min_length=1)

@app.post("/api/handoff")
def handoff(t: Transfer):
    rows = db.q("SELECT id FROM items WHERE LOWER(owner)=LOWER(?) AND status IN ('open','in_progress')", (t.from_person,))
    for r in rows:
        db.x("UPDATE items SET owner=? WHERE id=?", (t.to_person, r["id"])); ev(r["id"], "reassigned", f"{t.from_person} -> {t.to_person} (work transfer)")
    return {"reassigned": len(rows)}

class BriefIn(BaseModel):
    person: str = Field(min_length=1, max_length=100)

@app.post("/api/brief")
def brief(b: BriefIn):
    p = b.person.lower()
    ms = [get_meeting(r["id"]) for r in db.q("SELECT id,absent FROM meetings ORDER BY id DESC LIMIT 30") if p in [a.lower() for a in json.loads(r["absent"] or "[]")]]
    mine = [i for i in radar() if (i["owner"] or "").lower() == p]
    kb = {k["id"]: k["title"] for k in db.q("SELECT id,title FROM kb")}
    ctx = {"meetings": [{"title": m["title"], "summary": m["summary"], "decisions": m["decisions"]} for m in ms],
           "open_items": [{"title": i["title"], "due": i["due"], "risk": i["risk"], "why": i["risk_reasons"], "kb": [kb.get(k) for k in i["kb"]]} for i in mine]}
    if not ms and not mine: raise HTTPException(404, "nothing to brief for this person (no missed meetings or open items)")
    return ai.brief(b.person, ctx)

@app.get("/api/kb")
def kb_list(): return db.q("SELECT * FROM kb ORDER BY id DESC")

@app.post("/api/kb", status_code=201)
def kb_add(k: KbIn):
    i = db.x("INSERT INTO kb(title,content,tags) VALUES(?,?,?)", (k.title, k.content, k.tags)); log.info("kb_created", extra={"ctx": {"kb_id": i}})
    return db.q("SELECT * FROM kb WHERE id=?", (i,))[0]


def _read_recent_logs(limit: int = 500):
    """Read newest structured log records from the configured log and rotated files."""
    log_file = Path(LOG_PATH)
    candidates = [log_file] + [Path(str(log_file) + f".{n}") for n in range(1, 4)]
    records = []
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            # Read only the newest lines, avoiding loading a large log file into memory.
            with candidate.open("r", encoding="utf-8", errors="replace") as stream:
                lines = deque(stream, maxlen=limit)
            for line in lines:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        records.append(record)
                except json.JSONDecodeError:
                    records.append({
                        "ts": "", "level": "INFO", "logger": "unknown",
                        "request_id": "-", "msg": line[:1000],
                    })
        except OSError:
            log.exception("log_file_read_failed", extra={"ctx": {"file": candidate.name}})
    records.sort(key=lambda item: str(item.get("ts", "")), reverse=True)
    return records[:limit]


def _check_log_access(x_logs_token: Optional[str]):
    expected = os.getenv("LOG_DASHBOARD_TOKEN", "")
    if expected and not hmac.compare_digest(x_logs_token or "", expected):
        raise HTTPException(status_code=401, detail="Invalid or missing logging dashboard token")


@app.get("/api/logs")
def get_logs(
    level: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    x_logs_token: Optional[str] = Header(default=None),
):
    """Return recent structured logs. Optional LOG_DASHBOARD_TOKEN protects this endpoint."""
    _check_log_access(x_logs_token)
    limit = max(1, min(limit, 500))
    records = _read_recent_logs(500)
    if level and level.upper() != "ALL":
        records = [r for r in records if str(r.get("level", "")).upper() == level.upper()]
    if search:
        needle = search.casefold()
        records = [
            r for r in records
            if needle in json.dumps(r, ensure_ascii=False, default=str).casefold()
        ]
    return {"items": records[:limit], "count": min(len(records), limit), "log_file": Path(LOG_PATH).name}


@app.get("/api/logs/stats")
def get_log_stats(x_logs_token: Optional[str] = Header(default=None)):
    """Aggregate recent request and AI activity from structured logs."""
    _check_log_access(x_logs_token)
    records = _read_recent_logs(500)
    requests = [r for r in records if r.get("msg") == "http_request"]
    ai_calls = [r for r in records if r.get("msg") == "ai_call"]
    errors = [r for r in records if str(r.get("level", "")).upper() == "ERROR"]
    warnings = [r for r in records if str(r.get("level", "")).upper() == "WARNING"]
    latencies = [float(r["latency_ms"]) for r in requests if isinstance(r.get("latency_ms"), (int, float))]
    ai_success = sum(1 for r in ai_calls if r.get("outcome") == "success")
    ai_failed = sum(1 for r in ai_calls if r.get("outcome") != "success")
    return {
        "total_events": len(records),
        "requests": len(requests),
        "errors": len(errors),
        "warnings": len(warnings),
        "avg_request_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "ai_calls": len(ai_calls),
        "ai_success": ai_success,
        "ai_failed": ai_failed,
        "log_file": Path(LOG_PATH).name,
    }


@app.get("/health")
def health(): return {"status": "ok"}

@app.get("/")
def index(): return FileResponse("static/index.html")
app.mount("/static", StaticFiles(directory="static"), name="static")
