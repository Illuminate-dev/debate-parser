import re
from typing import Dict


class CitationParser:
    def __init__(self):
        self._compile_regex_patterns()

    def _compile_regex_patterns(self):
        """Pre-compile all regex patterns used in parsing"""
        self.author_year_pattern = re.compile(
            r"^([A-Za-z]+(?:\s[A-Za-z]+)*)\s+(\d{2,4})"
        )
        self.url_pattern = re.compile(r"https?://[^\s,)]+")
        self.title_pattern = re.compile(r'["""]([^"""]+)["""]')
        self.credentials_pattern = re.compile(r"\[([^\]]+)\]")
        self.credentials_check_pattern = re.compile(r"\[.*\]")
        self.credits_pattern = re.compile(r"//\s*(.+)$")
        self.citation_check_pattern = re.compile(r"[A-Za-z]+\s+\d{2,4}")

    def parse_citation(
        self, citation_text: str, cite_bolded_text: str = ""
    ) -> Dict[str, str]:
        """Parse citation text into structured components"""
        result = {
            "author_surname": "",
            "author_year": "",
            "author_credentials": "",
            "publication_title": "",
            "publisher": "",
            "url": "",
            "credits": "",
        }

        if cite_bolded_text:
            bolded_parts = cite_bolded_text.split()
            if bolded_parts:
                result["author_surname"] = bolded_parts[0]

        if not result["author_surname"]:
            author_match = self.author_year_pattern.match(citation_text)
            if author_match:
                result["author_surname"] = author_match.group(1)
                result["author_year"] = author_match.group(2)

        if not result["author_year"]:
            author_match = self.author_year_pattern.search(citation_text)
            if author_match:
                result["author_year"] = author_match.group(2)

        url_match = self.url_pattern.search(citation_text)
        if url_match:
            result["url"] = url_match.group()

        title_match = self.title_pattern.search(citation_text)
        if title_match:
            result["publication_title"] = title_match.group(1)

        cred_match = self.credentials_pattern.search(citation_text)
        if cred_match:
            result["author_credentials"] = cred_match.group(1)

        credits_match = self.credits_pattern.search(citation_text)
        if credits_match:
            result["credits"] = credits_match.group(1)

        return result

    def is_valid_citation(self, citation_para) -> bool:
        """Check if a paragraph is a valid citation"""
        return (
            citation_para.style.name in ["Normal", "Normal (Web)"]
            and len(citation_para.text) >= 10
            and (
                self.citation_check_pattern.search(citation_para.text)
                or self.credentials_check_pattern.search(citation_para.text)
                or "http" in citation_para.text.lower()
                or ".org" in citation_para.text.lower()
                or ".com" in citation_para.text.lower()
                or ".edu" in citation_para.text.lower()
                or any(
                    word in citation_para.text.lower()
                    for word in ["professor", "university", "phd", "dr.", "institute"]
                )
            )
        )
