import shutil
import subprocess

import pytest

from kids_clips.concept import BLOCKLIST, Concept, offline_concept, review_concept
from kids_clips.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_offline_pipeline_makes_vertical_video_with_audio(tmp_path):
    from kids_clips.agent import produce_one
    from kids_clips.history import History

    s = Settings()
    s.out_dir = tmp_path
    s.target_seconds = 8
    s.music_provider = s.video_provider = "local"
    entry = produce_one(s, offline=True, theme="dinosaurs")

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "csv=p=0",
         entry["video"]], capture_output=True, text=True, check=True).stdout
    assert "video,1080,1920" in out
    assert "audio" in out
    assert History(tmp_path).recent_titles() == [entry["title"]]


def test_blocklist_rejects_known_ip_without_calling_claude():
    c = offline_concept()
    bad = Concept(**{**c.model_dump(), "title": "Baby Shark dance remix"})
    review = review_concept(client=None, model="unused", concept=bad)
    assert not review.approved
    assert BLOCKLIST.search(bad.title)
