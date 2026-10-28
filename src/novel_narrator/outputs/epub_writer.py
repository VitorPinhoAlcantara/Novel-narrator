import uuid
import zipfile
from html import escape

_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CSS = "body{font-family:serif;line-height:1.5}h1{font-size:1.4em;margin:1em 0}p{margin:0 0 .8em}"


def write_volume_epub(path, book_title, chapters, author="", series="", series_index=None, language="pt"):
    identifier = f"urn:uuid:{uuid.uuid4()}"
    series_meta = ""
    if series:
        series_meta += (
            f'    <meta property="belongs-to-collection" id="series">{escape(series)}</meta>\n'
            '    <meta refines="#series" property="collection-type">series</meta>\n'
        )
        if series_index is not None:
            series_meta += (
                f'    <meta refines="#series" property="group-position">{escape(str(series_index))}</meta>\n'
                f'    <meta name="calibre:series" content="{escape(series)}"/>\n'
                f'    <meta name="calibre:series_index" content="{escape(str(series_index))}"/>\n'
            )
    author_xml = f"    <dc:creator>{escape(author)}</dc:creator>\n" if author else ""
    items = "".join(
        f'    <item id="ch{i}" href="ch_{i:04d}.xhtml" media-type="application/xhtml+xml"/>\n'
        for i in range(len(chapters))
    )
    spine = "".join(f'    <itemref idref="ch{i}"/>\n' for i in range(len(chapters)))
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{identifier}</dc:identifier>
    <dc:title>{escape(book_title)}</dc:title>
{author_xml}    <dc:language>{escape(language)}</dc:language>
{series_meta}  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
{items}  </manifest>
  <spine>
{spine}  </spine>
</package>
"""
    nav_entries = "".join(
        f'      <li><a href="ch_{i:04d}.xhtml">{escape(title)}</a></li>\n' for i, (title, _) in enumerate(chapters)
    )
    nav = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}">
<head><title>{escape(book_title)}</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <ol>
{nav_entries}    </ol>
  </nav>
</body>
</html>
"""
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", _CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        epub.writestr("OEBPS/style.css", _CSS, compress_type=zipfile.ZIP_DEFLATED)
        for i, (title, paragraphs) in enumerate(chapters):
            body = "\n".join(f"  <p>{escape(p)}</p>" for p in paragraphs)
            page = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}">
<head><title>{escape(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
  <h1>{escape(title)}</h1>
{body}
</body>
</html>
"""
            epub.writestr(f"OEBPS/ch_{i:04d}.xhtml", page, compress_type=zipfile.ZIP_DEFLATED)
    return path
