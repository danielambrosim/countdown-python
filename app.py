
from __future__ import annotations
import os
import string
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from flask import Flask, render_template, request, redirect, url_for, jsonify, abort
from sqlalchemy import Column, Integer, String, DateTime, create_engine, select, UniqueConstraint
from sqlalchemy.orm import declarative_base, Session


# -------------------- Config --------------------
def normalize_db_url(url: str) -> str:
    if not url:
        return "sqlite:///events.db"

    # usar driver psycopg (v3)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    # garantir SSL no Render
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    p = urlparse(url)
    q = dict(parse_qsl(p.query))
    if (p.hostname or "").endswith(".render.com") and "sslmode" not in q:
        q["sslmode"] = "require"
        p = p._replace(query=urlencode(q))
        url = urlunparse(p)
    return url

DB_URL = normalize_db_url(os.getenv("DATABASE_URL", "sqlite:///events.db"))

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

Base = declarative_base()
engine = create_engine(DB_URL, future=True, echo=False, pool_pre_ping=True)

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    slug = Column(String(40), unique=True, nullable=False, index=True)
    title = Column(String(200), nullable=False)
    until_utc = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("slug", name="uq_events_slug"),)

Base.metadata.create_all(engine)


# -------------------- Helpers --------------------
ALPHABET = string.ascii_lowercase + string.digits
def gen_slug(n: int = 6) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(n))

def ensure_unique_slug(session: Session, n: int = 6) -> str:
    # Try a few times in the extremely unlikely case of collision
    for _ in range(10):
        s = gen_slug(n)
        exists = session.scalar(select(Event).where(Event.slug == s))
        if not exists:
            return s
    # As a fallback, make a longer slug
    return ensure_unique_slug(session, n + 1)

def parse_iso_utc(iso: str) -> datetime:
    # Accept strings like "2025-09-22T15:30:00Z" or with "+00:00"
    if iso.endswith("Z"):
        iso = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        # Assume UTC if no tz provided
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# -------------------- Routes --------------------
@app.get("/")
def index():
    # Show recent events (last 8) for convenience
    with Session(engine) as s:
        events = s.scalars(select(Event).order_by(Event.created_at.desc()).limit(8)).all()
    server_utc_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return render_template("index.html", events=events, server_utc_now=server_utc_now)

@app.post("/create")
def create():
    title = (request.form.get("title") or "").strip()
    until_iso = (request.form.get("until_iso") or "").strip()
    if not title or not until_iso:
        abort(400, "Título e data/hora são obrigatórios.")
    try:
        until_utc = parse_iso_utc(until_iso)
    except Exception as e:
        abort(400, f"Data/hora inválida: {e}")

    with Session(engine) as s:
        slug = ensure_unique_slug(s, 6)
        ev = Event(slug=slug, title=title[:200], until_utc=until_utc)
        s.add(ev)
        s.commit()
        return redirect(url_for("event_page", slug=slug))

@app.get("/e/<slug>")
def event_page(slug: str):
    with Session(engine) as s:
        ev: Optional[Event] = s.scalar(select(Event).where(Event.slug == slug))
        if not ev:
            abort(404)
        server_utc_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        until_iso = ev.until_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return render_template("event.html",
                               title=ev.title,
                               slug=ev.slug,
                               until_iso=until_iso,
                               server_utc_now=server_utc_now)

@app.get("/api/time")
def api_time():
    return jsonify(utc_now=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

# Health check (useful for Render/Railway)
@app.get("/health")
def health():
    return {"ok": True}

# Entry point for local dev
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
