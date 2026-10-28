from dataclasses import dataclass, field


@dataclass
class Novel:
    title: str
    url: str = ""
    author: str = ""
    cover_url: str = ""
    language: str = "pt"


@dataclass
class Chapter:
    number: int
    title: str
    volume: str = "1"
    url: str = ""
    date: str = ""
    source_id: str = ""
    paragraphs: list[str] = field(default_factory=list)
    suggested: bool = True

    @property
    def chars(self):
        return sum(len(p) for p in self.paragraphs)
