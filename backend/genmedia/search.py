"""All search entry points share this bounded AST and parameterized SQL compiler."""
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Float, and_, cast, exists, false, func, literal, not_, or_, select, true

from .models import (Asset, AssetModel, AssetTag, Generation, ModelRef, PhysicalFile,
                     PromptToken, Tag, UserAsset)
from .parsers import canonical

FIELDS = {
    "filename": "text", "path": "text", "type": "enum", "extension": "enum",
    "width": "number", "height": "number", "ratio": "number", "size": "number",
    "created": "date", "imported": "date", "orientation": "enum",
    "generator": "enum", "model": "enum", "model_hash": "enum", "prompt": "text",
    "negative": "text", "seed": "seed", "steps": "number", "cfg": "number",
    "sampler": "enum", "scheduler": "enum", "lora": "enum", "tag": "enum",
    "tag.namespace": "enum", "prompt.tag": "enum", "negative.tag": "enum",
    "favorite": "bool", "rating": "number", "review": "enum", "text": "text",
}
ALIASES = {"neg": "negative", "fav": "favorite", "gen": "generator", "file_size": "size"}
OPERATORS = {"eq", "contains", "prefix", "gt", "gte", "lt", "lte", "between", "missing"}
PERSONAL_FIELDS = {"favorite", "rating", "review"}


class SearchError(ValueError):
    def __init__(self, message, position=0):
        super().__init__(message)
        self.position = position


@dataclass
class Token:
    text: str
    position: int


def lex(query: str):
    if len(query) > 8192:
        raise SearchError("查询不能超过 8192 个字符")
    result, start = [], 0
    while start < len(query):
        if query[start].isspace():
            start += 1
            continue
        if query[start] in "()":
            result.append(Token(query[start], start))
            start += 1
            continue
        end, quote, escape = start, False, False
        while end < len(query):
            char = query[end]
            if escape:
                escape = False
            elif char == "\\" and quote:
                escape = True
            elif char == '"':
                quote = not quote
            elif not quote and (char.isspace() or char in "()"):
                break
            end += 1
        if quote:
            raise SearchError("字符串缺少结束引号", start)
        result.append(Token(query[start:end], start))
        start = end
    if len(result) > 300:
        raise SearchError("查询条件过多")
    return result


def parse(query: str) -> dict:
    tokens, pos, depth = lex(query), 0, 0

    def peek(value):
        return pos < len(tokens) and tokens[pos].text.upper() == value

    def atom():
        nonlocal pos, depth
        if pos >= len(tokens):
            raise SearchError("逻辑运算符后缺少条件", len(query))
        if peek("NOT"):
            pos += 1
            depth += 1
            if depth > 12:
                raise SearchError("查询嵌套不能超过 12 层")
            child = atom()
            depth -= 1
            return {"type": "not", "child": child}
        if peek("("):
            pos += 1
            depth += 1
            if depth > 12:
                raise SearchError("查询嵌套不能超过 12 层")
            child = disjunction()
            depth -= 1
            if not peek(")"):
                raise SearchError("缺少右括号", len(query))
            pos += 1
            return child
        token = tokens[pos]
        if token.text.upper() in ("AND", "OR", ")"):
            raise SearchError("此处需要搜索条件", token.position)
        pos += 1
        field, value = (token.text.split(":", 1) if ":" in token.text and not token.text.startswith('"')
                        else ("text", token.text))
        field = ALIASES.get(field, field)
        if field not in FIELDS:
            raise SearchError(f"未知搜索字段：{field}", token.position)
        op = "contains" if FIELDS[field] == "text" else "eq"
        if value.startswith("="):
            op, value = "eq", value[1:]
        elif value.startswith("~"):
            op, value = "contains", value[1:]
        quoted_prefix = value.startswith('"') and value.endswith('"*')
        if quoted_prefix:
            op, value = "prefix", value[:-1]
        quoted = value.startswith('"')
        if quoted:
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise SearchError("字符串转义无效", token.position) from exc
        elif value == "missing":
            return {"type": "condition", "field": field, "op": "missing", "value": None}
        elif value.startswith((">=", "<=", ">", "<")):
            match = re.match(r"(>=|<=|>|<)(.*)", value)
            op = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}[match[1]]
            value = match[2]
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError as exc:
                    raise SearchError("字符串转义无效", token.position) from exc
        elif ".." in value and FIELDS[field] in ("date", "number"):
            op, value = "between", value.split("..", 1)
        elif "*" in value:
            if not value.endswith("*") or "*" in value[:-1]:
                raise SearchError("只支持末尾通配符", token.position)
            op, value = "prefix", value[:-1]
        elif value.startswith("/") and value.endswith("/"):
            raise SearchError("首版不支持正则表达式", token.position)
        if value == "":
            raise SearchError("字段值不能为空", token.position)
        return {"type": "condition", "field": field, "op": op, "value": value}

    def conjunction():
        nonlocal pos
        children = [atom()]
        while pos < len(tokens) and not peek("OR") and not peek(")"):
            if peek("AND"):
                pos += 1
            children.append(atom())
        return children[0] if len(children) == 1 else {"type": "and", "children": children}

    def disjunction():
        nonlocal pos
        children = [conjunction()]
        while peek("OR"):
            pos += 1
            children.append(conjunction())
        return children[0] if len(children) == 1 else {"type": "or", "children": children}

    root = disjunction() if tokens else {"type": "and", "children": []}
    if pos != len(tokens):
        raise SearchError("多余的右括号", tokens[pos].position)
    return validate({"version": 1, "root": root})


