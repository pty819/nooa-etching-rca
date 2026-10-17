"""Check built internal links, generated Mermaid SVGs and source traceability."""

import json
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/sphinx"
SITE = ROOT / "docs/_build/html"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for name in ("href", "src", "data" if tag == "object" else "src"):
            if attrs.get(name):
                self.links.append(attrs[name])


def check():
    failures = []
    docs = sorted(SITE.rglob("*.html"))
    parsed = {}
    for path in docs:
        page = Links()
        text = path.read_text()
        page.feed(text)
        parsed[path.resolve()] = page
        for private in ["/" + "Users" + "/", "cloud-conversation.json"]:
            if private in text:
                failures.append(f"private material in {path.relative_to(SITE)}")
    for path, page in parsed.items():
        for value in page.links:
            url = urlsplit(value)
            if url.scheme or url.netloc:
                continue
            target = (
                (
                    (SITE if url.path.startswith("/") else path.parent)
                    / unquote(url.path).lstrip("/")
                ).resolve()
                if url.path
                else path
            )
            if target.is_dir():
                target /= "index.html"
            if not target.is_relative_to(SITE.resolve()) or not target.exists():
                failures.append(f"missing local asset/link: {path.name} -> {value}")
            elif (
                url.fragment
                and target.suffix == ".html"
                and unquote(url.fragment) not in parsed.get(target, Links()).ids
            ):
                failures.append(f"missing anchor: {path.name} -> {value}")
    diagrams = sorted((SOURCE / "diagrams").glob("*.mmd"))
    images = sorted(
        {
            (path.parent / unquote(urlsplit(value).path)).resolve()
            for path, page in parsed.items()
            for value in page.links
            if urlsplit(value).path.endswith(".svg") and "mermaid-" in value
        }
    )
    if len(images) != len(diagrams):
        failures.append(f"Mermaid source/SVG mismatch: {len(diagrams)} vs {len(images)}")
    for path in images:
        tree = ET.parse(path)
        if not tree.getroot().attrib.get("viewBox"):
            failures.append(f"SVG missing viewBox: {path.name}")
        if "Syntax error in text" in path.read_text():
            failures.append(f"Mermaid syntax error: {path.name}")
    index = (SOURCE / "index.md").read_text()
    for path in SOURCE.glob("*.md"):
        if path.stem != "index" and not re.search(
            rf"^{re.escape(path.stem)}$", index, re.MULTILINE
        ):
            failures.append(f"chapter missing from toctree: {path.name}")
    trace = (SOURCE / "traceability.md").read_text()
    for file in set(re.findall(r"\b[a-z_]+\.py\b", trace)):
        if not any(
            (ROOT / base / file).exists() for base in ["src/factor_demo", "tests", "scripts"]
        ):
            failures.append(f"traceability file missing: {file}")
    result = {
        "html_pages": len(docs),
        "chapters": len(list(SOURCE.glob("*.md"))),
        "mermaid_sources": len(diagrams),
        "rendered_svg": len(images),
        "local_links": sum(len(x.links) for x in parsed.values()),
        "failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)
    return result


if __name__ == "__main__":
    check()
