class TestHasAiAnnotationsCheck:
    """The review-page tour checks ``dp.has_model_annotations()``
    to decide which text to show for the AI-predictions step."""

    def test_false_when_no_model_annotations_exist(self, provider_with_project):
        dp, *_ = provider_with_project
        assert dp.has_model_annotations() is False

    def test_true_when_model_annotations_exist(self, populated_provider):
        dp, *_ = populated_provider
        assert dp.has_model_annotations() is True
