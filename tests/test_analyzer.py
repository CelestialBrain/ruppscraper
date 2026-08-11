"""Unit tests for the review signal & keyword analyzer."""

import pytest

from scraper.analyzer import analyze_text


class TestAnalyzer:
    """Test suite for analyze_text()."""

    def test_unoable_and_engaging(self):
        text = "Super unoable prof! Activities are very engaging and clear PPT slides."
        signals = analyze_text(text)
        assert signals.dominant_grading == "generous"
        assert "unoable" in signals.keywords_found
        assert "engaging" in signals.keywords_found
        assert signals.pedagogy_mentions["engaging"] >= 1
        assert signals.pedagogy_mentions["clear"] >= 1

    def test_heavy_workload_strict_grading(self):
        text = "Heavy workload with so many requirements. Strict grader and zero uno given."
        signals = analyze_text(text)
        assert signals.dominant_workload == "heavy"
        assert signals.dominant_grading == "strict"
        assert "heavy workload" in signals.keywords_found
        assert "strict grader" in signals.keywords_found

    def test_light_workload_and_lenient_attendance(self):
        text = "Very light workload, manageable tasks, and optional attendance. Recorded sessions available."
        signals = analyze_text(text)
        assert signals.dominant_workload == "light"
        assert signals.attendance_mentions["lenient"] >= 1
        assert "light workload" in signals.keywords_found

    def test_empty_text(self):
        signals = analyze_text("")
        assert signals.dominant_workload is None
        assert signals.dominant_grading is None
        assert len(signals.keywords_found) == 0
