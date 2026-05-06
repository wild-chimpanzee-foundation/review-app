class TestHasAiAnnotationsCheck:
    """The review-page tour checks ``dp._get_model_annotations_df().empty``
    to decide which text to show for the AI-predictions step."""

    def test_empty_when_no_model_annotations_exist(self, provider_with_project):
        dp, *_ = provider_with_project
        assert dp._get_model_annotations_df().empty is True

    def test_non_empty_when_model_annotations_exist(self, populated_provider):
        dp, *_ = populated_provider
        assert dp._get_model_annotations_df().empty is False