def validate(ast: dict, allow_personal=True) -> dict:
    if not isinstance(ast, dict) or ast.get("version") != 1:
        raise SearchError("不支持的查询版本")
    count = 0

    def visit(node, depth=0):
        nonlocal count
        count += 1
        if count > 100 or depth > 12:
            raise SearchError("查询过于复杂")
        if not isinstance(node, dict):
            raise SearchError("查询节点必须是对象")
        kind = node.get("type")
        if kind in ("and", "or"):
            children = node.get("children")
            if not isinstance(children, list):
                raise SearchError("逻辑组缺少 children")
            if not children and (depth > 0 or kind == "or"):
                raise SearchError("条件组不能为空，请添加条件或删除该组")
            return {"type": kind, "children": [visit(n, depth + 1) for n in children]}
        if kind == "not":
            return {"type": kind, "child": visit(node.get("child"), depth + 1)}
        field, op, value = node.get("field"), node.get("op"), node.get("value")
        if kind != "condition" or not isinstance(field, str) or not isinstance(op, str) or field not in FIELDS or op not in OPERATORS:
            raise SearchError("查询字段或运算符无效")
        if not allow_personal and field in PERSONAL_FIELDS:
            raise SearchError("共享分类规则不能使用个人评分、收藏或筛选状态")
        field_type = FIELDS[field]
        allowed = {"eq", "missing"}
        if field_type in ("text", "enum"):
            allowed |= {"contains", "prefix"}
        if field_type in ("date", "number"):
            allowed |= {"gt", "gte", "lt", "lte", "between"}
        if op not in allowed:
            raise SearchError(f"{field} 不支持 {op}")
        if op == "missing":
            return {"type": kind, "field": field, "op": op, "value": None}

        def convert(v):
            if field_type == "number":
                try:
                    n = float(v)
                    if not math.isfinite(n):
                        raise ValueError()
                    return n
                except (TypeError, ValueError) as exc:
                    raise SearchError(f"{field} 需要数字") from exc
            if field_type == "date":
                try:
                    return date.fromisoformat(str(v)).isoformat()
                except ValueError as exc:
                    raise SearchError(f"{field} 需要 YYYY-MM-DD 日期") from exc
            if field_type == "bool":
                if str(v).lower() not in ("true", "false"):
                    raise SearchError(f"{field} 需要 true 或 false")
                return str(v).lower() == "true"
            if not isinstance(v, str) or not v or len(v) > 2048:
                raise SearchError(f"{field} 需要 1–2048 个字符的文本")
            if field_type == "seed" and not re.fullmatch(r"-?\d{1,100}", v):
                raise SearchError("seed 需要十进制整数字符串")
            return v

        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise SearchError("范围需要起点和终点")
            value = [convert(v) for v in value]
            if value[0] > value[1]:
                raise SearchError("范围起点不能大于终点")
        else:
            value = convert(value)
        return {"type": kind, "field": field, "op": op, "value": value}

    return {"version": 1, "root": visit(ast.get("root"))}


def to_dsl(ast: dict) -> str:
    def render(node):
        kind = node["type"]
        if kind in ("and", "or"):
            return "(" + f" {kind.upper()} ".join(render(n) for n in node["children"]) + ")" if node["children"] else ""
        if kind == "not":
            return "NOT (" + render(node["child"]) + ")"
        field, op, value = node["field"], node["op"], node["value"]
        if op == "missing":
            return f"{field}:missing"
        if op == "between":
            return f"{field}:{value[0]}..{value[1]}"
        prefix = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}.get(op, "")
        if op == "eq" and FIELDS[field] == "text":
            prefix = "="
        if op == "contains" and FIELDS[field] != "text":
            prefix = "~"
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        return f"{field}:{prefix}{value}" + ("*" if op == "prefix" else "")
    return render(validate(ast)["root"])


