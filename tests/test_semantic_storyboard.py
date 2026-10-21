import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from meowkeeper.core.event_semantics import SemanticRequest, normalize_result
from meowkeeper.core.semantic_storyboard import (
    FrameEvidence,
    FrameEvidenceBuffer,
    build_storyboards,
)


def jpeg(number):
    output = BytesIO()
    Image.new("RGB", (96, 96), (number * 30, 80, 90)).save(output, format="JPEG")
    return output.getvalue()


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        self.trigger = datetime.now(timezone.utc)
        self.samples = tuple(FrameEvidence(track, "epoch", self.trigger + timedelta(seconds=index * .3), jpeg(index))
                             for track in ("local-track-a", "local-track-b") for index in range(4))

    def request(self):
        tracks = ("local-track-a", "local-track-b")
        boards = build_storyboards(self.samples, tracks, self.trigger)
        return SemanticRequest("e", b"jpeg", tracks, self.trigger, boards)

    def test_real_numbered_jpeg_maps_two_tracks_and_coarse_time(self):
        request = self.request()
        self.assertEqual(len(request.storyboards), 2)
        for board in request.storyboards:
            with Image.open(BytesIO(board.jpeg)) as image:
                self.assertEqual(image.size, (1536, 1024))
                self.assertEqual(image.format, "JPEG")
            self.assertEqual(board.offsets_seconds, (0, .3, .6, .9))
        raw = {"tracks": [{"track_alias": alias, "tags": [{"code": "walking", "certainty": "low",
                    "evidence_frame_indices": [0, 2]}]} for alias in ("track_02", "track_01")]}
        result = normalize_result(raw, request)
        self.assertEqual([track for track, _tags in result.tracks], list(request.track_ids))
        self.assertEqual(result.payload()["tag_evidence"][0]["time_quality"], "coarse")
        self.assertNotIn("jpeg", str(result.payload()))

    def test_user_quality_options_change_real_image_and_metadata(self):
        view = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        samples = tuple(replace(sample, zones=((view, "food_zone", "food"),)) for sample in self.samples)
        boards = build_storyboards(samples, ("local-track-a",), self.trigger, frame_count=2,
                                   canvas_size="1280x960", jpeg_quality=75,
                                   include_timestamps=False, include_zones=False)
        self.assertEqual(len(boards), 1)
        self.assertEqual(len(boards[0].offsets_seconds), 2)
        self.assertEqual(boards[0].zones, ())
        with Image.open(BytesIO(boards[0].jpeg)) as image:
            self.assertEqual(image.size, (1280, 960))

    def test_quality_option_bounds_fail_closed(self):
        self.assertEqual(build_storyboards(self.samples, ("local-track-a",), self.trigger,
                                            frame_count=9), ())
        self.assertEqual(build_storyboards(self.samples, ("local-track-a",), self.trigger,
                                            jpeg_quality=96), ())

    def test_unknown_alias_wrong_index_missing_track_and_precise_time_are_rejected(self):
        request = self.request()
        valid = {"tracks": [{"track_alias": alias, "tags": []} for alias in ("track_01", "track_02")]}
        for tag in ({"code": "walking", "certainty": "high", "evidence_frame_indices": [0, 9]},
                    {"code": "walking", "certainty": "high", "evidence_frame_indices": [True, 1]},
                    {"code": "walking", "certainty": "high", "evidence_frame_indices": [1]},
                    {"code": "walking", "certainty": "high", "evidence_frame_indices": [0, 1], "start_ms": 0}):
            raw = {"tracks": [dict(valid["tracks"][0], tags=[tag]), valid["tracks"][1]]}
            with self.assertRaises(ValueError):
                normalize_result(raw, request)
        with self.assertRaises(ValueError):
            normalize_result({"tracks": valid["tracks"][:1]}, request)
        with self.assertRaises(ValueError):
            normalize_result({"tracks": [dict(valid["tracks"][0], track_alias="invented"), valid["tracks"][1]]}, request)

    def test_duplicate_image_mixed_epoch_missing_cat_and_bad_jpeg_never_build(self):
        tracks = ("local-track-a", "local-track-b")
        self.assertEqual(build_storyboards(self.samples[:3], tracks, self.trigger), ())
        mixed = self.samples + (FrameEvidence(tracks[0], "new-epoch", self.trigger, jpeg(5)),)
        self.assertEqual(build_storyboards(mixed, tracks, self.trigger), ())
        duplicate = tuple(FrameEvidence(tracks[0], "epoch", self.trigger + timedelta(seconds=i), jpeg(0)) for i in range(4))
        self.assertEqual(build_storyboards(duplicate, tracks[:1], self.trigger), ())
        bad = tuple(FrameEvidence(tracks[0], "epoch", self.trigger + timedelta(seconds=i * .3), bytes([i])) for i in range(4))
        self.assertEqual(build_storyboards(bad, tracks[:1], self.trigger), ())

    def test_spatial_candidate_needs_consistent_local_zone_at_every_evidence_frame(self):
        view = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        samples = tuple(replace(sample,zones=((view,"water_zone","water"),)) for sample in self.samples)
        boards = build_storyboards(samples,("local-track-a","local-track-b"),self.trigger)
        request = replace(self.request(),storyboards=boards)
        raw = {"tracks":[{"track_alias":alias,"tags":[{"code":"near_water","certainty":"medium","evidence_frame_indices":[0,2]}]}
                         for alias in ("track_01","track_02")]}
        result = normalize_result(raw,request)
        self.assertEqual(result.evidence[0].zone_ref,"water_zone")
        self.assertEqual(result.evidence[0].view_ref,view)
        self.assertNotIn("drinking",result.tracks[0][1])
        for zones in ((),((0,view,"water_zone","water"),),
                      ((0,view,"water_zone","water"),(2,view,"different_zone","water"))):
            bad_boards = (replace(boards[0],zones=zones),boards[1])
            with self.assertRaises(ValueError):
                normalize_result(raw,replace(request,storyboards=bad_boards))

    def test_background_collection_uses_future_observations_without_blocking_observe(self):
        buffer = FrameEvidenceBuffer()
        collected = []
        worker = threading.Thread(target=lambda: collected.append(buffer.collect(("local-track-a",), self.trigger)))
        worker.start()
        started = time.monotonic()
        for sample in self.samples[:4]:
            buffer.observe((sample,))
        self.assertLess(time.monotonic() - started, .1)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(collected[0]), 1)
        buffer.close()
        self.assertEqual(buffer.collect(("local-track-a",), self.trigger, wait_seconds=0), ())

    def test_maximum_wait_and_memory_are_bounded(self):
        buffer = FrameEvidenceBuffer()
        sample = self.samples[0]
        for _ in range(100):
            buffer.observe((sample,))
        self.assertLessEqual(len(buffer._samples), 64)
        self.assertLessEqual(buffer._bytes, 8 * 1024 * 1024)
        with self.assertRaises(ValueError):
            buffer.collect(("t",), self.trigger, wait_seconds=3)
        buffer.close()


if __name__ == "__main__":
    unittest.main()
