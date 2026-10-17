"""Documentation-only build: never imports the research runtime or reads credentials."""

from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT / "scripts/build_doc_reference.py"), run_name="__main__")

project = "NOOA Etching RCA"
author = "pty819"
copyright = "2026, pty819"
release = "0.1.0"
language = "zh_CN"
extensions = ["myst_parser", "sphinxcontrib.mermaid", "sphinx.ext.mathjax"]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "diagrams/**", "_static/**"]
nitpicky = True
myst_enable_extensions = ["colon_fence", "dollarmath", "fieldlist"]
html_theme = "furo"
html_title = "NOOA · 刻蚀根因研究系统设计"
html_static_path = ["_static"]
html_css_files = ["design.css"]
html_js_files = ["diagrams.js"]
html_theme_options = {
    "source_repository": "https://github.com/pty819/nooa-etching-rca/",
    "source_branch": "main",
    "source_directory": "docs/sphinx/",
    "navigation_with_keys": True,
    "light_css_variables": {"color-brand-primary": "#195c57", "color-brand-content": "#146660"},
    "dark_css_variables": {"color-brand-primary": "#81d3c3", "color-brand-content": "#9bd9cb"},
}
html_show_sphinx = False
html_copy_source = True
html_baseurl = "https://pty819.github.io/nooa-etching-rca/"
# Render on the builder, so readers need no CDN access or JavaScript for diagrams.
mermaid_output_format = "svg"
mermaid_cmd = str(ROOT / "docs/node_modules/.bin/mmdc")
mermaid_params = [
    "--puppeteerConfigFile",
    str(ROOT / "docs/puppeteer.json"),
    "--backgroundColor",
    "transparent",
    "--size",
    "1600",
]
mermaid_config = {
    "theme": "base",
    "securityLevel": "strict",
    "themeVariables": {
        "fontFamily": "Noto Sans CJK SC, PingFang SC, sans-serif",
        "fontSize": "16px",
        "primaryColor": "#e7f2ef",
        "primaryBorderColor": "#37786f",
        "primaryTextColor": "#183d39",
        "lineColor": "#617b78",
        "secondaryColor": "#edf1f7",
        "tertiaryColor": "#fff2dd",
    },
    "flowchart": {"htmlLabels": False, "curve": "basis", "nodeSpacing": 28, "rankSpacing": 42},
    "sequence": {"useMaxWidth": True, "wrap": True, "actorMargin": 35, "messageMargin": 28},
}
linkcheck_ignore = [r"https://github.com/pty819/nooa-etching-rca/.*"]
