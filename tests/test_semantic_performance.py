"""100筆合成SQLite紀錄的分母與nearest-rank百分位，不是真實性能。"""

import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.analytics.behavior_summary import summarize_behavior
from meowkeeper.core.analytics.semantic_performance import (
    distribution,
    summarize_semantic_performance,
)
from meowkeeper.core.event_semantics import SemanticResult
from meowkeeper.core.events.web_review import WebEventReview
from meowkeeper.storage.database import SQLiteStore


class SemanticPerformanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SQLiteStore(self.root / "events.db")
        self.addCleanup(self.store.close)

    def populate(self):
        base = datetime(2026, 10, 15, tzinfo=timezone.utc)
        for index in range(100):
            event = str(uuid4())
            trigger = base + timedelta(minutes=index)
            self.store.create_media_event(event_id=event, event_type="cat_detected", trigger_at=trigger,
                source_id="synthetic", track_id=None, pet_id="unknown", confidence=.8,
                requested_pre_seconds=5, requested_post_seconds=10)
            job = str(uuid4())
            status, sent, http = (("completed", True, 200) if index < 70 else
                                 ("timeout", True, None) if index < 80 else
                                 ("rate_limited", True, 429) if index < 90 else
                                 ("skipped_budget", False, None) if index < 95 else ("failed", None, None))
            timing = [("queue_ms", index), ("finished_at", (trigger+timedelta(seconds=10 if index%2 else 1)).isoformat())]
            if http is not None:
                timing.append(("api_response_ms", index+1))
            result = SemanticResult(status, (("scene", ("walking",)),) if status == "completed" else (),
                                    job, tuple(timing), request_dispatched=sent, http_status=http)
            self.store.record_event_semantics(event, result)
            self.store.record_delivery_trace(event, {"trace_id": str(uuid4()), "semantic_job_id": job,
                "event_trigger_at": trigger.isoformat(), "media_sendable_at": trigger.isoformat(),
                "wait_started_at": trigger.isoformat(), "wait_finished_at": (trigger+timedelta(seconds=5)).isoformat(),
                "video_prepare_ms": 1000+index, "validation_transcode_ms": 10,
                "extra_wait_ms": index*10, "wait_budget_ms": 5000,
                "caption_source": "local" if index%2 or status != "completed" else "ai_candidate",
                "wait_status": status, "omitted_upload_count": 0,
                "uploads": [{"recipient_ref": "a"*64, "attempt": 1, "started_at": trigger.isoformat(),
                             "finished_at": trigger.isoformat(), "elapsed_ms": 200+index, "accepted": index < 95}]})

    def test_100_events_means_percentiles_and_denominators(self):
        self.populate()
        report = summarize_semantic_performance(self.store)
        self.assertEqual(report["event_count"], 100)
        self.assertEqual(report["analysis_count"], 100)
        self.assertEqual(report["confirmed_request_count"], 90)
        self.assertEqual(report["unknown_request_count"], 5)
        self.assertEqual(report["not_dispatched_count"], 5)
        self.assertEqual(report["skipped_count"], 5)
        self.assertAlmostEqual(report["api_success_rate"], 70/90)
        self.assertEqual(report["metrics"]["api_response_ms"], {"sample_count":80,"mean_ms":41.75,"p50_ms":40,"p95_ms":86})
        self.assertEqual(report["metrics"]["extra_wait_ms"], {"sample_count":100,"mean_ms":495,"p50_ms":490,"p95_ms":940})
        self.assertEqual((report["candidate_caption_linked_count"],report["candidate_caption_missed_count"],report["late_candidate_count"]), (70,35,35))
        self.assertEqual(report["upload_acceptance_rate"], .95)
        self.assertFalse(report["truncated"])
        replay = report["grace_replay"]
        self.assertEqual([row["candidate_available_count"] for row in replay], [0,35,70])
        self.assertEqual([row["sample_count"] for row in replay], [100,100,100])
        self.assertEqual(replay[0]["extra_wait"]["mean_ms"],0)

    def test_time_window_reads_beyond_recent_limit_and_separates_models(self):
        now = datetime.now(timezone.utc)
        for index in range(30):
            event = str(uuid4())
            self.store.create_media_event(event_id=event,event_type="cat_detected",trigger_at=now-timedelta(minutes=index),
                source_id="fixture",track_id=None,pet_id=None,confidence=.8,requested_pre_seconds=5,requested_post_seconds=10)
            result = SemanticResult("completed", (("scene",("sitting",)),), str(uuid4()),
                                    (("api_response_ms",10+index),), request_dispatched=True, http_status=200,
                                    provenance=(("provider_id","fixture"),("model_id","one" if index%2 else "two")))
            self.store.record_event_semantics(event,result)
        self.assertEqual(summarize_semantic_performance(self.store,limit=25)["event_count"],25)
        report = summarize_semantic_performance(self.store,limit=25,hours=1)
        self.assertEqual(report["event_count"],30)
        self.assertEqual(report["scope"],"time_window")
        self.assertEqual([row["analysis_count"] for row in report["provider_models"]],[15,15])
        self.assertTrue(all(row["success_rate"] == 1 for row in report["provider_models"]))
        for value in (True,0,2,721):
            with self.assertRaises(ValueError):
                summarize_semantic_performance(self.store,hours=value)

    def test_empty_invalid_and_unobserved_samples_are_not_zero_percent(self):
        report = summarize_semantic_performance(self.store)
        self.assertEqual(report["status"], "no_data")
        self.assertIsNone(report["api_success_rate"])
        self.assertEqual(report["metrics"]["api_response_ms"]["sample_count"], 0)
        self.assertIsNone(report["metrics"]["api_response_ms"]["p95_ms"])
        self.assertEqual(distribution([None,True,float("nan"),-1,1e308,1e308])["sample_count"], 0)
        for value in (0,201,True,"100"):
            with self.assertRaises(ValueError):
                summarize_semantic_performance(self.store, limit=value)

    def test_statistics_endpoint_keeps_login_and_limit_validation(self):
        app = create_app(facade=CoreStateFacade(store=self.store,event_directory=self.root / "events"), token="s"*32)
        with TestClient(app) as client:
            url = "/api/v1/analytics/semantics"
            self.assertEqual(client.get(url).status_code, 401)
            header = {"Authorization":"Bearer "+"s"*32}
            self.assertEqual(client.get(url,headers=header).json()["status"], "no_data")
            self.assertEqual(client.get(url+"?limit=201",headers=header).status_code,422)
            self.assertNotIn(hashlib.sha256(b"synthetic").hexdigest(), client.get(url,headers=header).text)

    def test_corrupt_records_and_test_events_do_not_make_false_success_rates(self):
        self.populate()
        self.store.connection.execute("DROP TRIGGER semantic_revision_no_update")
        self.store.connection.execute("UPDATE event_semantic_revisions SET candidate_json=? WHERE rowid=(SELECT MIN(rowid) FROM event_semantic_revisions)",
                                      ('{"status":"completed","tracks":null}',))
        self.store.connection.commit()
        report = summarize_semantic_performance(self.store)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["invalid_record_count"], 1)
        self.assertIsNone(report["api_success_rate"])
        self.store.create_media_event(event_id=str(uuid4()), event_type="cat_detected", trigger_at=datetime(2030,1,1,tzinfo=timezone.utc),
            source_id="synthetic", track_id=None, pet_id="unknown", confidence=.8,
            requested_pre_seconds=5, requested_post_seconds=10, trigger_origin="simulated")
        self.assertEqual(summarize_semantic_performance(self.store)["event_count"], 100)

    def test_browser_utc_z_dates_are_accepted_without_loosening_range(self):
        result = summarize_behavior(self.store, "2026-10-15T00:00:00.000Z", "2026-10-16T00:00:00.000Z")
        self.assertEqual(result["status"], "no_data")
        with self.assertRaises(ValueError):
            summarize_behavior(self.store, "2026-10-15T00:00:00", "2026-10-16T00:00:00")
        with self.assertRaises(ValueError):
            summarize_behavior(self.store, "2026-10-01T00:00:00Z", "2026-12-01T00:00:00Z")

    def test_custom_window_filters_and_daily_trend_keep_denominators_separate(self):
        self.populate()
        report = summarize_semantic_performance(self.store,start="2026-10-15T00:00:00Z",end="2026-10-15T00:30:00Z",
                                               provider="unknown",input_mode="unknown",validity_filter="unreviewed")
        self.assertEqual(report["event_count"],30)
        self.assertEqual(report["matched_event_count"],30)
        self.assertEqual(report["analysis_count"],30)
        self.assertEqual(report["delivery_trace_count"],30)
        self.assertEqual(report["confirmed_request_count"],30)
        self.assertEqual(report["daily_trend"][0]["analysis_count"],30)
        self.assertEqual(report["daily_trend"][0]["date_utc"],"2026-10-15")
        self.assertTrue(report["filters_applied"])
        empty = summarize_semantic_performance(self.store,provider="never_used")
        self.assertEqual(empty["event_count"],100)
        self.assertEqual(empty["analysis_count"],0)
        self.assertEqual(empty["delivery_trace_count"],0)
        self.assertIsNone(empty["api_success_rate"])
        for kwargs in ({"start":"2026-10-15T00:00:00Z"},
                       {"start":"2026-10-15T00:00:00","end":"2026-10-16T00:00:00"},
                       {"start":"2026-10-01T00:00:00Z","end":"2026-12-01T00:00:00Z"},
                       {"provider":"bad?secret=value"},{"input_mode":"video"},{"validity_filter":"all"}):
            with self.assertRaises(ValueError):
                summarize_semantic_performance(self.store,**kwargs)

    def test_invalid_event_does_not_remove_real_api_failures_or_attempts(self):
        self.populate()
        event = self.store.connection.execute("SELECT event_id FROM event_semantic_revisions WHERE status='completed' LIMIT 1").fetchone()[0]
        service = WebEventReview(self.store,None)
        actor = "a"*64
        service.review_validity(event,actor,{"request_id":str(uuid4()),"expected_revision":0,"validity":"valid"})
        report = summarize_semantic_performance(self.store)
        self.assertEqual(report["quality_groups"]["valid"]["analysis_count"],1)
        payload = {"request_id":str(uuid4()),"expected_revision":1,"validity":"invalid"}
        service.review_validity(event,actor,payload)
        service.review_validity(event,actor,payload)
        report = summarize_semantic_performance(self.store)
        self.assertEqual(report["confirmed_request_count"],90)
        self.assertAlmostEqual(report["api_success_rate"],70/90)
        self.assertEqual(report["quality_groups"]["invalid"]["analysis_count"],1)
        self.assertEqual(report["quality_groups"]["unreviewed"]["analysis_count"],99)
        self.assertIsNone(report["usage_cost"])
        service.review_validity(event,"b"*64,{"request_id":str(uuid4()),"expected_revision":0,"validity":"valid"})
        self.assertEqual(summarize_semantic_performance(self.store)["quality_groups"]["conflict"]["analysis_count"],1)
