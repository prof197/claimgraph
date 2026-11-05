"""Parser tests on recorded records and on synthetic XML shaped like the PubMed DTD."""

from pathlib import Path

import pytest

from claimgraph.retrieve import (
    Abstract,
    Dropped,
    DropReason,
    EutilsError,
    parse_record,
    split_articles,
)


def article(
    *,
    pmid: str = "999000001",
    title: str = "Synthetic trial of placeholder supplementation.",
    abstract: str | None = "<AbstractText>Synthetic abstract text.</AbstractText>",
    other_abstract: str | None = None,
    language: str = "eng",
    authors: str = "<Author><LastName>Synthetic</LastName><Initials>A</Initials></Author>",
    pub_date: str = "<Year>2021</Year><Month>Mar</Month>",
    article_date: str = "",
    publication_types: tuple[str, ...] = ("Journal Article",),
    mesh: tuple[str, ...] = (),
    databanks: str = "",
    journal: str = "<Title>Journal of Synthetic Studies</Title>"
    "<ISOAbbreviation>J Synth Stud</ISOAbbreviation>",
    pubmed_year: str = "2021",
) -> bytes:
    abstract_xml = "" if abstract is None else f"<Abstract>{abstract}</Abstract>"
    other_xml = other_abstract or ""
    types = "".join(f"<PublicationType>{t}</PublicationType>" for t in publication_types)
    headings = "".join(
        f"<MeshHeading><DescriptorName MajorTopicYN='N'>{m}</DescriptorName></MeshHeading>"
        for m in mesh
    )
    mesh_xml = f"<MeshHeadingList>{headings}</MeshHeadingList>" if mesh else ""
    return f"""<PubmedArticle>
  <MedlineCitation Status="MEDLINE" Owner="NLM">
    <PMID Version="1">{pmid}</PMID>
    <Article PubModel="Print">
      <Journal>
        <JournalIssue CitedMedium="Internet"><PubDate>{pub_date}</PubDate></JournalIssue>
        {journal}
      </Journal>
      <ArticleTitle>{title}</ArticleTitle>
      {abstract_xml}
      <AuthorList CompleteYN="Y">{authors}</AuthorList>
      <Language>{language}</Language>
      {databanks}
      <PublicationTypeList>{types}</PublicationTypeList>
      {article_date}
    </Article>
    {mesh_xml}
    {other_xml}
  </MedlineCitation>
  <PubmedData>
    <History>
      <PubMedPubDate PubStatus="pubmed"><Year>{pubmed_year}</Year><Month>1</Month></PubMedPubDate>
    </History>
  </PubmedData>
</PubmedArticle>""".encode()


def parsed(xml: bytes) -> Abstract:
    record = parse_record(xml)
    assert isinstance(record, Abstract)
    return record


def dropped(xml: bytes) -> Dropped:
    record = parse_record(xml)
    assert isinstance(record, Dropped)
    return record


# Kept records


def test_keeps_the_citation_fields() -> None:
    record = parsed(
        article(
            publication_types=("Journal Article", "Randomized Controlled Trial"),
            mesh=("Vitamin D", "Depression"),
        )
    )

    assert record.pmid == "999000001"
    assert record.title == "Synthetic trial of placeholder supplementation."
    assert record.first_author == "Synthetic"
    assert record.journal == "J Synth Stud"
    assert record.year == 2021
    assert record.language == "eng"
    assert record.publication_types == ["Journal Article", "Randomized Controlled Trial"]
    assert record.mesh == ["Vitamin D", "Depression"]
    assert record.trial_ids == []


def test_unstructured_abstract_is_one_unlabelled_section() -> None:
    record = parsed(article())

    assert [(s.label, s.category, s.text) for s in record.sections] == [
        (None, None, "Synthetic abstract text.")
    ]
    assert record.text == "Synthetic abstract text."


def test_structured_abstract_keeps_labelled_sections() -> None:
    record = parsed(
        article(
            abstract=(
                "<AbstractText Label='BACKGROUND' NlmCategory='BACKGROUND'>Synthetic aim."
                "</AbstractText>"
                "<AbstractText Label='RESULTS' NlmCategory='RESULTS'>Synthetic result."
                "</AbstractText>"
                "<CopyrightInformation>Synthetic copyright line.</CopyrightInformation>"
            )
        )
    )

    assert [(s.label, s.category) for s in record.sections] == [
        ("BACKGROUND", "BACKGROUND"),
        ("RESULTS", "RESULTS"),
    ]
    assert record.text == "BACKGROUND: Synthetic aim.\nRESULTS: Synthetic result."


