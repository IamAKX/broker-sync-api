"""The chatbot knowledge base (knowledge/*.md): format, consistency with the code, selection."""
import re
import uuid
from datetime import date

import pytest

from app.services.chat import knowledge as kn
from app.services.chat.knowledge import KnowledgeBase, KnowledgeError, load_knowledge, parse_doc
from app.services.chat.sql_guard import validate_and_prepare


@pytest.fixture(scope="module")
def kb():
    return load_knowledge()


def _doc(**over):
    meta = {"id": "rules.x", "title": "X", "category": "rules", "load": "always"}
    meta.update(over)
    head = "\n".join(f"{k}: {v}" for k, v in meta.items())
    return f"---\n{head}\n---\nbody text"


# ── the real knowledge base ─────────────────────────────────────────────────

def test_real_knowledge_base_loads_with_every_category(kb):
    s = kb.stats()
    assert s["docs"] >= 20 and s["always"] > 0 and s["on_demand"] > 0
    assert set(s["by_category"]) == set(kn.CATEGORIES)


def test_every_sql_example_passes_the_sql_guard(kb):
    blocks = 0
    for d in kb.docs.values():
        for sql in re.findall(r"```sql\n(.*?)```", d.body, flags=re.S):
            blocks += 1
            validate_and_prepare(sql.strip(), max_rows=200)       # raises UnsafeSqlError if not allowed
    assert blocks >= 8
    for d in (x for x in kb.docs.values() if x.category == "examples"):
        assert "```sql" in d.body, f"{d.path}: an example needs a ```sql block"


def test_data_source_docs_match_the_views_the_runner_creates(kb):
    from app.services.chat.sql_runner import _view_ddl
    views = set()
    for ddl in _view_ddl("tenant_x", uuid.uuid4()):
        views.add(re.search(r"CREATE TEMP VIEW (\w+)", ddl).group(1))
    documented = {d.title for d in kb.docs.values() if d.category == "data-source"}
    assert documented == views, f"knowledge/data-sources and sql_runner views differ: {documented ^ views}"


def test_file_location_matches_category(kb):
    folder = {"data-source": "data-sources", "rules": "rules", "glossary": "glossary", "examples": "examples"}
    for d in kb.docs.values():
        assert d.path.split("/")[0] == folder[d.category], f"{d.path} is in the wrong folder for {d.category}"


# ── parsing / validation ────────────────────────────────────────────────────

def test_parse_valid_doc_with_list_and_comment():
    d = parse_doc(_doc(load="on-demand", triggers='[gap, "gap up"]  # why', order="5"))
    assert d.triggers == ("gap", "gap up") and d.order == 5 and d.body == "body text"


@pytest.mark.parametrize("text,msg", [
    ("no front matter", "must start"),
    ("---\nid: a\n", "not closed"),
    (_doc(id="glossary.x"), "must start with 'rules.'"),
    (_doc(category="nope", id="nope.x"), "category"),
    (_doc(load="sometimes"), "load must be"),
    (_doc(load="on-demand"), "at least one trigger"),
    (_doc(order="abc"), "order must be an integer"),
    ("---\nid: rules.x\ntitle: X\ncategory: rules\n---\nb", "missing"),
    ("---\nid: rules.x\ntitle: X\ncategory: rules\nload: always\n---\n", "empty body"),
])
def test_malformed_docs_are_rejected(text, msg):
    with pytest.raises(KnowledgeError, match=msg):
        parse_doc(text, "f.md")


def test_duplicate_ids_are_rejected():
    kb = KnowledgeBase()
    kb.add(parse_doc(_doc(), "a.md"))
    with pytest.raises(KnowledgeError, match="duplicate"):
        kb.add(parse_doc(_doc(), "b.md"))


def test_missing_directory_and_empty_directory_are_errors(tmp_path):
    with pytest.raises(KnowledgeError, match="not found"):
        load_knowledge(tmp_path / "nope")
    with pytest.raises(KnowledgeError, match="no knowledge files"):
        load_knowledge(tmp_path)


def test_loader_reads_nested_files_and_skips_readme(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "a.md").write_text(_doc(id="rules.a"))
    (tmp_path / "README.md").write_text("# not a doc")
    assert list(load_knowledge(tmp_path).docs) == ["rules.a"]


# ── selection ───────────────────────────────────────────────────────────────

def _mini():
    kb = KnowledgeBase()
    kb.add(parse_doc(_doc(id="rules.core"), "core.md"))
    kb.add(parse_doc(_doc(id="glossary.gaps", category="glossary", load="on-demand", triggers="[gap, UF]"), "g.md"))
    return kb


def test_on_demand_docs_need_a_whole_token_trigger():
    kb = _mini()
    assert [d.id for d in kb.select("hello")] == ["rules.core"]
    assert "glossary.gaps" in [d.id for d in kb.select("show the daily gap up stocks")]
    assert "glossary.gaps" in [d.id for d in kb.select("any uf areas?")]            # case-insensitive
    assert "glossary.gaps" not in [d.id for d in kb.select("a gaping hole, UFO")]   # not a substring match


def test_over_budget_drops_on_demand_never_always():
    kb = _mini()
    got = [d.id for d in kb.select("gap", max_chars=len("body text") + 1)]
    assert got == ["rules.core"]


def test_render_orders_by_category_then_order():
    kb = KnowledgeBase()
    kb.add(parse_doc(_doc(id="examples.e", category="examples", title="E"), "e.md"))
    kb.add(parse_doc(_doc(id="data-source.d", category="data-source", title="D"), "d.md"))
    kb.add(parse_doc(_doc(id="rules.b", title="B", order="2"), "b.md"))
    kb.add(parse_doc(_doc(id="rules.a", title="A", order="1"), "a.md"))
    out = kb.render(kb.select(""))
    assert out.index("DATA SOURCES") < out.index("DATA RULES") < out.index("EXAMPLES")
    assert out.index("## A") < out.index("## B")


# ── the prompt built from it ────────────────────────────────────────────────

def _ctx():
    from app.services.chat.sql_runner import DataContext
    return DataContext(eod_latest=date(2026, 11, 9), lmv_latest=date(2026, 11, 9), lmv_metric_keys=["Close", "MT"],
                       signals_latest=None, futures_latest=date(2026, 11, 9),
                       eod_earliest=date(2026, 5, 4), lmv_earliest=date(2026, 7, 21), futures_earliest=date(2000, 6, 12))


def test_planner_prompt_has_live_dates_the_glossary_and_only_relevant_extras():
    from app.services.chat.prompts import build_planner_system
    p = build_planner_system(_ctx(), date(2026, 11, 10), "what is the MT of reliance on 5-7-2020")
    assert "lmv_daily trade_date range: 2026-07-21 to 2026-11-09" in p
    assert "Month Top" in p and "NOT \"Month Turnover\"" in p            # glossary reached the model
    assert "52WH" in p and "eod_futures_bars" in p
    assert "Q: What was the MT" in p                                     # triggered example included
    assert "DAY UF GUP 1" not in p                                       # gap doc not pulled in
    assert "DAY UF GUP 1" in build_planner_system(_ctx(), date(2026, 11, 10), "show unfilled gap up stocks")
    assert "late Oct" not in p
