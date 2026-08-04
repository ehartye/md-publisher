"""Heading-slug and internal-anchor tests.

These exist because hand-authored tables of contents kept breaking: the
slugifier and the markdown renderers an author actually reads the `.md`
in disagreed about punctuation, so `[Section](#anchor)` links silently
resolved to nothing while the PDF still built successfully.
"""

from lib.toc import assign_heading_ids, find_broken_anchors, slugify


class TestSlugifyMatchesGitHub:
    """Slugs must match github-slugger so one TOC works in .md and PDF."""

    def test_plain_heading(self):
        assert slugify("Data Model") == "data-model"

    def test_lowercases(self):
        assert slugify("HIGH-Level Architecture") == "high-level-architecture"

    def test_drops_leading_number_period(self):
        assert slugify("8. Data Model") == "8-data-model"

    def test_ampersand_leaves_double_hyphen(self):
        # "&" is removed but both surrounding spaces become hyphens.
        # Collapsing to a single hyphen is the historical bug.
        assert slugify("1. Purpose & Scope") == "1-purpose--scope"

    def test_slash_leaves_double_hyphen(self):
        assert slugify("13. Open Questions / Assumptions") == (
            "13-open-questions--assumptions"
        )

    def test_colon_is_dropped(self):
        assert slugify("18. Summary: Effort and Cost") == "18-summary-effort-and-cost"

    def test_comma_is_dropped(self):
        assert slugify("15. Operational Logging, Completeness & Exception Handling") == (
            "15-operational-logging-completeness--exception-handling"
        )

    def test_apostrophe_is_dropped(self):
        assert slugify("2. The Problem We're Solving") == "2-the-problem-were-solving"

    def test_parentheses_are_dropped(self):
        assert slugify("7. Pipeline Design (Stage by Stage)") == (
            "7-pipeline-design-stage-by-stage"
        )

    def test_existing_hyphens_survive(self):
        assert slugify("Follow-Up Runs") == "follow-up-runs"

    def test_unicode_is_folded_to_ascii(self):
        assert slugify("Café Résumé") == "cafe-resume"

    def test_empty_falls_back(self):
        assert slugify("!!!") == "section"


class TestAssignHeadingIds:
    def test_assigns_slug_when_absent(self):
        html, headings = assign_heading_ids("<h2>Effort &amp; Cost Estimate</h2>")
        assert 'id="effort--cost-estimate"' in html
        assert headings[0].slug == "effort--cost-estimate"

    def test_respects_explicit_id(self):
        # attr_list overrides remain the escape hatch for a specific anchor.
        html, headings = assign_heading_ids('<h2 id="custom">Whatever</h2>')
        assert headings[0].slug == "custom"
        assert html.count("id=") == 1

    def test_duplicate_headings_get_distinct_ids(self):
        _, headings = assign_heading_ids("<h2>Summary</h2><h2>Summary</h2>")
        assert [h.slug for h in headings] == ["summary", "summary-1"]


class TestFindBrokenAnchors:
    def test_resolving_link_is_not_reported(self):
        html = '<h2 id="data-model">Data Model</h2><a href="#data-model">go</a>'
        assert find_broken_anchors(html) == []

    def test_missing_target_is_reported(self):
        assert find_broken_anchors('<a href="#nope">go</a>') == ["nope"]

    def test_percent_encoded_anchor_is_reported(self):
        # The regression that motivated this check: "%3A" never matches a
        # literal ":" id, because fragments are compared without decoding.
        html = '<h2 id="a:b">t</h2><a href="#a%3Ab">go</a>'
        assert find_broken_anchors(html) == ["a%3Ab"]

    def test_each_broken_target_reported_once(self):
        html = '<a href="#x">a</a><a href="#x">b</a>'
        assert find_broken_anchors(html) == ["x"]

    def test_external_links_are_ignored(self):
        assert find_broken_anchors('<a href="https://example.com#frag">e</a>') == []


class TestRoundTrip:
    """A TOC written against slugify() must resolve against the built HTML."""

    def test_hand_authored_toc_resolves(self):
        titles = [
            "1. Purpose & Scope",
            "13. Open Questions / Assumptions",
            "15. Operational Logging, Completeness & Exception Handling",
            "18. Summary: Effort and Cost",
        ]
        toc = "".join(f'<a href="#{slugify(t)}">{t}</a>' for t in titles)
        body = "".join(f"<h2>{t}</h2>" for t in titles)
        html, _ = assign_heading_ids(body)
        assert find_broken_anchors(toc + html) == []
