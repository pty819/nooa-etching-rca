"""Keep Chinese splitting and match Sphinx's supplied English JS stemmer name.

Sphinx 9.1 SearchChinese uses english-stemmer.js, but the generic builder emits
window.Stemmer = ChineseStemmer from its language_name. Use the public language
registration API rather than editing Sphinx or patching generated output.
"""

from sphinx.search.zh import SearchChinese


class ChineseSearchWithMatchingStemmer(SearchChinese):
    # This names the JS constructor only; lang, jieba splitting and Python stem
    # behaviour remain Chinese search's existing implementation.
    language_name = "English"


def setup(app):
    app.add_search_language(ChineseSearchWithMatchingStemmer)
    return {"version": "1.0", "parallel_read_safe": True, "parallel_write_safe": True}
