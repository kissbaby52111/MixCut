import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from mixcut.common import PipelineError, video_stream_duration
from mixcut.draft_timing import bounded_source_duration, shift_packaging_start


class DraftTimingTests(unittest.TestCase):
    def test_video_stream_duration_excludes_container_audio_tail(self):
        info={"streams":[{"codec_type":"video","duration":"81.000000","duration_ts":2430,"time_base":"1/30"}],
            "format":{"duration":"81.013991"}}
        with patch("mixcut.common.media_info",return_value=info):
            self.assertEqual(video_stream_duration(Path("sample.mp4")),81)

    def test_video_duration_falls_back_when_stream_ticks_are_unavailable(self):
        info={"streams":[{"codec_type":"video","duration":"14.766667","duration_ts":"N/A","time_base":"0/0"}],
            "format":{"duration":"14.768005"}}
        with patch("mixcut.common.media_info",return_value=info):
            self.assertEqual(video_stream_duration(Path("sample.mp4")),14.766667)

    def test_millisecond_overrun_is_bounded_and_large_difference_is_reported(self):
        self.assertEqual(bounded_source_duration(81_013_991,81_000_000,"sample.mp4"),81_000_000)
        self.assertEqual(bounded_source_duration(80_000_000,81_000_000,"sample.mp4"),80_000_000)
        with self.assertRaisesRegex(PipelineError,"超过 0.1 秒"):
            bounded_source_duration(82_000_000,81_000_000,"sample.mp4")

    def test_shift_moves_all_tail_layers_and_music_without_reselecting(self):
        original={"seed":"same-seed","start_us":81_013_991,"duration_us":1_020_000_000,
            "effects":[{"key":str(i),"start_us":81_013_991,"duration_us":1_020_000_000} for i in range(5)],
            "music":[{"id":"first","start_us":81_013_991,"duration_us":300_000_000},
                {"id":"second","start_us":381_013_991,"duration_us":720_000_000}]}
        before=copy.deepcopy(original)
        changed=shift_packaging_start(original,81_000_000)
        self.assertEqual(original,before)
        self.assertEqual(changed["seed"],before["seed"])
        self.assertTrue(all(item["start_us"]==81_000_000 for item in changed["effects"]))
        self.assertEqual(changed["music"][1]["start_us"],381_000_000)
        self.assertEqual([item["duration_us"] for item in changed["music"]],[300_000_000,720_000_000])