def compile_ast(ast: dict, user_id: str):
    ast = validate(ast)

    def predicate(column, node):
        op, value, field = node["op"], node["value"], node["field"]
        if op == "missing":
            return or_(column.is_(None), column == "") if FIELDS[field] in ("text", "enum", "seed") else column.is_(None)
        if FIELDS[field] == "date":
            def day(v):
                return datetime.combine(date.fromisoformat(v), datetime.min.time(), tzinfo=timezone.utc)
            if op == "eq":
                return and_(column >= day(value), column < day(value) + timedelta(days=1))
            if op == "between":
                return and_(column >= day(value[0]), column < day(value[1]) + timedelta(days=1))
            if op in ("lte", "gt"):
                value = day(value) + timedelta(days=1)
                op = "lt" if op == "lte" else "gte"
            else:
                value = day(value)
        if op in ("contains", "prefix"):
            escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return column.ilike(("%" if op == "contains" else "") + escaped + "%", escape="\\")
        if op == "between":
            return column.between(*value)
        if op == "eq":
            if FIELDS[field] in ("enum", "text"):
                return func.lower(column) == str(value).lower()
            return column == value
        return {"gt": column.__gt__, "gte": column.__ge__, "lt": column.__lt__, "lte": column.__le__}[op](value)

    def related(table, column, node, additional=None):
        base = select(1).select_from(table).where(table.asset_id == Asset.id).correlate(Asset)
        if additional is not None:
            base = base.where(additional)
        if node["op"] == "missing":
            return not_(exists(base.where(not_(predicate(column, node)))))
        return exists(base.where(predicate(column, node)))

    def condition(node):
        field = node["field"]
        direct = {"filename": Asset.filename, "extension": Asset.extension,
                  "width": Asset.width, "height": Asset.height, "size": Asset.file_size,
                  "ratio": cast(Asset.width, Float) / func.nullif(Asset.height, 0),
                  "created": func.coalesce(Asset.source_created_at, Asset.imported_at),
                  "imported": Asset.imported_at, "type": literal("image")}
        if field in direct:
            return predicate(direct[field], node)
        if field == "orientation":
            from sqlalchemy import case
            return predicate(case((Asset.width == Asset.height, "square"),
                                  (Asset.width < Asset.height, "portrait"), else_="landscape"), node)
        if field in PERSONAL_FIELDS:
            column = getattr(UserAsset, field)
            owner = UserAsset.user_id == user_id
            if field == "rating":
                return related(UserAsset, column, node, owner)
            if field == "favorite":
                if node["op"] == "missing":
                    return false()
                starred = related(UserAsset, column, {**node, "op": "eq", "value": True}, owner)
                return starred if node["value"] else not_(starred)
            value = select(column).where(UserAsset.asset_id == Asset.id, UserAsset.user_id == user_id).correlate(Asset).scalar_subquery()
            if field == "review":
                value = func.coalesce(value, "unreviewed")
            return predicate(value, node)
        if field == "path":
            return related(PhysicalFile, PhysicalFile.path, node, PhysicalFile.role == "original")
        if field in ("prompt.tag", "negative.tag"):
            n = {**node, "value": canonical(node["value"]) if node["value"] else None}
            return related(PromptToken, PromptToken.token, n,
                           PromptToken.polarity == ("positive" if field == "prompt.tag" else "negative"))
        if field in ("tag", "tag.namespace"):
            column = Tag.name if field == "tag" else Tag.namespace
            base = select(1).select_from(AssetTag).join(Tag, Tag.id == AssetTag.tag_id).where(AssetTag.asset_id == Asset.id).correlate(Asset)
            return not_(exists(base)) if node["op"] == "missing" else exists(base.where(predicate(column, node)))
        if field == "lora":
            base = select(1).select_from(AssetModel).join(ModelRef, ModelRef.id == AssetModel.model_id).where(
                AssetModel.asset_id == Asset.id, ModelRef.kind == "lora").correlate(Asset)
            return not_(exists(base)) if node["op"] == "missing" else exists(base.where(predicate(ModelRef.name, node)))
        if field == "text":
            return or_(predicate(Asset.filename, node), related(Generation, Generation.prompt, node),
                       condition({**node, "field": "tag"}))
        return related(Generation, getattr(Generation, field), node)

    def visit(node):
        if node["type"] == "and":
            return and_(true(), *(visit(n) for n in node["children"]))
        if node["type"] == "or":
            return or_(false(), *(visit(n) for n in node["children"]))
        if node["type"] == "not":
            return not_(visit(node["child"]))
        # Related predicates use EXISTS so a missing row remains a non-match under NOT.
        return condition(node)

    return visit(ast["root"])
