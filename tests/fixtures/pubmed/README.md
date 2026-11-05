# Recorded PubMed responses

Response bodies recorded from NCBI E-utilities on 2026-11-05. Only bodies are kept: request
parameters, including `tool`, `email` and `api_key`, are never written here, and
`tests/test_fixture_hygiene.py` fails if they appear.

- `esearch.json`: ESearch, JSON, for `35280228[pmid] OR 32945627[pmid] OR 26585952[pmid]`
  with `usehistory=y`.
- `efetch.xml`: EFetch, XML, of that search's history.

The three records are openly licensed articles. Their PubMed records are reproduced unchanged.
Each licence was checked against Europe PMC's licence field and the article's own permissions
statement.

| PMID | Article | Licence |
|---|---|---|
| 35280228 | Penckofer S et al. Vitamin D Supplementation for the Treatment of Depressive Symptoms in Women with Type 2 Diabetes: A Randomized Clinical Trial. *J Diabetes Res* 2022. [doi:10.1155/2022/4090807](https://doi.org/10.1155/2022/4090807), [PMC8913152](https://pmc.ncbi.nlm.nih.gov/articles/PMC8913152/) | [CC BY](https://creativecommons.org/licenses/by/4.0/) |
| 32945627 | Zhu C et al. Vitamin D supplementation improves anxiety but not depression symptoms in patients with vitamin D deficiency. *Brain Behav* 2020. [doi:10.1002/brb3.1760](https://doi.org/10.1002/brb3.1760), [PMC7667301](https://pmc.ncbi.nlm.nih.gov/articles/PMC7667301/) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| 26585952 | de Koning EJ et al. Vitamin D supplementation to prevent depression and poor physical function in older adults: Study protocol of the D-Vitaal study, a randomized placebo-controlled clinical trial. *BMC Geriatr* 2015. [doi:10.1186/s12877-015-0148-3](https://doi.org/10.1186/s12877-015-0148-3), [PMC4653865](https://pmc.ncbi.nlm.nih.gov/articles/PMC4653865/) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

Other parser cases use synthetic XML built in the tests, with PMIDs from 999000001 and text
marked "Synthetic".
