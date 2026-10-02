"""Deterministic synthetic images; no personal files are bundled with tests."""
import gzip
import json
from pathlib import Path

from PIL import Image, ImageDraw, PngImagePlugin

target = Path("backend/tests/fixtures")
target.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (640, 840), "#1e342f")
draw = ImageDraw.Draw(image)
for y in range(840):
    draw.line((0, y, 640, y), fill=(20 + y // 45, 44 + y // 30, 42 + y // 38))
draw.ellipse((135, 105, 510, 480), fill="#d5bb8d")
draw.polygon([(0, 600), (240, 330), (500, 840), (0, 840)], fill="#47675b")
draw.polygon([(130, 840), (475, 385), (640, 575), (640, 840)], fill="#708572")
info = PngImagePlugin.PngInfo()
info.add_text("parameters", "white hair, blue eyes, cinematic lighting, (masterpiece:1.2)\nNegative prompt: bad hands, low quality\nSteps: 28, Sampler: DPM++ 2M Karras, CFG scale: 5, Seed: 18446744073709551615, Model: illustrative-xl")
image.save(target / "a1111.png", pnginfo=info)
data = {"Software": "NovelAI", "Source": "nai-diffusion-4", "Comment": json.dumps({"prompt": "white hair, blue eyes", "uc": "low quality", "seed": 42, "steps": 28, "scale": 5, "sampler": "k_euler"})}
info = PngImagePlugin.PngInfo()
for key, value in data.items():
    info.add_text(key, value)
image.save(target / "novelai.png", pnginfo=info)
character_data = {"Software": "NovelAI", "Source": "nai-diffusion-4-full", "Comment": json.dumps({
    "seed": 4321098765432109876, "steps": 28, "scale": 5, "sampler": "k_euler",
    "v4_prompt": {"caption": {"base_caption": "two people, garden", "char_captions": [
        {"char_caption": "white hair, blue eyes", "centers": [{"x": 0.2, "y": 0.5}]},
        {"char_caption": "black hair, smile", "centers": [{"x": 0.8, "y": 0.5}]}]}, "use_coords": True},
    "v4_negative_prompt": {"caption": {"base_caption": "low quality", "char_captions": [
        {"char_caption": "red eyes"}, {"char_caption": "hat"}]}}})}
character_info = PngImagePlugin.PngInfo()
for key, value in character_data.items():
    character_info.add_text(key, value)
image.save(target / "novelai-characters.png", pnginfo=character_info)
payload = gzip.compress(json.dumps(data).encode(), mtime=0)
encoded = b"stealth_pngcomp" + (len(payload) * 8).to_bytes(4, "big") + payload
stealth = image.convert("RGBA")
pixels = stealth.load()
for index, bit in enumerate(int(bit) for byte in encoded for bit in f"{byte:08b}"):
    x, y = divmod(index, stealth.height)
    pixels[x, y] = (*pixels[x, y][:3], 254 | bit)
stealth.save(target / "novelai-stealth.png")
graph = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "illustrative-xl"}},
         "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "white hair, blue eyes"}},
         "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad hands"}},
         "4": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "seed": 42, "steps": 28, "cfg": 5, "sampler_name": "euler", "scheduler": "normal"}}}
info = PngImagePlugin.PngInfo()
info.add_text("prompt", json.dumps(graph))
info.add_text("workflow", json.dumps({"last_node_id": 4, "nodes": [], "links": [], "version": 0.4}))
image.save(target / "comfyui.png", pnginfo=info)
image.save(target / "plain.webp")
(target / "malformed.png").write_bytes(b"not a PNG")