def test_inline_markup_and_whitespace_are_flattened() -> None:
    record = parsed(
        article(
            title="Synthetic <i>title</i> with 25(OH)D<sub>3</sub>.",
            abstract=(
                "<AbstractText>Serum   25(OH)D<sub>3</sub>\n  rose by <b>5</b> units."
                "</AbstractText>"
            ),
        )
    )

    assert record.title == "Synthetic title with 25(OH)D3."
    assert record.text == "Serum 25(OH)D3 rose by 5 units."


def test_english_abstract_on_a_non_english_article_is_kept() -> None:
    record = parsed(article(language="ger", title="[Synthetic translated title]."))

    assert record.language == "ger"


def test_group_author_stands_in_for_a_person() -> None:
    record = parsed(
        article(authors="<Author><CollectiveName>Synthetic Study Group</CollectiveName></Author>")
    )

    assert record.first_author == "Synthetic Study Group"


def test_no_authors() -> None:
    assert parsed(article(authors="")).first_author is None


def test_journal_without_abbreviation_uses_its_title() -> None:
    record = parsed(article(journal="<Title>Journal of Synthetic Studies</Title>"))

    assert record.journal == "Journal of Synthetic Studies"


@pytest.mark.parametrize(
    ("pub_date", "article_date", "year"),
    [
        ("<Year>2019</Year>", "", 2019),
        ("<MedlineDate>1998 Dec-1999 Jan</MedlineDate>", "", 1998),
        (
            "<Season>Spring</Season>",
            "<ArticleDate DateType='Electronic'><Year>2017</Year></ArticleDate>",
            2017,
        ),
        ("<Season>Spring</Season>", "", 2021),
    ],
)
def test_year_falls_back_in_order(pub_date: str, article_date: str, year: int) -> None:
    record = parsed(article(pub_date=pub_date, article_date=article_date, pubmed_year="2021"))

    assert record.year == year


def databank_list(*banks: tuple[str, str]) -> str:
    entries = "".join(
        f"<DataBank><DataBankName>{name}</DataBankName><AccessionNumberList>"
        f"<AccessionNumber>{number}</AccessionNumber></AccessionNumberList></DataBank>"
        for name, number in banks
    )
    return f"<DataBankList CompleteYN='Y'>{entries}</DataBankList>"


def test_trial_ids_come_from_every_registry_and_the_text() -> None:
    databanks = databank_list(
        ("ClinicalTrials.gov", "NCT99000002"),
        ("ISRCTN", "ISRCTN99000003"),
        ("IRCT", " irct99000004n1 "),
        ("UMIN CTR", "UMIN99000005"),
    )
    abstract = "<AbstractText>Registered as NCT99000001 and NCT99000002 (synthetic).</AbstractText>"

    record = parsed(article(databanks=databanks, abstract=abstract))

    assert record.trial_ids == [
        "CLINICALTRIALS.GOV:NCT99000001",
        "CLINICALTRIALS.GOV:NCT99000002",
        "IRCT:IRCT99000004N1",
        "ISRCTN:ISRCTN99000003",
        "UMIN CTR:UMIN99000005",
    ]


def test_data_repositories_are_not_trial_registries() -> None:
    databanks = databank_list(("figshare", "10.6084/m9.figshare.99000001"), ("GENBANK", "X99001"))

    assert parsed(article(databanks=databanks)).trial_ids == []


@pytest.mark.parametrize(
    "banks",
    [(("ClinicalTrials.gov", "NCT9900"),), (("ISRCTN", "  "),), (("IRCT", "IRCT 99000001N1"),)],
)
def test_malformed_registry_number_is_ignored(banks: tuple[tuple[str, str], ...]) -> None:
    assert parsed(article(databanks=databank_list(*banks))).trial_ids == []


def test_recorded_trials_keep_their_own_registries() -> None:
    records = recorded()

    assert records["32945627"].trial_ids == ["CHICTR:CHICTR2000030130"]
    assert records["26585952"].trial_ids == ["NTR:NTR3845"]


