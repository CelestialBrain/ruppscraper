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
        assert clean_scraped_name("WFX Castaneda", "Roann") == ("Castaneda", "Roann")
        assert clean_scraped_name("THY Santos", "Ana") == ("Santos", "Ana")

    def test_keeps_de_di_del_surnames(self):
        assert clean_scraped_name("De La Rosa", "Ma. Cecilia") == (
            "De La Rosa",
            "Ma. Cecilia",
        )
        assert clean_scraped_name("Del Rosario", "Juan") == ("Del Rosario", "Juan")
        assert clean_scraped_name("Di Angelo", "Maria") == ("Di Angelo", "Maria")

    def test_strips_trailing_first_name_noise(self):
        assert clean_scraped_name("Santos", "Rolando Email") == ("Santos", "Rolando")
        assert clean_scraped_name("Garcia", "Kenneth Arwin Prerog") == (
            "Garcia",
            "Kenneth Arwin",
        )
        assert clean_scraped_name("Cruz", "Maria Notes") == ("Cruz", "Maria")
        assert clean_scraped_name("Reyes", "Jose pls") == ("Reyes", "Jose")

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
        assert not is_plausible_professor_name("Venue", "Looking For")
        assert not is_plausible_professor_name("Email", "Leander P. Marquez's")
        assert not is_plausible_professor_name("Boydon", "Kai Brynne Or Saddi, Daryl Allen")
        assert not is_plausible_professor_name("Serra", "Patrick James Vs. Abejo, Raymund")
        assert not is_plausible_professor_name("De", "Juan")
        assert not is_plausible_professor_name("Garcia@gmail.com", "Kenneth")
        assert not is_plausible_professor_name("Suggestions", "'yung Chill Sana")
        assert not is_plausible_professor_name("Thesis", "I Need Respondents For My Master's")
        assert not is_plausible_professor_name("(plata, Alcasid", "Cabrera)")
        assert not is_plausible_professor_name(":'>", "Help Save Class")

    def test_accepts_real(self):
        assert is_plausible_professor_name("Garcia", "Mark Lester")
        assert is_plausible_professor_name("Dela Cruz", "Juan")
        assert is_plausible_professor_name("De La Rosa", "Ma. Cecilia")


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
