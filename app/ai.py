import os
import re
import json
import time
import logging
import httpx
from datetime import date

log = logging.getLogger("handoffiq.ai")
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

class AIError(Exception):
    pass


def call(op, system, user, max_tokens=1200):
    """Call the Gemini Developer API using the free-tier Flash model by default."""
    key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if not key:
        raise AIError("GEMINI_API_KEY not set")

    url = f"{API_BASE}/{model}:generateContent"
    generation_config = {
        "temperature": 0.2,
        "maxOutputTokens": max_tokens,
    }
    if op == "recap":
        generation_config["responseMimeType"] = "application/json"

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": generation_config,
    }

    last_error = None
    for attempt in (1, 2):
        started = time.perf_counter()
        try:
            response = httpx.post(
                url,
                timeout=45,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise AIError("Gemini returned no candidates (possibly blocked by safety filters)")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
            if not text.strip():
                raise AIError("Gemini returned an empty response")

            log.info(
                "ai_call",
                extra={"ctx": {
                    "provider": "google_gemini",
                    "op": op,
                    "model": model,
                    "attempt": attempt,
                    "outcome": "success",
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }},
            )
            return text.strip()
        except Exception as exc:
            last_error = exc
            # Do not log request headers, API keys, prompts, or provider response bodies.
            log.warning(
                "ai_call",
                extra={"ctx": {
                    "provider": "google_gemini",
                    "op": op,
                    "model": model,
                    "attempt": attempt,
                    "outcome": "error",
                    "error": type(exc).__name__,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }},
            )

    raise AIError(f"{op} failed after retries ({type(last_error).__name__})")


def _json(text):
    # JSON mode should return JSON directly; tolerate a fenced response as a defensive fallback.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise AIError("no JSON object in Gemini response")
        return json.loads(match.group(0))


RECAP_SYS = (
    "You convert raw, messy meeting/handoff notes into a structured recap. Reply with ONLY JSON: "
    '{"summary":str,"decisions":[str],"items":[{"title":str,"owner":str|"","due":"YYYY-MM-DD"|"","kb_ids":[int]}]}. '
    "Only include owners/dates stated or clearly implied; never invent them (use empty strings). "
    "Resolve relative dates against today's date. "
    "kb_ids: ids of knowledge-base docs that are genuinely relevant to that item, else []."
)


def recap(notes, participants, kb):
    try:
        kbtxt = "\n".join(
            f"[{k['id']}] {k['title']}: {k['content'][:200]}" for k in kb
        )
        out = _json(call(
            "recap",
            RECAP_SYS,
            f"Today: {date.today()}\nParticipants: {participants}\nKB:\n{kbtxt}\n\nNOTES:\n{notes}",
            1400,
        ))
        valid_kb_ids = {doc["id"] for doc in kb}
        items = [
            {
                "title": str(item["title"])[:300],
                "owner": str(item.get("owner") or ""),
                "due": str(item.get("due") or ""),
                "kb_ids": [
                    item_id for item_id in item.get("kb_ids", [])
                    if item_id in valid_kb_ids
                ],
            }
            for item in out.get("items", [])
            if isinstance(item, dict) and item.get("title")
        ]
        return {
            "summary": str(out.get("summary", "")),
            "decisions": [str(decision) for decision in out.get("decisions", [])],
            "items": items,
            "ai_used": True,
        }
    except Exception as exc:
        log.warning("recap_fallback", extra={"ctx": {"reason": type(exc).__name__}})
        return _fallback(notes, participants, kb)


def _fallback(notes, participants, kb):
    lines = [line.strip(" -*•\t") for line in notes.splitlines() if line.strip()]
    decisions = [
        re.sub(r"^(decision|decided)[:\- ]*", "", line, flags=re.I)
        for line in lines if re.match(r"(decision|decided)", line, re.I)
    ]
    items = []
    for line in lines:
        if (
            re.search(
                r"^(action|todo|to-do)|\bwill\b|\bto (draft|finish|send|prepare|review|update|write|share)\b|@\w+",
                line, re.I,
            )
            and not re.match(r"(decision|decided)", line, re.I)
        ):
            owner = next((person for person in participants if person.lower() in line.lower()), "")
            words = set(re.findall(r"\w{4,}", line.lower()))
            hits = [
                doc["id"] for doc in kb
                if words & set(re.findall(
                    r"\w{4,}", (doc["title"] + " " + (doc["tags"] or "")).lower()
                ))
            ]
            items.append({
                "title": re.sub(r"^(action|todo)[:\- ]*", "", line, flags=re.I)[:300],
                "owner": owner,
                "due": "",
                "kb_ids": hits[:2],
            })
    return {
        "summary": " ".join(lines[:2])[:400] or "No notes provided.",
        "decisions": decisions,
        "items": items,
        "ai_used": False,
    }


BRIEF_SYS = (
    "You write a concise catch-up brief (max 180 words) for a person who missed a meeting "
    "or is taking over work. Structure: What happened / Decisions / What you now own "
    "(flag risky or overdue items first) / Read first (KB docs). Plain text, no preamble."
)


def brief(person, ctx):
    try:
        return {
            "brief": call(
                "brief",
                BRIEF_SYS,
                f"Person: {person}\n{json.dumps(ctx, default=str)}",
                700,
            ).strip(),
            "ai_used": True,
        }
    except Exception:
        parts = [f"Brief for {person} (AI unavailable - generated from records)."]
        for meeting in ctx["meetings"]:
            parts.append(
                f"• {meeting['title']}: {meeting['summary']}  "
                f"Decisions: {'; '.join(meeting['decisions']) or 'none'}"
            )
        parts += [
            f"• You own: {item['title']} (due {item['due'] or 'n/a'}, risk {item['risk']})"
            for item in ctx["open_items"]
        ]
        return {"brief": "\n".join(parts), "ai_used": False}
