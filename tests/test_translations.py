import re

from review_app.app.translations import TRANSLATIONS, t


class TestTranslationKeyParity:
    def test_all_languages_have_same_keys(self):
        en_keys = set(TRANSLATIONS["en"].keys())
        fr_keys = set(TRANSLATIONS["fr"].keys())

        missing_in_fr = en_keys - fr_keys
        missing_in_en = fr_keys - en_keys

        assert not missing_in_fr, f"Keys missing in French: {missing_in_fr}"
        assert not missing_in_en, f"Keys missing in English: {missing_in_en}"

    def test_no_empty_translations(self):
        for lang, entries in TRANSLATIONS.items():
            for key, value in entries.items():
                assert value, f"Empty translation for {lang}/{key}"


class TestTranslationPlaceholders:
    def test_same_placeholder_names_per_key(self):
        en = TRANSLATIONS["en"]
        fr = TRANSLATIONS["fr"]

        for key in en:
            en_val = en[key]
            fr_val = fr.get(key, "")
            if en_val is None or fr_val is None:
                continue

            en_names = set(re.findall(r"\{(\w+)\}", en_val))
            fr_names = set(re.findall(r"\{(\w+)\}", fr_val))

            assert en_names == fr_names, (
                f"Placeholder mismatch for '{key}': en={en_names}, fr={fr_names}"
            )


class TestTFunction:
    def test_returns_english_by_default(self, monkeypatch):
        monkeypatch.setattr("review_app.app.translations.get_language", lambda: "en")
        result = t("app_title")
        assert result == TRANSLATIONS["en"]["app_title"]

    def test_returns_french_when_language_is_fr(self, monkeypatch):
        monkeypatch.setattr("review_app.app.translations.get_language", lambda: "fr")
        result = t("app_title")
        assert result == TRANSLATIONS["fr"]["app_title"]

    def test_fallback_to_english_for_unknown_language(self, monkeypatch):
        monkeypatch.setattr("review_app.app.translations.get_language", lambda: "zz")
        result = t("app_title")
        assert result == TRANSLATIONS["en"]["app_title"]

    def test_fallback_to_key_when_key_unknown(self, monkeypatch):
        monkeypatch.setattr("review_app.app.translations.get_language", lambda: "en")
        result = t("nonexistent_key_xyz")
        assert result == "nonexistent_key_xyz"

    def test_handles_format_kwargs(self, monkeypatch):
        monkeypatch.setattr("review_app.app.translations.get_language", lambda: "en")
        result = t("sync_processing", current=5, total=10, filename="video.mp4")
        assert "5" in result
        assert "10" in result
        assert "video.mp4" in result
        assert "{" not in result

    def test_review_later_in_both_languages(self, monkeypatch):
        for lang in ("en", "fr"):
            monkeypatch.setattr("review_app.app.translations.get_language", lambda la=lang: la)
            result = t("marked_review_later")
            assert result == TRANSLATIONS[lang]["marked_review_later"]
            assert len(result) > 0


class TestTranslationCoverage:
    """Verify error message keys used in AppError subclasses exist in translations."""

    def test_apperror_subclass_keys_exist(self):
        from review_app.backend.errors import (
            AppError,
            DataImportError,
            SpeciesError,
            VideoError,
        )

        keys = [
            AppError.user_message_key,
            DataImportError.user_message_key,
            SpeciesError.user_message_key,
            VideoError.user_message_key,
        ]
        for key in keys:
            assert key in TRANSLATIONS["en"], f"Key '{key}' missing from English translations"
            assert key in TRANSLATIONS["fr"], f"Key '{key}' missing from French translations"
