"""Unit tests for the r/RateUPProfs title parser."""

import pytest

from scraper.parser import ParsedTitle, parse_title


class TestParseTitle:
    """Test suite for parse_title()."""

    # ------------------------------------------------------------------
    # Standard formats
    # ------------------------------------------------------------------

    def test_standard_upd(self):
        result = parse_title("[UPD] Fil 40 - Dela Cruz, Juan")
        assert result is not None
        assert result.campus == "UPD"
        assert result.course == "Fil 40"
        assert result.last_name == "Dela Cruz"
        assert result.first_name == "Juan"

    def test_standard_uplb(self):
        result = parse_title("[UPLB] Nat Sci 2 - Santos, Maria")
        assert result is not None
        assert result.campus == "UPLB"
        assert result.course == "Nat Sci 2"
        assert result.last_name == "Santos"
        assert result.first_name == "Maria"

    def test_standard_upm(self):
        result = parse_title("[UPM] Bio 1 - Reyes, Jose")
        assert result is not None
        assert result.campus == "UPM"
        assert result.course == "Bio 1"
        assert result.last_name == "Reyes"
        assert result.first_name == "Jose"

    def test_standard_upou(self):
        result = parse_title("[UPOU] Ethics 1 - Garcia, Ana")
        assert result is not None
        assert result.campus == "UPOU"
        assert result.course == "Ethics 1"

    # ------------------------------------------------------------------
    # All-caps professor names (common in the subreddit)
    # ------------------------------------------------------------------

    def test_all_caps_name(self):
        result = parse_title("[UPD] Speech 30 - REDELICIA, ROMEO JOSHUA")
        assert result is not None
        assert result.last_name == "Redelicia"
        assert result.first_name == "Romeo Joshua"
        assert result.course == "Speech 30"

    # ------------------------------------------------------------------
    # Multi-word course codes
    # ------------------------------------------------------------------

    def test_multi_word_course(self):
        result = parse_title("[UPLB] Speech Comm 11 - Lomongo, Michael Ian")
        assert result is not None
        assert result.course == "Speech Comm 11"
        assert result.last_name == "Lomongo"
        assert result.first_name == "Michael Ian"

    def test_long_course_name(self):
        result = parse_title("[UPD] Kas 1 - Torres, Antonio")
        assert result is not None
        assert result.course == "Kas 1"

    # ------------------------------------------------------------------
    # Hyphenated and compound names
    # ------------------------------------------------------------------

    def test_hyphenated_last_name(self):
        result = parse_title("[UPM] Bio 1 - Santos-Reyes, Maria Clara")
        assert result is not None
        assert result.last_name == "Santos-Reyes"
        assert result.first_name == "Maria Clara"

    def test_compound_last_name(self):
        result = parse_title("[UPD] Philo 1 - De La Cruz, Juan")
        assert result is not None
        assert result.last_name == "De La Cruz"
        assert result.first_name == "Juan"

    # ------------------------------------------------------------------
    # Name abbreviations and suffixes
    # ------------------------------------------------------------------

    def test_ma_abbreviation(self):
        result = parse_title("[UPD] CWTS 1 - Diola, Ma. Teresa")
        assert result is not None
        assert result.first_name == "Ma. Teresa"

    def test_jr_suffix(self):
        result = parse_title("[UPD] Math 17 - Santos, Juan Jr.")
        assert result is not None
        assert result.first_name == "Juan Jr."

    def test_iii_suffix(self):
        result = parse_title("[UPLB] Chem 16 - Reyes, Carlos III")
        assert result is not None
        assert result.first_name == "Carlos Iii"

    # ------------------------------------------------------------------
    # Parenthetical annotations
    # ------------------------------------------------------------------

    def test_with_ay_parenthetical(self):
        result = parse_title("[UPD] Math 17 - Santos, Juan (AY 2024-2025)")
        assert result is not None
        assert result.course == "Math 17"
        assert result.last_name == "Santos"
        assert result.first_name == "Juan"

    def test_with_semester_parenthetical(self):
        result = parse_title("[UPD] Econ 11 - Cruz, Maria (1st Sem)")
        assert result is not None
        assert result.first_name == "Maria"

    # ------------------------------------------------------------------
    # Whitespace edge cases
    # ------------------------------------------------------------------

    def test_extra_whitespace(self):
        result = parse_title("[UPD]  Fil 40  -  Dela Cruz ,  Juan")
        assert result is not None
        assert result.campus == "UPD"
        assert result.course == "Fil 40"
        assert result.last_name == "Dela Cruz"
        assert result.first_name == "Juan"

    def test_leading_trailing_whitespace(self):
        result = parse_title("  [UPD] Fil 40 - Dela Cruz, Juan  ")
        assert result is not None
        assert result.campus == "UPD"

    # ------------------------------------------------------------------
    # Unknown / uncommon campus codes
    # ------------------------------------------------------------------

    def test_unknown_campus_code(self):
        """Unknown codes are not real campuses — don't invent a professor row."""
        result = parse_title("[UPXYZ] Math 1 - Test, User")
        assert result is None

    def test_fake_campus_coerced_to_upd(self):
        result = parse_title("[REVIEW] Econ 11 - Jandoc, Karl Robert")
        assert result is not None
        assert result.campus == "UPD"
        assert result.last_name == "Jandoc"

    def test_diliman_alias(self):
        result = parse_title("[DILIMAN] PE 2 - Guinto, Maria Luisa")
        assert result is not None
        assert result.campus == "UPD"

    def test_upmin_campus(self):
        result = parse_title("[UPMin] Hist 1 - Garcia, Pedro")
        assert result is not None
        assert result.campus == "UPMin"

    # ------------------------------------------------------------------
    # Non-conforming titles → should return None
    # ------------------------------------------------------------------

    def test_no_brackets(self):
        result = parse_title("UPD Fil 40 - Dela Cruz, Juan")
        assert result is None

    def test_no_dash_separator(self):
        result = parse_title("[UPD] Fil 40 Dela Cruz, Juan")
        assert result is not None
        assert result.course == "Fil 40"
        assert result.last_name == "Dela Cruz"
        assert result.first_name == "Juan"

    def test_no_comma(self):
        result = parse_title("[UPD] Fil 40 - Dela Cruz Juan")
        assert result is not None
        assert result.course == "Fil 40"
        # Informal no-comma: last token is surname
        assert result.last_name == "Juan"
        assert result.first_name == "Dela Cruz"

    def test_empty_string(self):
        result = parse_title("")
        assert result is None

    def test_random_text(self):
        result = parse_title("Rate my profs for this semester please")
        assert result is None

    def test_compilation_post(self):
        result = parse_title("Rate my enlistment lineup")
        assert result is None

    # ------------------------------------------------------------------
    # Professor ID normalization
    # ------------------------------------------------------------------

    def test_professor_id(self):
        result = parse_title("[UPD] Speech 30 - REDELICIA, ROMEO JOSHUA")
        assert result is not None
        assert result.professor_id == "upd__redelicia__romeo_joshua"

    def test_professor_full(self):
        result = parse_title("[UPD] Speech 30 - REDELICIA, ROMEO JOSHUA")
        assert result is not None
        assert result.professor_full == "Redelicia, Romeo Joshua"

    # ------------------------------------------------------------------
    # Case insensitivity for campus code
    # ------------------------------------------------------------------

    def test_lowercase_campus(self):
        result = parse_title("[upd] Math 17 - Santos, Juan")
        assert result is not None
        assert result.campus == "UPD"

    def test_mixed_case_campus(self):
        result = parse_title("[Uplb] Chem 16 - Reyes, Ana")
        assert result is not None
        assert result.campus == "UPLB"

    # ------------------------------------------------------------------
    # Alternative format fallbacks
    # ------------------------------------------------------------------

    def test_swapped_format(self):
        result = parse_title("[UPD] Dela Cruz, Juan - Fil 40")
        assert result is not None
        assert result.campus == "UPD"
        assert result.course == "Fil 40"
        assert result.last_name == "Dela Cruz"
        assert result.first_name == "Juan"

    def test_unbracketed_course_first(self):
        result = parse_title("Speech 30 - Redelicia, Romeo Joshua")
        assert result is not None
        assert result.campus == "UPD"
        assert result.course == "Speech 30"
        assert result.last_name == "Redelicia"

    def test_multi_prof_title(self):
        result = parse_title("[UPD] Math 21 - Neri, Marrick; Buhain, Carmelo")
        assert result is not None
        assert result.last_name == "Neri"
        assert result.first_name == "Marrick"

    def test_no_comma_real_titles(self):
        result = parse_title("[UPD] Math 22 - Arvin Lamando")
        assert result is not None
        assert result.course == "Math 22"
        assert result.last_name == "Lamando"
        assert result.first_name == "Arvin"

        result = parse_title("[UPM] CMSC 11 - May Ann Grace Puquiz Palisoc")
        assert result is not None
        assert result.last_name == "Palisoc"
        assert result.first_name == "May Ann Grace Puquiz"

    def test_no_dash_all_caps_name(self):
        result = parse_title("[UPD] SOC SCI 2 PAGUIRIGAN, MARK RYAN")
        assert result is not None
        assert result.course == "SOC SCI 2"
        assert result.last_name == "Paguirigan"
        assert result.first_name == "Mark Ryan"

    def test_no_dash_with_course_number(self):
        result = parse_title("[UPD] PA 141 Diñgal, Ian Kenneth")
        assert result is not None
        assert result.course == "PA 141"
        assert result.last_name == "Diñgal"
        assert result.first_name == "Ian Kenneth"

    def test_rejects_conversational_false_positive(self):
        result = parse_title(
            "[UPD] Sa mga nakakuha ng Philo - 1, Kailan First F2f Class Niyo?"
        )
        assert result is None

    def test_rejects_classmate_hunt(self):
        assert parse_title("[UPD] Socio 101 WFX - looking for classmates") is None
        assert parse_title("[UPD] Math 22 X7 - Looking for classmates!") is None

    def test_rejects_inverted_meta_titles(self):
        assert parse_title("[UPD] Film 100 - Looking for venue") is None
        assert parse_title("[UPD] French 10 THW - Looking for the classroom") is None
        assert parse_title("[UPD] Fil 40 - Thoughts on these profs") is None
        assert parse_title("[UPD] MATH 10 - BOYDON, KAI BRYNNE or SADDI, DARYL ALLEN") is None

    def test_strips_section_code_from_name(self):
        result = parse_title("[UPD] Span 10 WFX: Cruel, Jevic")
        assert result is not None
        assert result.last_name == "Cruel"
        assert result.first_name == "Jevic"

    def test_strips_honorific(self):
        result = parse_title("[UPD] EDCO 101 Teacher CAUYAN, JACLYN MARIE")
        assert result is not None
        assert result.last_name == "Cauyan"
        assert result.first_name == "Jaclyn Marie"

    def test_en_dash_separator(self):
        result = parse_title("[UPD] Physics 71 – Pagayon, Julius")
        assert result is not None
        assert result.course == "Physics 71"
        assert result.last_name == "Pagayon"
        assert result.first_name == "Julius"

    def test_embedded_course_dash_before_name(self):
        result = parse_title(
            "[UPD] PE 2 - PHILIPPINE GAMES - BERNALES, GABRIELLE MIKAELA"
        )
        assert result is not None
        assert result.last_name == "Bernales"
        assert result.first_name == "Gabrielle Mikaela"

    def test_keeps_de_surname_particle(self):
        result = parse_title("[UPD] Fil 40 - De La Rosa, Ma. Cecilia")
        assert result is not None
        assert result.last_name == "De La Rosa"
        assert result.first_name == "Ma. Cecilia"

    def test_strips_trailing_first_name_noise(self):
        result = parse_title("[UPD] Math 17 - Santos, Rolando Email")
        assert result is not None
        assert result.last_name == "Santos"
        assert result.first_name == "Rolando"

        result = parse_title("[UPD] Math 17 - Garcia, Kenneth Arwin Prerog")
        assert result is not None
        assert result.last_name == "Garcia"
        assert result.first_name == "Kenneth Arwin"
