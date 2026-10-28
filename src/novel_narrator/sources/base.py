class SourceError(RuntimeError):
    pass


class Source:
    name = ""

    def popular(self, limit=10):
        return []

    def search(self, query, limit=10):
        return []

    def parse_url(self, text):
        return None

    def novel(self, url):
        raise NotImplementedError

    def chapters(self, novel):
        raise NotImplementedError

    def chapter_text(self, chapter):
        return chapter.paragraphs

    def cover(self, novel):
        return None
