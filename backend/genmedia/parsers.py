"""Bounded extraction and lossless, versioned adapters. No generator runtime is executed."""
import base64
import gzip
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import ExifTags, Image

Image.MAX_IMAGE_PIXELS = 64_000_000
PARSER_VERSION = "1.1.0"
MAX_METADATA = 8 * 1024 * 1024
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def canonical(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def safe_json(value):
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode()}
    if isinstance(value, str) and "\x00" in value:
        return safe_json(value.encode())
    if isinstance(value, dict):
        return {str(k).replace("\x00", "\\u0000"): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    return str(value)


def text_value(value) -> str:
    if isinstance(value, dict) and value.get("encoding") == "base64":
        value = base64.b64decode(value["value"])
    if isinstance(value, bytes):
        if value.startswith(b"ASCII\0\0\0"):
            value = value[8:]
        elif value.startswith(b"UNICODE\0"):
            content = value[8:]
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-be"
            return content.decode(encoding, errors="replace").rstrip("\0")
        return value.decode("utf-8", errors="replace").rstrip("\0")
    return value if isinstance(value, str) else ""


def json_object(value):
    if isinstance(value, dict) and "encoding" not in value:
        return value
    try:
        parsed = json.loads(text_value(value))
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def extract_stealth(image: Image.Image) -> dict | None:
    if "A" not in image.getbands():
        return None
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    bits = (pixels[x, y][3] & 1 for x in range(image.width) for y in range(image.height))

    def read(n):
        out = bytearray()
        for _ in range(n):
            byte = 0
            for _ in range(8):
                try:
                    byte = (byte << 1) | next(bits)
                except StopIteration as exc:
                    raise ValueError("NovelAI 隐写数据被截断") from exc
            out.append(byte)
        return bytes(out)

    try:
        signature = read(len(b"stealth_pngcomp"))
    except ValueError:
        return None
    if signature not in (b"stealth_pngcomp", b"stealth_pnginfo"):
        return None
    length_bits = int.from_bytes(read(4), "big")
    if length_bits % 8 or length_bits > MAX_METADATA * 8:
        raise ValueError("NovelAI 隐写数据长度无效")
    payload = read(length_bits // 8)
    if signature == b"stealth_pngcomp":
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            payload = stream.read(MAX_METADATA + 1)
    if len(payload) > MAX_METADATA:
        raise ValueError("NovelAI 隐写解压结果超过限制")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("NovelAI 隐写数据不是对象")
    return decoded


def sidecars_for(path: Path) -> list[Path]:
    result = []
    siblings = [p for p in path.parent.iterdir() if p.stem == path.stem and p.suffix.lower() in EXTENSIONS]
    for suffix in (".json", ".txt"):
        exact = Path(str(path) + suffix)
        stem = path.with_suffix(suffix)
        candidate = exact if exact.is_file() else stem if len(siblings) == 1 and stem.is_file() else None
        if candidate is not None:
            result.append(candidate)
    return result


def extract(path: Path, sidecars: list[Path] | None = None) -> dict:
    warnings = []
    with Image.open(path) as image:
        if image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValueError("仅支持 PNG、JPEG、WebP 图片")
        if image.width * image.height > Image.MAX_IMAGE_PIXELS:
            raise ValueError("图片超过 6400 万像素限制")
        image.load()
        info = safe_json(image.info)
        exif = {}
        try:
            exif_data = image.getexif()
            exif = {ExifTags.TAGS.get(k, str(k)): safe_json(v) for k, v in exif_data.items()}
            if 34665 in exif_data:
                exif.update({ExifTags.TAGS.get(k, str(k)): safe_json(v)
                             for k, v in exif_data.get_ifd(34665).items()})
        except (ValueError, OSError, SyntaxError) as exc:
            warnings.append(f"EXIF 部分读取失败：{exc}")
        stealth = None
        try:
            stealth = extract_stealth(image)
        except (ValueError, OSError, EOFError) as exc:
            warnings.append(f"隐写元数据读取失败：{exc}")
        bundle = {"info": info, "exif": exif, "stealth": safe_json(stealth), "sidecars": [],
                  "technical": {"width": image.width, "height": image.height,
                                "format": image.format, "frames": getattr(image, "n_frames", 1)},
                  "warnings": warnings}
    for sidecar in sidecars if sidecars is not None else sidecars_for(path):
        if sidecar.stat().st_size > MAX_METADATA:
            warnings.append(f"Sidecar 超过 8 MB：{sidecar.name}")
            continue
        try:
            content = sidecar.read_text(encoding="utf-8-sig")
            bundle["sidecars"].append({"name": sidecar.name, "format": sidecar.suffix.lower(),
                                       "text": content})
        except (OSError, UnicodeError) as exc:
            warnings.append(f"Sidecar 无法读取：{sidecar.name}：{exc}")
    if len(json.dumps(bundle, ensure_ascii=False).encode()) > MAX_METADATA * 4:
        raise ValueError("提取的元数据超过总量限制")
    return bundle


@dataclass
class Parsed:
    normalized: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    parser: str = "generic"
    confidence: float = 0.0
    raw: dict = field(default_factory=dict)


class MetadataParser(Protocol):
    key: str
    def detect(self, data: dict) -> float: ...
    def extract(self, data: dict) -> dict: ...
    def normalize(self, data: dict) -> Parsed: ...


class A1111Parser:
    key = "a1111"

    def detect(self, data):
        return 0.9 if "Steps:" in text_value(data.get("parameters")) else 0

    def extract(self, data):
        return {"parameters": text_value(data.get("parameters"))}

    def normalize(self, data):
        value = data["parameters"]
        match = re.search(r"(?:^|\n)Steps:\s*\d+", value)
        if not match:
            return Parsed({"prompt": value}, parser=self.key)
        prompts, parameters = value[:match.start()].strip(), value[match.start():].strip()
        positive, separator, negative = prompts.partition("\nNegative prompt:")
        if prompts.startswith("Negative prompt:"):
            positive, negative = "", prompts[len("Negative prompt:"):]
        fields = dict(re.findall(r'(?:^|,\s*)([\w /]+):\s*("(?:\\.|[^"\\])*"|[^,]*)', parameters))
        fields = {k.strip(): v.strip().strip('"') for k, v in fields.items()}
        sampler = fields.get("Sampler")
        scheduler = fields.get("Schedule type")
        if sampler and sampler.endswith(" Karras"):
            sampler, scheduler = sampler[:-7], "karras"
        loras = [{"name": m[0], "weight": float(m[1])}
                 for m in re.findall(r"<lora:([^:>]+):(-?[\d.]+)>", positive)]
        return Parsed({"generator": self.key, "prompt": positive, "negative": negative.strip(),
                       "model": fields.get("Model"), "model_hash": fields.get("Model hash"),
                       "seed": fields.get("Seed"), "steps": fields.get("Steps"),
                       "cfg": fields.get("CFG scale"), "sampler": sampler,
                       "scheduler": scheduler, "loras": loras, "extra": {"a1111": fields}}, parser=self.key)


class NovelAIParser:
    key = "novelai"

    def detect(self, data):
        comment = json_object(data.get("Comment")) or data
        return 0.98 if ("novelai" in str(data.get("Software", "")).lower()
                        or "nai-" in str(data.get("Source", "")).lower()
                        or "v4_prompt" in comment or "characterPrompts" in comment
                        or ("uc" in comment and "sampler" in comment)) else 0

    def extract(self, data):
        return data

    def normalize(self, data):
        comment = json_object(data.get("Comment")) or data
        if "Comment" in comment:
            comment = json_object(comment["Comment"])
        v4 = json_object(comment.get("v4_prompt"))
        caption = json_object(v4.get("caption"))
        negative_caption = json_object(json_object(comment.get("v4_negative_prompt")).get("caption"))
        prompt = caption.get("base_caption", comment.get("prompt") or data.get("Description", ""))
        negative = negative_caption.get("base_caption", comment.get("uc", ""))
        positives = caption.get("char_captions", [])
        negatives = negative_caption.get("char_captions", [])
        legacy = comment.get("characterPrompts", [])
        positives = positives if isinstance(positives, list) else []
        negatives = negatives if isinstance(negatives, list) else []
        legacy = legacy if isinstance(legacy, list) else []
        characters, warnings = [], []
        for index in range(min(max(len(positives), len(negatives), len(legacy)), 100)):
            def entry(values, index=index):
                return values[index] if index < len(values) and isinstance(values[index], dict) else {}
            positive, undesired, fallback = entry(positives), entry(negatives), entry(legacy)
            centers = positive.get("centers", fallback.get("centers", [fallback["center"]] if "center" in fallback else []))
            characters.append({"index": index, "name": fallback.get("name") or f"角色 {index + 1}",
                               "prompt": text_value(positive.get("char_caption", fallback.get("prompt", ""))),
                               "negative": text_value(undesired.get("char_caption", fallback.get("uc", ""))),
                               "centers": centers, "enabled": fallback.get("enabled", True)})
        if len(positives) != len(negatives) and negatives:
            warnings.append("NovelAI 角色正负提示词数量不同，按原始顺序配对，缺失部分保留为空")
        if max(len(positives), len(negatives), len(legacy)) > 100:
            warnings.append("角色数量超过展示上限，完整内容仍保存在原始信息中")
        return Parsed({"generator": self.key, "model": data.get("Source"), "prompt": prompt,
                       "negative": negative, "characters": characters,
                       "character_settings": {"use_coords": v4.get("use_coords"), "use_order": v4.get("use_order")},
                       "seed": comment.get("seed"),
                       "steps": comment.get("steps"), "cfg": comment.get("scale"),
                       "sampler": comment.get("sampler"), "scheduler": comment.get("noise_schedule"),
                       "extra": {"novelai": comment}}, parser=self.key, warnings=warnings)


class ComfyUIParser:
    key = "comfyui"

    def detect(self, data):
        graph = json_object(data.get("prompt"))
        return 0.95 if any(isinstance(n, dict) and "class_type" in n for n in graph.values()) else (
            0.85 if "nodes" in json_object(data.get("workflow")) else 0)

    def extract(self, data):
        return {"prompt": json_object(data.get("prompt")), "workflow": json_object(data.get("workflow"))}

    def normalize(self, data):
        graph = data["prompt"]
        result = Parsed({"generator": self.key, "workflow": data.get("workflow") or None,
                         "execution_graph": graph, "loras": []}, parser=self.key)
        samplers = [(key, n) for key, n in graph.items() if isinstance(n, dict)
                    and n.get("class_type") in ("KSampler", "KSamplerAdvanced")]
        if len(samplers) != 1:
            result.warnings.append("ComfyUI 未找到唯一采样分支，仅保存完整图；不猜测生成参数")
            return result
        inputs = samplers[0][1].get("inputs", {})

        def upstream(link, allowed, visited=None):
            visited = set() if visited is None else visited
            if not isinstance(link, list) or len(link) != 2:
                return None
            key = str(link[0])
            if key in visited or len(visited) > 100:
                return None
            visited.add(key)
            node = graph.get(key, {})
            if node.get("class_type") in allowed:
                return node
            return None

        for side, output in (("positive", "prompt"), ("negative", "negative")):
            node = upstream(inputs.get(side), {"CLIPTextEncode"})
            text = node.get("inputs", {}).get("text") if node else None
            if isinstance(text, str):
                result.normalized[output] = text
            else:
                result.warnings.append(f"ComfyUI {side} 路径未支持，已保留原图数据")
        result.normalized.update({out: inputs.get(key) for out, key in
                                  (("seed", "seed"), ("steps", "steps"), ("cfg", "cfg"),
                                   ("sampler", "sampler_name"), ("scheduler", "scheduler"))
                                  if not isinstance(inputs.get(key), list)})
        if "seed" not in inputs:
            result.normalized["seed"] = inputs.get("noise_seed")
        link = inputs.get("model")
        visited = set()
        while isinstance(link, list) and len(link) == 2 and str(link[0]) not in visited:
            visited.add(str(link[0]))
            node = graph.get(str(link[0]), {})
            values = node.get("inputs", {})
            if node.get("class_type") in ("CheckpointLoaderSimple", "CheckpointLoader"):
                result.normalized["model"] = values.get("ckpt_name")
                break
            if node.get("class_type") in ("LoraLoader", "LoraLoaderModelOnly"):
                result.normalized["loras"].append({"name": values.get("lora_name"),
                                                   "weight": values.get("strength_model"),
                                                   "weight_clip": values.get("strength_clip")})
                link = values.get("model")
            else:
                result.warnings.append("ComfyUI 模型路径包含未支持节点")
                break
        return result


PARSERS: list[MetadataParser] = [NovelAIParser(), ComfyUIParser(), A1111Parser()]


def parse_data(data: dict) -> Parsed:
    parser = max(PARSERS, key=lambda p: p.detect(data))
    if parser.detect(data) == 0:
        return Parsed(raw=data)
    try:
        result = parser.normalize(parser.extract(data))
        result.confidence, result.raw = parser.detect(data), data
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Parsed(warnings=[f"{parser.key} 部分元数据无效：{exc}"], parser=parser.key)


def normalize(bundle: dict) -> Parsed:
    data = {**bundle.get("exif", {}), **bundle.get("info", {})}
    if "Source" not in data and str(data.get("Software", "")).lower().startswith("nai-"):
        data["Source"] = data["Software"]
    if "Description" not in data and "ImageDescription" in data:
        data["Description"] = data["ImageDescription"]
    if "UserComment" in data and "parameters" not in data:
        data["parameters"] = text_value(data["UserComment"])
        comment = json_object(data["parameters"])
        if "sampler" in comment:
            data["Comment"] = comment
    # ComfyUI WebP embeds prompt/workflow in EXIF Model/Make fields.
    for value in list(data.values()):
        text = text_value(value)
        for prefix in ("prompt:", "workflow:"):
            if text.startswith(prefix):
                data[prefix[:-1]] = json_object(text[len(prefix):])
    result = parse_data(data)
    result.warnings = list(bundle.get("warnings", [])) + result.warnings
    provenance = {key: "embedded" for key in result.normalized}
    sources = [{"source": "embedded", "parser": result.parser, "confidence": result.confidence}]

    def merge(other: dict, source: str):
        for key, value in other.items():
            if value is None or value == "":
                continue
            previous = result.normalized.get(key)
            if previous is not None and previous != "" and previous != value:
                result.conflicts.append({"field": key, "previous": previous, "incoming": value,
                                         "previous_source": provenance.get(key, "embedded"),
                                         "incoming_source": source, "resolved_source": source})
            result.normalized[key] = value
            provenance[key] = source

    if isinstance(bundle.get("stealth"), dict):
        stealth_result = NovelAIParser().normalize(bundle["stealth"])
        result.warnings.extend(stealth_result.warnings)
        merge(stealth_result.normalized, "novelai_stealth")
        result.parser = "novelai"
        result.confidence = 1.0
        sources.append({"source": "novelai_stealth", "parser": "novelai", "confidence": 1.0})
    for sidecar in bundle.get("sidecars", []):
        if sidecar["format"] == ".txt":
            if not result.normalized.get("prompt"):
                parsed = A1111Parser().normalize({"parameters": sidecar["text"]})
                merge(parsed.normalized, sidecar["name"])
                sources.append({"source": sidecar["name"], "parser": "text", "confidence": 0.5})
            continue
        payload = json_object(sidecar["text"])
        if not payload:
            result.warnings.append(f"Sidecar JSON 未识别：{sidecar['name']}")
            continue
        if payload.get("schema_version") == 1 and isinstance(payload.get("generation"), dict):
            merge(payload["generation"], sidecar["name"])
            sources.append({"source": sidecar["name"], "parser": "genmedia_json", "confidence": 1.0})
        elif payload.get("schema_version") == 1 and isinstance(payload.get("prompt"), str):
            merge(payload, sidecar["name"])
            sources.append({"source": sidecar["name"], "parser": "genmedia_json", "confidence": 1.0})
        else:
            other = parse_data(payload)
            if other.normalized:
                merge(other.normalized, sidecar["name"])
                sources.append({"source": sidecar["name"], "parser": other.parser, "confidence": other.confidence})
                result.warnings.extend(other.warnings)
            else:
                result.warnings.append(f"Sidecar 格式未支持，原文已保存：{sidecar['name']}")
    normalized = result.normalized
    normalized.setdefault("generator", "unknown")
    normalized.setdefault("prompt", "")
    normalized.setdefault("negative", "")
    normalized.setdefault("loras", [])
    normalized["schema_version"] = 1
    for dimension in ("width", "height"):
        if dimension not in normalized and dimension in bundle.get("technical", {}):
            normalized[dimension] = bundle["technical"][dimension]
            provenance[dimension] = "image"
    result.confidence = max(source["confidence"] for source in sources)
    normalized["parsing"] = {"parser_version": PARSER_VERSION, "confidence": result.confidence,
                             "sources": sources, "field_sources": provenance}
    result.raw = bundle
    for name, kind in (("seed", str), ("steps", int), ("cfg", float)):
        value = normalized.get(name)
        if value is not None:
            try:
                if name == "seed" and not re.fullmatch(r"-?\d{1,100}", str(value)):
                    raise ValueError("不是十进制整数")
                normalized[name] = kind(value)
                if name == "steps" and not 0 <= normalized[name] <= 1_000_000:
                    raise ValueError("步数超出合理范围")
                if name == "cfg" and not math.isfinite(normalized[name]):
                    raise ValueError("不是有限数值")
            except (ValueError, TypeError, OverflowError):
                result.warnings.append(f"{name} 无效，原始值已保存")
                normalized[name] = None
    for name in ("prompt", "negative", "model", "model_hash", "sampler", "scheduler", "generator"):
        if normalized.get(name) is not None and not isinstance(normalized[name], str):
            result.warnings.append(f"{name} 不是文本，原始值已保存")
            normalized[name] = "" if name in ("prompt", "negative") else None
        elif isinstance(normalized.get(name), str):
            normalized[name] = normalized[name].replace("\x00", "\ufffd")
            if name not in ("prompt", "negative") and len(normalized[name]) > 512:
                result.warnings.append(f"{name} 超过索引长度，原始值已保存")
                normalized[name] = normalized[name][:512]
    return result


DICTIONARY = {
    "white hair": "hair:white", "black hair": "hair:black", "blonde hair": "hair:blonde",
    "blue eyes": "eyes:blue", "green eyes": "eyes:green", "red eyes": "eyes:red",
    "school uniform": "clothes:school_uniform", "dress": "clothes:dress",
    "outdoors": "scene:outdoors", "indoors": "scene:indoors", "sunset": "lighting:sunset",
    "cinematic lighting": "lighting:cinematic", "anime": "style:anime",
    "masterpiece": "quality:masterpiece", "looking at viewer": "pose:looking_at_viewer",
}


def tokenize_prompt(prompt: str, generator: str = "a1111") -> list[dict]:
    # Split commas only outside balanced weighting / LoRA constructs; preserve escaped commas.
    parts, buffer, stack, escaped = [], [], [], False
    for char in prompt:
        if escaped:
            buffer.extend(("\\", char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "([{<":
            stack.append(char)
        elif char in ")]}>" and stack:
            stack.pop()
        if char == "," and not stack:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if escaped:
        buffer.append("\\")
    parts.append("".join(buffer))
    result = []
    for part in parts:
        part, weight = part.strip(), 1.0
        match = re.fullmatch(r"\((.*):(-?\d+(?:\.\d+)?)\)", part, re.S)
        if match:
            part, weight = match[1], float(match[2])
        else:
            for opening, closing, factor in (("(", ")", 1.1), ("{", "}", 1.05), ("[", "]", 1 / (1.05 if generator == "novelai" else 1.1))):
                while part.startswith(opening) and part.endswith(closing):
                    part, weight = part[1:-1], weight * factor
        # Groups may contain several tags with the same weight.
        values = tokenize_prompt(part, generator) if "," in part and part not in parts and "\\," not in part else None
        if values:
            for item in values:
                item["weight"] *= weight
                item["position"] = len(result)
                result.append(item)
            continue
        token = canonical(part.replace("\\,", ","))
        if token:
            tag = DICTIONARY.get(token)
            result.append({"token": token, "weight": weight, "position": len(result),
                           "category": tag.split(":")[0] if tag else "other"})
    return result
