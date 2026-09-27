"""Source decoding beyond UTF-8."""

from __future__ import annotations

from findio.text import decode_source


def test_decode_source_handles_bom_cookie_and_legacy_bytes() -> None:
    assert decode_source("print('ok')\n".encode("utf-8-sig")) == "print('ok')\n"
    assert decode_source("print('ok')\n".encode("utf-16")) == "print('ok')\n"
    assert decode_source("print('ok')\n".encode("utf-16-le")) == "print('ok')\n"
    assert decode_source("# coding: latin-1\n# café\n".encode("latin-1")) == "# coding: latin-1\n# café\n"
    assert decode_source("# coding: cp1252\n# €\n".encode("cp1252")) == "# coding: cp1252\n# €\n"
