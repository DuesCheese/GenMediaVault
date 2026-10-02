import gzip
import json

import pytest

from PIL import Image, PngImagePlugin

from genmedia.parsers import extract, normalize, sidecars_for, tokenize_prompt


def png(tmp_path, info=None, name="sample.png"):
    metadata = PngImagePlugin.PngInfo()
    for key, value in (info or {}).items():
        metadata.add_text(key, value if isinstance(value, str) else json.dumps(value))
    path = tmp_path / name
    Image.new("RGB", (64, 96), "#c5a88b").save(path, pnginfo=metadata)
    return path


def test_a1111_seed_and_weight(tmp_path):
    path = png(tmp_path, {"parameters": "white hair, (blue eyes:1.2), <lora:portrait:0.8>\nNegative prompt: bad hands\nSteps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 18446744073709551615, Model: portrait-v2"})
    result = normalize(extract(path)).normalized
    assert result["seed"] == "18446744073709551615"
    assert result["negative"] == "bad hands"
    assert result["scheduler"] == "karras"
    assert result["loras"] == [{"name": "portrait", "weight": 0.8}]


def test_novelai_regular_and_stealth(tmp_path):
    data = {"Software": "NovelAI", "Source": "nai-diffusion-4", "Description": "white hair",
            "Comment": json.dumps({"prompt": "blue eyes", "uc": "bad hands", "seed": 987654321012345678,
                                    "steps": 28, "scale": 5, "sampler": "k_euler"})}
    assert normalize(extract(png(tmp_path, data))).normalized["prompt"] == "blue eyes"
    payload = gzip.compress(json.dumps(data).encode())
    encoded = b"stealth_pngcomp" + (len(payload) * 8).to_bytes(4, "big") + payload
    image = Image.new("RGBA", (128, 128), (100, 120, 140, 254))
    pixels = image.load()
    bits = [int(bit) for byte in encoded for bit in f"{byte:08b}"]
    for index, bit in enumerate(bits):
        x, y = divmod(index, image.height)
        pixels[x, y] = (100, 120, 140, 254 | bit)
    path = tmp_path / "stealth.png"
    image.save(path)
    result = normalize(extract(path))
    assert result.normalized["generator"] == "novelai"
    assert result.normalized["seed"] == "987654321012345678"


def test_comfy_graph_and_ambiguous_branch(tmp_path):
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "white hair"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands"}},
        "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0],
                "negative": ["3", 0], "seed": 42, "cfg": 5, "steps": 20, "sampler_name": "euler"}},
    }
    result = normalize(extract(png(tmp_path, {"prompt": graph, "workflow": {"nodes": []}})))
    assert result.normalized["prompt"] == "white hair"
    assert result.normalized["model"] == "model.safetensors"
    assert result.normalized["execution_graph"] == graph
    graph["5"] = graph["4"]
    result = normalize(extract(png(tmp_path, {"prompt": graph})))
    assert result.warnings
    assert "seed" not in result.normalized


def test_sidecar_conflict_and_ambiguity(tmp_path):
    path = png(tmp_path, {"parameters": "original\nSteps: 20, Seed: 123"})
    (tmp_path / "sample.png.json").write_text(json.dumps({"schema_version": 1, "generation": {"seed": "456"}}))
    result = normalize(extract(path))
    assert result.normalized["seed"] == "456"
    assert result.conflicts[0]["previous"] == "123"
    assert result.conflicts[0]["previous_source"] == "embedded"
    assert result.conflicts[0]["incoming_source"] == "sample.png.json"
    assert result.confidence == 1
    assert result.raw["sidecars"][0]["name"] == "sample.png.json"
    assert result.normalized["parsing"]["field_sources"]["seed"] == "sample.png.json"
    assert sidecars_for(path) == [tmp_path / "sample.png.json"]
    (tmp_path / "sample.png.json").unlink()
    (tmp_path / "sample.json").write_text("{}")
    Image.new("RGB", (64, 64)).save(tmp_path / "sample.jpg")
    assert sidecars_for(path) == []


def test_plain_image_and_unknown_fields_preserved(tmp_path):
    bundle = extract(png(tmp_path, {"unknown": "a future parameter"}))
    assert bundle["info"]["unknown"] == "a future parameter"
    assert normalize(bundle).normalized["generator"] == "unknown"
    assert normalize(bundle).confidence == 0


def test_weighted_tokens_and_escaped_comma():
    tokens = tokenize_prompt(r"white hair, (blue eyes:1.2), sky\, sunset, {{masterpiece}}")
    assert tokens[1]["weight"] == 1.2
    assert tokens[2]["token"] == "sky, sunset"
    assert tokens[3]["token"] == "masterpiece"
    assert tokens[3]["weight"] > 1


@pytest.mark.parametrize("extension,format", [("jpg", "JPEG"), ("webp", "WEBP")])
def test_a1111_unicode_exif(tmp_path, extension, format):
    parameters = "白发, blue eyes\nNegative prompt: bad hands\nSteps: 20, Seed: 18446744073709551615"
    exif = Image.Exif()
    exif[34665] = {37510: b"UNICODE\0" + parameters.encode("utf-16-be")}
    path = tmp_path / f"exif.{extension}"
    Image.new("RGB", (50, 50)).save(path, format=format, exif=exif)
    result = normalize(extract(path)).normalized
    assert result["prompt"] == "白发, blue eyes"
    assert result["seed"] == "18446744073709551615"


def test_novelai_webp_exif(tmp_path):
    exif = Image.Exif()
    exif[305] = "nai-diffusion-4"
    exif[270] = "white hair"
    exif[34665] = {37510: b"ASCII\0\0\0" + json.dumps({"prompt": "white hair", "uc": "bad hands", "sampler": "k_euler", "seed": 42}).encode()}
    path = tmp_path / "nai.webp"
    Image.new("RGBA", (64, 64)).save(path, exif=exif)
    result = normalize(extract(path)).normalized
    assert result["model"] == "nai-diffusion-4"
    assert result["generator"] == "novelai"


def test_novelai_reduced_weight():
    assert tokenize_prompt("[white hair]", "novelai")[0]["weight"] == pytest.approx(1 / 1.05)
