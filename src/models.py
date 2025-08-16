from dataclasses import dataclass


@dataclass
class DebateCard:
    card_id: str
    tagline: str
    tagline_html: str
    citation_raw: str
    citation_html: str
    cite: str
    author_surname: str
    author_year: str
    author_credentials: str
    publication_title: str
    publisher: str
    url: str
    credits: str
    body_text: str
    body_html: str
    highlighted_text: str
    source_filename: str
    paragraph_start: int
    paragraph_end: int
    word_count: int
    highlight_count: int
    created_at: str
