"""安全工作帳本與限量短期裁切快取；不保存Key、URL或HTTP原文。"""

import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from meowkeeper.core.event_semantics import (
    TAXONOMY_VERSION,
    SemanticRequest,
    SemanticResult,
    TagEvidence,
)
from meowkeeper.core.semantic_storyboard import TrackStoryboard


def provider_identity(provider):
    values = []
    for name in ("provider_id", "model_id"):
        value = getattr(provider, name, "unknown")
        values.append(value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value) else "unknown")
    return tuple(values)


def restore_result(payload):
    evidence = tuple(TagEvidence(row["track_id"], row["code"], tuple(row["evidence_frame_indices"]),
                                 tuple(row["observed_offsets_seconds"]), row["certainty"],row.get("zone_ref"),row.get("view_ref"))
                     for row in payload.get("tag_evidence", []))
    transport = payload.get("transport", {})
    return SemanticResult(payload["status"], tuple((row["track_id"], tuple(row["tags"])) for row in payload["tracks"]),
                          payload["job_id"], tuple(payload.get("timings", {}).items()), evidence,
                          transport.get("request_dispatched"), transport.get("http_status"),
                          tuple(payload.get("analysis_source", {}).items()))


class SemanticJobJournal:
    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    def register(self, request, job_id, provider, ttl):
        provider_id, model_id = provider_identity(provider)
        config = getattr(provider, "config_reference", "default")
        if not isinstance(config, str) or len(config) > 128:
            config = "unknown"
        reference_digest = (hashlib.sha256(request.scene_reference_jpeg).hexdigest()
                            if request.scene_reference_jpeg is not None else None)
        key = hashlib.sha256(json.dumps([request.event_id, request.track_ids, hashlib.sha256(request.jpeg).hexdigest(),
                                        request.track_epoch, request.input_revision, TAXONOMY_VERSION,
                                        reference_digest, request.scene_reference_labels,
                                        provider_id, model_id, config], sort_keys=True).encode()).hexdigest()
        def operation():
            cursor = self.store.connection.execute(
                "INSERT OR IGNORE INTO semantic_work_jobs(job_id,job_key,event_id,provider_id,model_id,state,queued_at,deadline_at) VALUES(?,?,?,?,?,'queued',?,?)",
                (job_id, key, request.event_id, provider_id, model_id, datetime.now(timezone.utc).isoformat(), self.clock()+ttl))
            return cursor.rowcount == 1
        # submit位於事件排程端：不可繼承一般資料寫入的5秒busy重試。
        if not self.store._connection_lock.acquire(timeout=.01):
            raise TimeoutError("分析帳本忙碌，這次不外送。")
        previous_timeout = None
        try:
            previous_timeout = self.store.connection.execute("PRAGMA busy_timeout").fetchone()[0]
            self.store.connection.execute("PRAGMA busy_timeout=20")
            self.store.connection.execute("BEGIN IMMEDIATE")
            accepted = operation()
            self.store.connection.commit()
            return accepted
        except Exception:
            self.store.connection.rollback()
            raise
        finally:
            if previous_timeout is not None:
                self.store.connection.execute(f"PRAGMA busy_timeout={int(previous_timeout)}")
            self.store._connection_lock.release()

    def start(self, job_id):
        def operation():
            return self.store.connection.execute(
                "UPDATE semantic_work_jobs SET state='running' WHERE job_id=? AND state='queued' AND deadline_at>?",
                (job_id, self.clock())).rowcount == 1
        return self.store._transaction(operation)

    def save_input(self, job_id, request):
        # 只保存已裁切且通過時序品質gate的拼貼，不保存原始全圖、URL或Key。
        total_bytes = sum(len(board.jpeg) for board in request.storyboards)
        if request.scene_reference_jpeg is not None:
            total_bytes += len(request.scene_reference_jpeg)
        byte_limit = 1_750 * 1024 if request.scene_reference_jpeg is not None else 2 * 1024 * 1024
        if not request.storyboards or total_bytes > byte_limit:
            return
        payload = {"tracks":request.track_ids, "trigger_at":request.trigger_at.isoformat() if request.trigger_at else None,
                   "epoch":request.track_epoch, "boards":[{"track":board.track_id,"alias":board.alias,"offsets":board.offsets_seconds,"epoch":board.epoch,
                                                          "zones":board.zones,"jpeg":base64.b64encode(board.jpeg).decode("ascii")} for board in request.storyboards],
                   "scene_reference_jpeg":base64.b64encode(request.scene_reference_jpeg).decode("ascii") if request.scene_reference_jpeg else None,
                   "scene_reference_labels":request.scene_reference_labels}
        self.store._write("UPDATE semantic_work_jobs SET input_json=? WHERE job_id=?", (json.dumps(payload),job_id))
        self.store._write("UPDATE semantic_work_jobs SET input_json=NULL WHERE julianday(queued_at)<julianday('now','-1 day') OR job_id NOT IN (SELECT job_id FROM semantic_work_jobs WHERE input_json IS NOT NULL ORDER BY queued_at DESC,rowid DESC LIMIT 8)")

    def prepare_manual(self, event_id, request_id):
        request_id = str(UUID(request_id))
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                prior = self.store.connection.execute("SELECT event_id,job_id,input_revision FROM semantic_reanalysis_requests WHERE request_id=?", (request_id,)).fetchone()
                if prior:
                    if prior[0] != event_id:
                        raise ValueError("此提交識別屬於其他事件。")
                    if self.store.connection.execute("SELECT 1 FROM semantic_work_jobs WHERE job_id=?",(prior[1],)).fetchone():
                        return prior[1], None
                recent = self.store.connection.execute("SELECT MAX(requested_at),MAX(input_revision) FROM semantic_reanalysis_requests WHERE event_id=?", (event_id,)).fetchone()
                if prior is None and recent[0] is not None and self.clock()-recent[0] < 30:
                    raise ValueError("同一事件每30秒最多手動分析一次。")
                row = self.store.connection.execute("SELECT input_json FROM semantic_work_jobs WHERE event_id=? AND input_json IS NOT NULL AND julianday(queued_at)>=julianday('now','-1 day') ORDER BY queued_at DESC,rowid DESC LIMIT 1", (event_id,)).fetchone()
                if row is None or len(row[0]) > 3*1024*1024:
                    raise ValueError("已無可安全重用的裁切時序證據；不以目前畫面假冒舊事件。")
                value = json.loads(row[0])
                boards = tuple(TrackStoryboard(board["track"],board["alias"],base64.b64decode(board["jpeg"],validate=True),tuple(board["offsets"]),board["epoch"],tuple(tuple(zone) for zone in board.get("zones",[]))) for board in value["boards"])
                if not 1 <= len(boards) <= 8:
                    raise ValueError("舊事件輸入無法驗證。")
                revision = prior[2] if prior else (recent[1] or 1)+1
                scene_reference = (base64.b64decode(value["scene_reference_jpeg"], validate=True)
                                   if value.get("scene_reference_jpeg") else None)
                request = SemanticRequest(event_id,boards[0].jpeg,tuple(value["tracks"]),
                    datetime.fromisoformat(value["trigger_at"]) if value["trigger_at"] else None,
                    boards,value["epoch"],revision,scene_reference,tuple(value.get("scene_reference_labels", ())))
                job_id = prior[1] if prior else str(uuid4())
                if prior is None:
                    self.store.connection.execute("INSERT INTO semantic_reanalysis_requests(request_id,event_id,job_id,input_revision,requested_at) VALUES(?,?,?,?,?)", (request_id,event_id,job_id,revision,self.clock()))
                return job_id, request
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(operation)

    def finish(self, job_id, result):
        self.store._write("UPDATE semantic_work_jobs SET state=?,terminal_reason=COALESCE(terminal_reason,?),result_json=? WHERE job_id=? AND result_json IS NULL",
                          (result.status, result.status, json.dumps(result.payload(), ensure_ascii=False, allow_nan=False), job_id))

    def reason(self, job_id, reason):
        if not isinstance(reason,str) or not re.fullmatch(r"[a-z_]{1,64}",reason):
            raise ValueError("工作終止原因不正確。")
        self.store._write("UPDATE semantic_work_jobs SET terminal_reason=COALESCE(terminal_reason,?) WHERE job_id=?",(reason,job_id))

    def request_cancel(self, event_id, job_id):
        UUID(job_id)
        def operation():
            row = self.store.connection.execute("SELECT state,result_json FROM semantic_work_jobs WHERE event_id=? AND job_id=?",(event_id,job_id)).fetchone()
            if row is None:
                raise ValueError("找不到此事件的分析工作。")
            if row[1] is not None:
                return False
            cursor=self.store.connection.execute("UPDATE semantic_work_jobs SET cancel_requested=1,terminal_reason='user_cancelled' WHERE job_id=? AND result_json IS NULL",(job_id,))
            return cursor.rowcount==1
        return self.store._transaction(operation)

    def cancelled(self, job_id):
        with self.store._connection_lock:
            row = self.store.connection.execute("SELECT cancel_requested FROM semantic_work_jobs WHERE job_id=?",(job_id,)).fetchone()
        return bool(row and row[0])

    def runtime(self):
        self.recover(self.store.record_event_semantics)
        with self.store._connection_lock:
            rows = self.store.connection.execute("SELECT job_id,event_id,provider_id,model_id,state,queued_at,cancel_requested,terminal_reason FROM semantic_work_jobs WHERE result_json IS NULL ORDER BY queued_at LIMIT 33").fetchall()
        return {"jobs":[dict(row) for row in rows[:32]],"truncated":len(rows)>32}

    def recover(self, sink):
        # 租期過後保守終止，不猜測已收費的請求能否重送。不保存/恢復私人影格。
        with self.store._connection_lock:
            expired = self.store.connection.execute(
                "SELECT job_id,state FROM semantic_work_jobs WHERE result_json IS NULL AND deadline_at<=? LIMIT 32",
                (self.clock(),)).fetchall()
        for row in expired:
            self.reason(row[0],"ttl_expired_recovery")
            result = SemanticResult("cancelled", job_id=row[0],
                                    request_dispatched=False if row[1] == "queued" else None)
            self.finish(row[0], result)
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT job_id,event_id,result_json FROM semantic_work_jobs WHERE projected=0 AND result_json IS NOT NULL LIMIT 32").fetchall()
        for row in rows:
            result = restore_result(json.loads(row[2]))
            sink(row[1], result)
            self.store._write("UPDATE semantic_work_jobs SET projected=1 WHERE job_id=?", (row[0],))
        # 僅清理已投影且超過30日的安全帳本，候選/人工歷史仍按既有保留政策。
        self.store._write("DELETE FROM semantic_work_jobs WHERE projected=1 AND julianday(queued_at)<julianday('now','-30 days')")
        self.store._write("UPDATE semantic_work_jobs SET input_json=NULL WHERE input_json IS NOT NULL AND julianday(queued_at)<julianday('now','-1 day')")

    def list_event(self, event_id):
        self.recover(self.store.record_event_semantics)
        with self.store._connection_lock:
            return [dict(row) for row in self.store.connection.execute(
                "SELECT job_id,provider_id,model_id,state,queued_at,projected,cancel_requested,terminal_reason FROM semantic_work_jobs WHERE event_id=? ORDER BY queued_at LIMIT 100", (event_id,))]
