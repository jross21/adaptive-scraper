from pathlib import Path

from adaptive_scraper.compress.compressor import CompressedDOM, compress

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_listing.html"


def html() -> str:
    return FIXTURE.read_text()


def test_strips_noise_from_prompt():
    prompt = compress(html()).to_prompt()
    assert "dataLayer" not in prompt  # analytics <script> stripped
    assert "padding: 1rem" not in prompt  # <style> body stripped
    assert "<svg" not in prompt and "<circle" not in prompt  # svg stripped


def test_surfaces_json_ld():
    cd = compress(html())
    assert cd.json_ld_blocks, "expected JSON-LD to be surfaced"
    joined = "\n".join(cd.json_ld_blocks)
    assert "JobPosting" in joined
    assert "Senior Backend Engineer" in joined
    # JSON-LD must also reach the rendered prompt (it's the cheat code).
    assert "Senior Backend Engineer" in cd.to_prompt()


def test_detects_repeating_job_cards():
    cd = compress(html())
    job_groups = [g for g in cd.repeating if "job-card" in g.signature]
    assert job_groups, f"no repeating job-card group found in {[g.signature for g in cd.repeating]}"
    assert job_groups[0].count == 3


def test_exemplars_are_capped_not_all_rows():
    cd = compress(html())
    job_groups = [g for g in cd.repeating if "job-card" in g.signature]
    assert 1 <= len(job_groups[0].exemplars) <= 2  # exemplars, not all 3 rows


def test_outline_shows_structure():
    prompt = compress(html()).to_prompt()
    assert "ul" in prompt
    assert "job-card" in prompt


def test_to_prompt_respects_token_budget():
    prompt = compress(html()).to_prompt(token_budget=100)
    assert len(prompt) <= 100 * 4  # ~4 chars/token hard cap


def test_returns_compressed_dom_type():
    assert isinstance(compress(html()), CompressedDOM)
