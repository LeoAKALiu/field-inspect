"""Durable single-worker station queue; item reports remain immutable."""
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .instruments import encoded
from .station_processing import VERSION, process
from .writer_lock import ImportWriterLock, WriterLockHeld

SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_jobs (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, version TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processing_job_items (
 job_id TEXT NOT NULL, seq INTEGER NOT NULL, attempt_id TEXT NOT NULL,
 capture_seq INTEGER NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 report_id TEXT, error TEXT, PRIMARY KEY(job_id,seq)
);
CREATE TABLE IF NOT EXISTS processing_job_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, item_seq INTEGER,
 status TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS processing_job_run ON processing_jobs(run_id,created_at);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def event(conn, job_id, item_seq, status, detail=""):
    conn.execute("INSERT INTO processing_job_events(job_id,item_seq,status,detail,created_at) VALUES(?,?,?,?,?)",
                 (job_id,item_seq,status,detail,now()))


def describe(db, job_id):
    row = db.query_one("SELECT * FROM processing_jobs WHERE id=?", (job_id,))
    if row is None:
        raise ValueError("unknown processing job")
    counts = {r["status"]:r["n"] for r in db.query_all("SELECT status,COUNT(*) n FROM processing_job_items WHERE job_id=? GROUP BY status", (job_id,))}
    return {**dict(row), "counts":counts, "total":sum(counts.values())}


def create(db, run_id):
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM run_bundle_imports WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError("unknown archived run")
        items = set()
        for row in conn.execute("SELECT payload FROM instrument_records WHERE kind='station_evidence' AND run_id=? ORDER BY id", (run_id,)):
            evidence = json.loads(row["payload"])
            for capture in evidence["captures"]:
                if capture["state"] == "captured":
                    items.add((evidence["attempt_id"],capture["capture_attempt_seq"]))
                    if len(items)>10000:
                        raise ValueError("batch exceeds 10000 captures")
        if not items:
            raise ValueError("no indexed successful captures; reindex station evidence first")
        items = sorted(items)
        job_id = "batch-"+hashlib.sha256(encoded({"run":run_id,"version":VERSION,"items":items}).encode()).hexdigest()[:24]
        if conn.execute("SELECT 1 FROM processing_jobs WHERE id=?", (job_id,)).fetchone() is None:
            conn.execute("INSERT INTO processing_jobs VALUES(?,?,?,?,?,?)", (job_id,run_id,VERSION,"queued",now(),now()))
            conn.executemany("INSERT INTO processing_job_items(job_id,seq,attempt_id,capture_seq,status) VALUES(?,?,?,?, 'queued')",
                             [(job_id,index,attempt,seq) for index,(attempt,seq) in enumerate(items,1)])
            event(conn,job_id,None,"created","Frozen capture list and processor version")
    return describe(db,job_id)


def control(db, job_id, action):
    with db.transaction() as conn:
        job = conn.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise ValueError("unknown processing job")
        if action == "pause":
            if job["status"] in {"queued","running"}:
                conn.execute("UPDATE processing_jobs SET status='paused',updated_at=? WHERE id=?", (now(),job_id))
        else:
            if job["version"] != VERSION:
                raise ValueError("processor version changed; create a new batch")
            if action == "retry_failed":
                conn.execute("UPDATE processing_job_items SET status='queued',error=NULL WHERE job_id=? AND status='failed'", (job_id,))
            if conn.execute("SELECT 1 FROM processing_job_items WHERE job_id=? AND status IN ('queued','running')", (job_id,)).fetchone():
                conn.execute("UPDATE processing_jobs SET status='queued',updated_at=? WHERE id=?", (now(),job_id))
        event(conn,job_id,None,action)
    return describe(db,job_id)


class Worker:
    def __init__(self, db, import_root):
        self.db = db
        self.root = Path(import_root)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="station-processing", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join()  # Finish current item before the shared database is closed.

    def run(self):
        while not self.stopped.is_set():
            try:
                with ImportWriterLock(self.root / ".station-worker"):
                    with self.db.transaction() as conn:
                        for item in conn.execute("SELECT job_id,seq FROM processing_job_items WHERE status='running'").fetchall():
                            event(conn,item["job_id"],item["seq"],"recovered","Interrupted item queued again; report identity remains idempotent")
                        conn.execute("UPDATE processing_job_items SET status='queued' WHERE status='running'")
                        conn.execute("UPDATE processing_jobs SET status='queued',updated_at=? WHERE status='running'", (now(),))
                    while not self.stopped.is_set():
                        if not self.step():
                            self.stopped.wait(2)
            except WriterLockHeld:
                self.stopped.wait(2)
            except Exception:
                logging.getLogger(__name__).exception("Station worker paused after unexpected queue failure")
                self.stopped.wait(5)

    def step(self):
        with self.db.transaction() as conn:
            job = conn.execute("SELECT * FROM processing_jobs WHERE status IN ('queued','running') ORDER BY created_at,id LIMIT 1").fetchone()
            if job is None:
                return False
            if job["version"] != VERSION:
                conn.execute("UPDATE processing_jobs SET status='paused',updated_at=? WHERE id=?", (now(),job["id"]))
                event(conn,job["id"],None,"version_mismatch","Create a new batch with the current processor")
                return True
            item = conn.execute("SELECT * FROM processing_job_items WHERE job_id=? AND status='queued' ORDER BY seq LIMIT 1", (job["id"],)).fetchone()
            if item is None:
                issues = conn.execute("SELECT 1 FROM processing_job_items WHERE job_id=? AND status IN ('failed','blocked') LIMIT 1", (job["id"],)).fetchone()
                status = "completed_with_issues" if issues else "completed"
                conn.execute("UPDATE processing_jobs SET status=?,updated_at=? WHERE id=?", (status,now(),job["id"]))
                event(conn,job["id"],None,status)
                return True
            conn.execute("UPDATE processing_job_items SET status='running',attempts=attempts+1,error=NULL WHERE job_id=? AND seq=?", (job["id"],item["seq"]))
            conn.execute("UPDATE processing_jobs SET status='running',updated_at=? WHERE id=?", (now(),job["id"]))
            event(conn,job["id"],item["seq"],"running")
        report_id = error = None
        try:
            report = process(self.db,item["attempt_id"],item["capture_seq"])
            report_id = report["report_id"]
            status = "blocked" if report["status"] == "blocked" else "succeeded"
        except WriterLockHeld:
            status,error = "queued","writer_busy"
        except Exception as exc:
            status,error = "failed",f"{type(exc).__name__}: {exc}"[:1000]
            logging.getLogger(__name__).exception("Station batch item failed: %s/%s",job["id"],item["seq"])
        with self.db.transaction() as conn:
            conn.execute("UPDATE processing_job_items SET status=?,report_id=?,error=? WHERE job_id=? AND seq=?", (status,report_id,error,job["id"],item["seq"]))
            conn.execute("UPDATE processing_jobs SET updated_at=? WHERE id=?", (now(),job["id"]))
            event(conn,job["id"],item["seq"],status,error or report_id or "")
        if status == "queued":
            self.stopped.wait(2)
        return True
