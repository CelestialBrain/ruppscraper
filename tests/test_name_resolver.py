"""Tests for blead-ported professor name helpers."""

from __future__ import annotations

from scraper.name_resolver import (
    ProfNameResolver,
    clean_scraped_name,
    generate_variants,
    is_plausible_professor_name,
    normalize_name,
    split_name_parts,
)


class TestNormalize:
    def test_accent_fold(self):
        assert normalize_name("Castañeda") == "castaneda"
        assert normalize_name("GARCIA, MARK LESTER B.") == "garcia mark lester b"


class TestSplitNameParts:
    def test_comma(self):
        assert split_name_parts("Garcia, Mark Lester") == ("Garcia", "Mark Lester")

    def test_particle_surname(self):
        assert split_name_parts("Juan Dela Cruz") == ("Dela Cruz", "Juan")

    def test_plain(self):
        assert split_name_parts("Mark Garcia") == ("Garcia", "Mark")


class TestCleanScrapedName:
    def test_section_prefix(self):
        assert clean_scraped_name("1 Francisco", "Ana") == ("Francisco", "Ana")
        assert clean_scraped_name("2 - Castaneda", "Roann") == ("Castaneda", "Roann")

    def test_honorific(self):
        assert clean_scraped_name("Sir Garcia", "Mark") == ("Garcia", "Mark")


class TestPlausible:
    def test_rejects_junk(self):
        assert not is_plausible_professor_name("Prerogative", "11")
        assert not is_plausible_professor_name("?", "What")
        assert not is_plausible_professor_name("Math", "22")
        assert not is_plausible_professor_name("Garcia", "")
        assert not is_plausible_professor_name("Espanola", "Carmela And Orozco, Zenith")
        assert not is_plausible_professor_name("Villegas", "Patrick / Fil 40")

    def test_accepts_real(self):
        assert is_plausible_professor_name("Garcia", "Mark Lester")
        assert is_plausible_professor_name("Dela Cruz", "Juan")


class TestVariantsAndResolver:
    def test_generate_variants(self):
        variants, parsed = generate_variants("GARCIA, MARK LESTER B.")
        assert parsed["last_name"] == "garcia"
        assert parsed["first_name"] == "mark"
        assert "garcia mark lester" in variants
        assert "mark garcia" in variants

    def test_mentions_and_attribute(self):
        resolver = ProfNameResolver()
        resolver.load_professors(["Garcia, Mark Lester", "Santos, Ana"])
        assert resolver.mentions_prof("had Sir Garcia last sem", "Garcia, Mark Lester") >= 0.3
        assert (
            resolver.attribute_comment(
                "take Sir Garcia if you can",
                ["Garcia, Mark Lester", "Santos, Ana"],
            )
            == "Garcia, Mark Lester"
        )
