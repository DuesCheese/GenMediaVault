import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from genmedia.models import Asset
from genmedia.search import SearchError, compile_ast, parse, to_dsl, validate


def test_precedence_and_missing():
    root = parse('model:pony OR cfg:4..6 AND NOT model:missing')["root"]
    assert root["type"] == "or"
    assert root["children"][1]["type"] == "and"
    assert root["children"][1]["children"][0]["value"] == [4, 6]


@pytest.mark.parametrize("query", ["cfg:abc", "(model:pony", "model:pony OR", "unknown:x", "cfg:7..2",
                                   "created:2026-99-01", 'prompt:"unclosed', "model:p*ny", "favorite:maybe"])
def test_errors(query):
    with pytest.raises(SearchError):
        parse(query)


def test_quotes_unicode_and_namespace():
    ast = parse('prompt:"白发女孩" AND tag:hair:white')
    assert ast["root"]["children"][0]["value"] == "白发女孩"
    assert ast["root"]["children"][1]["value"] == "hair:white"


def test_sql_is_parameterized_and_personal():
    ast = parse('prompt:"\' OR 1=1 --" AND rating:>=4')
    compiled = select(Asset.id).where(compile_ast(ast, "00000000-0000-0000-0000-000000000001")).compile(dialect=postgresql.dialect())
    assert "OR 1=1" not in str(compiled)
    assert any("OR 1=1" in str(value) for value in compiled.params.values())
    assert "user_assets.user_id" in str(compiled)


def test_roundtrip():
    for query in ['model:pony AND (rating:>=4 OR favorite:true)', 'created:2026-01-01..2026-02-01', 'created:>=2026-01-01', 'imported:<="2026-02-01"', 'NOT prompt:"blue eyes"', 'model:pony*', 'model:~"pony xl"', 'prompt:="white hair"']:
        assert parse(to_dsl(parse(query))) == parse(query)


def test_rules_cannot_use_personal_fields():
    with pytest.raises(SearchError):
        validate(parse("favorite:true"), allow_personal=False)