# Dropped records


def test_record_without_an_abstract_is_dropped() -> None:
    record = dropped(article(abstract=None, language="eng"))

    assert record == Dropped(pmid="999000001", reason=DropReason.NO_ABSTRACT, language="eng")


def test_abstract_with_only_a_copyright_line_counts_as_none() -> None:
    record = dropped(article(abstract="<CopyrightInformation>Synthetic.</CopyrightInformation>"))

    assert record.reason is DropReason.NO_ABSTRACT


def test_non_english_only_abstract_is_dropped_with_its_language() -> None:
    other = (
        "<OtherAbstract Type='Publisher' Language='spa'>"
        "<AbstractText>Resumen sintético.</AbstractText></OtherAbstract>"
    )

    record = dropped(article(abstract=None, other_abstract=other, language="spa"))

    assert record == Dropped(pmid="999000001", reason=DropReason.NON_ENGLISH_ONLY, language="spa")


@pytest.mark.parametrize("language", ["Language='eng'", ""])
def test_english_other_abstract_alone_counts_as_no_abstract(language: str) -> None:
    # Extraction reads only the Abstract element; an English OtherAbstract (such as a
    # population-research summary) is not a non-English abstract. The DTD defaults its
    # Language to English.
    other = (
        f"<OtherAbstract Type='PIP' {language}>"
        "<AbstractText>Synthetic English summary.</AbstractText></OtherAbstract>"
    )

    record = dropped(article(abstract=None, other_abstract=other))

    assert record.reason is DropReason.NO_ABSTRACT


def test_english_abstract_beside_another_language_is_kept() -> None:
    other = (
        "<OtherAbstract Type='Publisher' Language='por'>"
        "<AbstractText>Resumo sintético.</AbstractText></OtherAbstract>"
    )

    record = parsed(article(other_abstract=other, language="por"))

    assert record.text == "Synthetic abstract text."


def test_retracted_publication_is_dropped_first() -> None:
    record = dropped(article(publication_types=("Journal Article", "Retracted Publication")))

    assert record.reason is DropReason.RETRACTED


def test_book_record_is_dropped() -> None:
    book = b"""<PubmedBookArticle><BookDocument>
      <PMID Version="1">999000009</PMID><Language>eng</Language>
    </BookDocument></PubmedBookArticle>"""

    assert parse_record(book) == Dropped(pmid="999000009", reason=DropReason.BOOK, language="eng")


# Splitting a fetched document


def test_split_keys_each_record_by_pmid() -> None:
    document = (
        b"<?xml version='1.0' ?><PubmedArticleSet>"
        + article(pmid="999000001")
        + article(pmid="999000002")
        + b"</PubmedArticleSet>"
    )

    records = split_articles(document)

    assert list(records) == ["999000001", "999000002"]
    assert parsed(records["999000002"]).pmid == "999000002"


def test_split_rejects_an_error_document() -> None:
    with pytest.raises(EutilsError, match="Synthetic failure"):
        split_articles(b"<eFetchResult><ERROR>Synthetic failure</ERROR></eFetchResult>")


# Recorded records


RECORDED = Path(__file__).parent / "fixtures" / "pubmed" / "efetch.xml"


def recorded() -> dict[str, Abstract]:
    return {pmid: parsed(xml) for pmid, xml in split_articles(RECORDED.read_bytes()).items()}


def test_recorded_records_all_parse() -> None:
    assert list(recorded()) == ["35280228", "32945627", "26585952"]


def test_recorded_structured_trial() -> None:
    record = recorded()["35280228"]

    assert record.first_author == "Penckofer"
    assert record.journal == "J Diabetes Res"
    assert record.year == 2022
    assert record.language == "eng"
    assert "Randomized Controlled Trial" in record.publication_types
    assert [s.label for s in record.sections] == ["AIM", "METHODS", "RESULTS", "CONCLUSIONS"]
    assert record.mesh
    assert record.trial_ids == []


def test_recorded_split_round_trips_each_record() -> None:
    for pmid, xml in split_articles(RECORDED.read_bytes()).items():
        assert split_articles(b"<PubmedArticleSet>" + xml + b"</PubmedArticleSet>").keys() == {pmid}
