#!/usr/bin/env python3
"""
Debate Card Parser
"""

import argparse
import csv
import hashlib
import json
import multiprocessing
import re
import sys
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import docx
import psycopg2
from alive_progress import alive_bar
from datasketch import MinHash
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml.ns import qn
from docx.shared import RGBColor
from psycopg2.extras import RealDictCursor


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


class DebateCardParser:
    def __init__(
        self,
        db_config: Optional[Dict] = None,
        debug_duplicates: bool = False,
        skip_duplicates: bool = False,
    ):
        self.db_config = db_config
        self.parsed_cards = []
        self.malformed_cards = []
        self.debug_duplicates = debug_duplicates
        self.skip_duplicates = skip_duplicates

        self.content_hashes: Set[str] = set()

        self.fuzzy_hash_signatures: Set[str] = (
            set()
        )  # Store MinHash signatures as strings
        self.near_duplicates = []

        self.num_perm = 64
        self.similarity_threshold = 0.75

        self._compile_regex_patterns()

        self._xpath_cache = {}

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

    def _generate_content_hash(
        self, tagline: str, author_surname: str, author_year: str, body_text: str
    ) -> str:
        """Generate a hash for exact duplicate detection"""
        normalized_content = (
            tagline.strip().lower()
            + author_surname.strip().lower()
            + author_year.strip()
            + body_text.strip().lower()
        )
        return hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

    def _generate_fuzzy_hash(self, text: str) -> MinHash:
        """Generate MinHash for fuzzy similarity detection"""
        normalized_text = text.strip().lower()
        words = normalized_text.split()

        minhash = MinHash(num_perm=self.num_perm)

        # Add word-level tokens
        for word in words:
            minhash.update(word.encode("utf-8"))
            # Add character 3-grams directly
            for i in range(len(word) - 2):
                minhash.update(word[i : i + 3].encode("utf-8"))

        return minhash

    def _compute_fuzzy_similarity(self, minhash1: MinHash, minhash2: MinHash) -> float:
        return minhash1.jaccard(minhash2)

    def _check_xpath_cached(self, element, query):
        if query not in self._xpath_cache:
            try:
                self._xpath_cache[query] = element.xpath(query)
            except:
                self._xpath_cache[query] = []
        return self._xpath_cache[query]

    def extract_formatting_as_html(
        self, paragraph, is_citation_paragraph: bool = False
    ) -> str:
        html_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            tags = []
            close_tags = []

            is_bold = (
                run.bold
                or (hasattr(run.font, "bold") and run.font.bold is True)
                or (
                    run.style
                    and run.style.name
                    and any(
                        keyword in run.style.name.lower()
                        for keyword in ["bold", "strong", "emphasis"]
                    )
                )
            )

            if not is_bold and run._element.rPr is not None:
                try:
                    bold_elements = self._check_xpath_cached(run._element.rPr, ".//w:b")
                    if bold_elements:
                        is_bold = True
                except:
                    pass

            # Use <cite> tags for bold text in citation paragraphs, <strong> elsewhere
            if is_bold:
                if is_citation_paragraph:
                    tags.append("<cite>")
                    close_tags.insert(0, "</cite>")
                else:
                    tags.append("<strong>")
                    close_tags.insert(0, "</strong>")

            is_italic = run.italic or (
                hasattr(run.font, "italic") and run.font.italic is True
            )

            # Only check XML if other methods failed
            if not is_italic and run._element.rPr is not None:
                try:
                    italic_elements = self._check_xpath_cached(
                        run._element.rPr, ".//w:i"
                    )
                    if italic_elements:
                        is_italic = True
                except:
                    pass

            if is_italic:
                tags.append("<em>")
                close_tags.insert(0, "</em>")

            # Check for underline
            if run.underline:
                tags.append("<u>")
                close_tags.insert(0, "</u>")

            # Check for highlighting
            if run._element.rPr is not None:
                highlight = run._element.rPr.highlight_val
                if highlight:
                    # Map Word highlighting to CSS colors
                    color_map = {
                        3: "#00FFFF",  # Turquoise
                        4: "#00FF00",  # Green
                        5: "#FFFF00",  # Yellow
                        6: "#FF00FF",  # Magenta
                        7: "#0000FF",  # Blue
                    }
                    bg_color = color_map.get(highlight, "#FFFF00")
                    tags.append(f'<mark style="background-color: {bg_color};">')
                    close_tags.insert(0, "</mark>")

            # Check for font color
            if run.font.color and run.font.color.rgb:
                color_hex = str(run.font.color.rgb)
                tags.append(f'<span style="color: #{color_hex};">')
                close_tags.insert(0, "</span>")

            # Combine tags with text
            formatted_text = "".join(tags) + text + "".join(close_tags)
            html_parts.append(formatted_text)

        return "".join(html_parts)

    def extract_formatting_and_bolded_text(
        self, paragraph, is_citation_paragraph: bool = False
    ) -> tuple[str, str]:
        """Convert a paragraph with rich formatting to HTML and extract bolded text"""
        html_parts = []
        bolded_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            tags = []
            close_tags = []

            # Check for bold formatting
            is_bold = (
                run.bold
                or (hasattr(run.font, "bold") and run.font.bold is True)
                or (
                    run.style
                    and run.style.name
                    and any(
                        keyword in run.style.name.lower()
                        for keyword in ["bold", "strong", "emphasis"]
                    )
                )
            )

            if not is_bold and run._element.rPr is not None:
                try:
                    bold_elements = self._check_xpath_cached(run._element.rPr, ".//w:b")
                    if bold_elements:
                        is_bold = True
                except:
                    pass

            if is_bold:
                bolded_parts.append(text)
                if is_citation_paragraph:
                    tags.append("<cite>")
                    close_tags.insert(0, "</cite>")
                else:
                    tags.append("<strong>")
                    close_tags.insert(0, "</strong>")

            # Check for italic formatting
            is_italic = run.italic or (
                hasattr(run.font, "italic") and run.font.italic is True
            )

            if not is_italic and run._element.rPr is not None:
                try:
                    italic_elements = self._check_xpath_cached(
                        run._element.rPr, ".//w:i"
                    )
                    if italic_elements:
                        is_italic = True
                except:
                    pass

            if is_italic:
                tags.append("<em>")
                close_tags.insert(0, "</em>")

            # Check for underline
            if run.underline:
                tags.append("<u>")
                close_tags.insert(0, "</u>")

            # Check for highlighting
            if run._element.rPr is not None:
                highlight = run._element.rPr.highlight_val
                if highlight:
                    # Map Word highlighting to CSS colors
                    color_map = {
                        3: "#00FFFF",  # Turquoise
                        4: "#00FF00",  # Green
                        5: "#FFFF00",  # Yellow
                        6: "#FF00FF",  # Magenta
                        7: "#0000FF",  # Blue
                    }
                    bg_color = color_map.get(highlight, "#FFFF00")
                    tags.append(f'<mark style="background-color: {bg_color};">')
                    close_tags.insert(0, "</mark>")

            if run.font.color and run.font.color.rgb:
                color_hex = str(run.font.color.rgb)
                tags.append(f'<span style="color: #{color_hex};">')
                close_tags.insert(0, "</span>")

            formatted_text = "".join(tags) + text + "".join(close_tags)
            html_parts.append(formatted_text)

        return "".join(html_parts), " ".join(bolded_parts)

    def debug_run_formatting(self, paragraph) -> None:
        """Debug function to print formatting information for each run in a paragraph"""
        print(f"\nDebugging paragraph: {paragraph.text[:100]}...")

        for i, run in enumerate(paragraph.runs):
            print(f"  Run {i}: '{run.text}'")
            print(f"    Direct bold: {run.bold}")
            print(f"    Font bold: {getattr(run.font, 'bold', None)}")
            print(f"    Style: {run.style.name if run.style else 'None'}")
            if run.style:
                print(f"    Style ID: {getattr(run.style, 'style_id', 'None')}")
                print(f"    Style type: {getattr(run.style, 'type', 'None')}")
                if hasattr(run.style, "_element") and run.style._element is not None:
                    print(f"    Style element: {run.style._element.tag}")

            if run._element.rPr is not None:
                try:
                    bold_elements = run._element.rPr.xpath(".//w:b")
                    italic_elements = run._element.rPr.xpath(".//w:i")
                    print(f"    XML bold elements: {len(bold_elements)}")
                    print(f"    XML italic elements: {len(italic_elements)}")
                except:
                    print("    XML parsing failed")
            print()

    # Also method to specifically handle citation formatting
    def extract_citation_with_enhanced_formatting(
        self, citation_para
    ) -> Tuple[str, str]:
        """Extract citation with enhanced formatting detection"""

        # self.debug_run_formatting(citation_para)

        citation_raw = citation_para.text
        citation_html = self.extract_formatting_as_html(
            citation_para, is_citation_paragraph=True
        )

        return citation_raw, citation_html

    def extract_highlighted_text(self, paragraph) -> str:
        """Extract only the highlighted text from a paragraph"""
        highlighted_parts = []

        for run in paragraph.runs:
            if run._element.rPr is not None and run._element.rPr.highlight_val:
                highlighted_parts.append(run.text)

        return " ".join(highlighted_parts)

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

    def is_duplicate(
        self,
        tagline: str,
        author_surname: str,
        author_year: str,
        body_text: str,
        debug: bool = False,
    ) -> bool:
        """Check if this card is a duplicate using hash-based detection and fuzzy hash approximate detection"""

        if self.skip_duplicates:
            return False

        if debug:
            print(f"\n=== DEBUG ===")
            print(f"Tagline: {tagline[:50]}...")
            print(f"Author: {author_surname} {author_year}")
            print(f"Body length: {len(body_text)} chars")

        # First, check for exact duplicates using hash lookup
        content_hash = self._generate_content_hash(
            tagline, author_surname, author_year, body_text
        )

        if debug:
            print(f"Content hash: {content_hash[:16]}...")
            print(f"Hash already exists: {content_hash in self.content_hashes}")
            print(f"Total unique hashes so far: {len(self.content_hashes)}")

        if content_hash in self.content_hashes:
            if debug:
                print("!!EXACT DUPLICATE!!")

            self.near_duplicates.append(
                {
                    "new_tagline": tagline,
                    "existing_tagline": "N/A",
                    "author": author_surname,
                    "year": author_year,
                    "similarity": 1.0,
                    "duplicate_type": "exact_hash",
                }
            )
            return True

        self.content_hashes.add(content_hash)

        # Then, check for fuzzy duplicates using MinHash signature lookup
        tagline_minhash = self._generate_fuzzy_hash(tagline)
        fuzzy_signature = str(tagline_minhash.digest())

        if debug:
            print(f"Fuzzy hash signature: {fuzzy_signature[:16]}...")
            print(
                f"Fuzzy signature already exists: {fuzzy_signature in self.fuzzy_hash_signatures}"
            )
            print(f"Total unique fuzzy signatures: {len(self.fuzzy_hash_signatures)}")

        if fuzzy_signature in self.fuzzy_hash_signatures:
            if debug:
                print("!! FUZZY DUPLICATE")

            self.near_duplicates.append(
                {
                    "new_tagline": tagline,
                    "existing_tagline": "N/A (fuzzy signature match)",
                    "author": author_surname,
                    "year": author_year,
                    "similarity": 0.8,
                    "duplicate_type": "fuzzy_hash",
                }
            )
            return True

        self.fuzzy_hash_signatures.add(fuzzy_signature)

        if debug:
            print(f"Total fuzzy signatures: {len(self.fuzzy_hash_signatures)}")

        return False

    def parse_docx(self, file, filename: str) -> List[DebateCard]:
        """Parse a single .docx file and extract debate cards"""
        doc = docx.Document(file)
        cards = []

        # Cache paragraph count to avoid repeated len() calls
        paragraph_count = len(doc.paragraphs)

        for i in range(paragraph_count - 1):
            # Look for Heading 4 taglines
            current_para = doc.paragraphs[i]
            if current_para.style.name != "Heading 4":
                continue

            # Check if next paragraph exists and is citation
            if i + 1 >= paragraph_count:
                self._add_malformed_card(
                    filename, i, current_para.text, "No next paragraph found", doc, i
                )
                continue

            citation_para = doc.paragraphs[i + 1]
            if not self._is_valid_citation(citation_para):
                self._add_malformed_card(
                    filename, i, current_para.text, "No valid citation found", doc, i
                )
                continue

            try:
                card = self._create_card_from_paragraphs(
                    doc, i, filename, paragraph_count
                )
                if card:
                    # Check for duplicates before adding
                    if not self.is_duplicate(
                        card.tagline,
                        card.author_surname,
                        card.author_year,
                        card.body_text,
                        debug=self.debug_duplicates,
                    ):
                        cards.append(card)
            except Exception as e:
                self._add_malformed_card(
                    filename,
                    i,
                    current_para.text[:100],
                    f"Parsing error: {str(e)}",
                    doc,
                    i,
                )

        return cards

    def _add_malformed_card(
        self,
        filename: str,
        para_index: int,
        tagline: str,
        issue: str,
        doc,
        context_index: int,
    ):
        """Helper to add malformed card - reduces code duplication"""
        self.malformed_cards.append(
            {
                "filename": filename,
                "paragraph_index": para_index,
                "tagline": tagline,
                "issue": issue,
                "context": [
                    p.text[:100]
                    for p in doc.paragraphs[
                        max(0, context_index - 1) : context_index + 4
                    ]
                ],
            }
        )

    def _create_card_from_paragraphs(
        self, doc, tagline_index: int, filename: str, paragraph_count: int
    ) -> Optional[DebateCard]:
        """Create a DebateCard from document paragraphs starting at tagline_index"""
        tagline_para = doc.paragraphs[tagline_index]
        citation_para = doc.paragraphs[tagline_index + 1]
        citation_index = tagline_index + 1

        # Extract tagline and citation
        tagline = tagline_para.text
        tagline_html = self.extract_formatting_as_html(tagline_para)
        citation_raw = citation_para.text
        citation_html, cite_bolded = self.extract_formatting_and_bolded_text(
            citation_para, is_citation_paragraph=True
        )
        citation_parts = self.parse_citation(citation_raw, cite_bolded)

        body_data = self._extract_body_paragraphs(
            doc, citation_index + 1, paragraph_count
        )
        body_paras, body_html_parts, highlighted_parts, end_para = body_data

        body_text = "\n".join(body_paras)
        body_html = (
            "<p>" + "</p>\n<p>".join(body_html_parts) + "</p>"
            if body_html_parts
            else ""
        )
        highlighted_text = " ".join(highlighted_parts)

        return DebateCard(
            card_id=str(uuid.uuid4()),
            tagline=tagline,
            tagline_html=tagline_html,
            citation_raw=citation_raw,
            citation_html=citation_html,
            cite=cite_bolded,
            author_surname=citation_parts["author_surname"],
            author_year=citation_parts["author_year"],
            author_credentials=citation_parts["author_credentials"],
            publication_title=citation_parts["publication_title"],
            publisher=citation_parts["publisher"],
            url=citation_parts["url"],
            credits=citation_parts["credits"],
            body_text=body_text,
            body_html=body_html,
            highlighted_text=highlighted_text,
            source_filename=filename,
            paragraph_start=tagline_index,
            paragraph_end=end_para,
            word_count=len(body_text.split()) if body_text else 0,
            highlight_count=len(highlighted_parts),
            created_at=datetime.now().isoformat(),
        )

    def _extract_body_paragraphs(
        self, doc, start_index: int, paragraph_count: int
    ) -> Tuple[List[str], List[str], List[str], int]:
        """Extract body paragraphs efficiently"""
        body_paras = []
        body_html_parts = []
        highlighted_parts = []
        end_para = start_index

        for j in range(start_index, paragraph_count):
            para = doc.paragraphs[j]

            # Stop at non-body paragraphs or empty paragraphs
            if (
                para.style.name not in ["Normal", "List Paragraph"]
                or not para.text.strip()
            ):
                end_para = j - 1
                break

            body_paras.append(para.text)
            body_html_parts.append(self.extract_formatting_as_html(para))

            # Extract highlighted text if present
            highlighted = self.extract_highlighted_text(para)
            if highlighted:
                highlighted_parts.append(highlighted)

            end_para = j

        return body_paras, body_html_parts, highlighted_parts, end_para

    def parse_single_docx(self, docx_path: str) -> None:
        """Parse a single .docx file"""
        filename = Path(docx_path).name
        print(f"Parsing single document: {filename}")

        try:
            with open(docx_path, "rb") as docx_file:
                cards = self.parse_docx(docx_file, filename)
                self.parsed_cards.extend(cards)
                print(f"Found {len(cards)} cards in {filename}")
        except Exception as e:
            print(f"Error processing {filename}: {e}")

    def parse_zip_archive(
        self,
        zip_path: str,
        use_multiprocessing: bool = True,
        max_workers: Optional[int] = None,
    ) -> None:
        """Parse all .docx files in a zip archive with optional multiprocessing"""
        with zipfile.ZipFile(zip_path, "r") as zf:
            docx_files = [
                f
                for f in zf.infolist()
                if f.filename.endswith(".docx")
                and not f.filename.startswith("__MACOSX/")
                and not f.filename.split("/")[-1].startswith("._")
            ]

            print(f"Found {len(docx_files)} .docx files in archive")

            if not use_multiprocessing or len(docx_files) < 4:
                with alive_bar(len(docx_files), title="Parsing documents") as bar:
                    for file_info in docx_files:
                        try:
                            with zf.open(file_info.filename) as docx_file:
                                cards = self.parse_docx(docx_file, file_info.filename)
                                self.parsed_cards.extend(cards)
                        except Exception as e:
                            print(f"Error processing {file_info.filename}: {e}")
                        bar()
            else:
                if max_workers is None:
                    max_workers = min(multiprocessing.cpu_count(), len(docx_files))

                temp_files = []
                for file_info in docx_files:
                    with zf.open(file_info.filename) as docx_file:
                        temp_files.append((docx_file.read(), file_info.filename))

                with ProcessPoolExecutor(max_workers=max_workers) as executor:
                    with alive_bar(
                        len(temp_files), title="Parsing documents (parallel)"
                    ) as bar:
                        future_to_file = {
                            executor.submit(
                                self._parse_docx_data, file_data, filename
                            ): filename
                            for file_data, filename in temp_files
                        }

                        for future in as_completed(future_to_file):
                            filename = future_to_file[future]
                            try:
                                cards, malformed = future.result()
                                self.parsed_cards.extend(cards)
                                self.malformed_cards.extend(malformed)
                            except Exception as e:
                                print(f"Error processing {filename}: {e}")
                            bar()

    def _parse_docx_data(
        self, file_data: bytes, filename: str
    ) -> Tuple[List[DebateCard], List[Dict]]:
        """Parse docx data"""
        import tempfile
        from io import BytesIO

        with BytesIO(file_data) as file_obj:
            try:
                doc = docx.Document(file_obj)
                cards = []
                malformed_cards = []

                temp_parser = DebateCardParser(
                    db_config=None,
                    debug_duplicates=self.debug_duplicates,
                    skip_duplicates=True,
                )

                for i in range(len(doc.paragraphs) - 1):
                    if doc.paragraphs[i].style.name != "Heading 4":
                        continue

                    try:
                        card = temp_parser._parse_card_at_index(doc, i, filename)
                        if card:
                            cards.append(card)
                    except Exception as e:
                        malformed_cards.append(
                            {
                                "filename": filename,
                                "paragraph_index": i,
                                "tagline": doc.paragraphs[i].text[:100],
                                "issue": f"Parsing error: {str(e)}",
                                "context": [
                                    p.text[:100]
                                    for p in doc.paragraphs[max(0, i - 1) : i + 3]
                                ],
                            }
                        )

                return cards, malformed_cards
            except Exception as e:
                return [], [
                    {
                        "filename": filename,
                        "paragraph_index": -1,
                        "tagline": "Document error",
                        "issue": f"Document parsing error: {str(e)}",
                        "context": [],
                    }
                ]

    def _parse_card_at_index(self, doc, i: int, filename: str) -> Optional[DebateCard]:
        """Extract a single card at the given paragraph index"""
        # Check if next paragraph is citation
        if i + 1 >= len(doc.paragraphs):
            return None

        citation_para = doc.paragraphs[i + 1]
        citation_index = i + 1

        # Validate citation paragraph
        if not self._is_valid_citation(citation_para):
            return None

        # Extract tagline
        tagline_para = doc.paragraphs[i]
        tagline = tagline_para.text
        tagline_html = self.extract_formatting_as_html(tagline_para)

        # Extract citation
        citation_raw = citation_para.text
        citation_html, cite_bolded = self.extract_formatting_and_bolded_text(
            citation_para, is_citation_paragraph=True
        )
        citation_parts = self.parse_citation(citation_raw, cite_bolded)

        # Extract body paragraphs
        body_paras = []
        body_html_parts = []
        highlighted_parts = []
        end_para = citation_index + 1

        for j in range(citation_index + 1, len(doc.paragraphs)):
            para = doc.paragraphs[j]

            if (
                para.style.name not in ["Normal", "List Paragraph"]
                or para.text.strip() == ""
            ):
                end_para = j - 1
                break

            body_paras.append(para.text)
            body_html_parts.append(self.extract_formatting_as_html(para))

            highlighted = self.extract_highlighted_text(para)
            if highlighted:
                highlighted_parts.append(highlighted)

            end_para = j

        body_text = "\n".join(body_paras)
        body_html = "<p>" + "</p>\n<p>".join(body_html_parts) + "</p>"
        highlighted_text = " ".join(highlighted_parts)

        return DebateCard(
            card_id=str(uuid.uuid4()),
            tagline=tagline,
            tagline_html=tagline_html,
            citation_raw=citation_raw,
            citation_html=citation_html,
            cite=cite_bolded,
            author_surname=citation_parts["author_surname"],
            author_year=citation_parts["author_year"],
            author_credentials=citation_parts["author_credentials"],
            publication_title=citation_parts["publication_title"],
            publisher=citation_parts["publisher"],
            url=citation_parts["url"],
            credits=citation_parts["credits"],
            body_text=body_text,
            body_html=body_html,
            highlighted_text=highlighted_text,
            source_filename=filename,
            paragraph_start=i,
            paragraph_end=end_para,
            word_count=len(body_text.split()),
            highlight_count=len(highlighted_parts),
            created_at=datetime.now().isoformat(),
        )

    def _is_valid_citation(self, citation_para) -> bool:
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

    def save_malformed_cards_to_docx(self, output_path: str) -> None:
        """Save malformed cards to a separate .docx file for review"""
        if not self.malformed_cards:
            return

        doc = docx.Document()
        doc.add_heading("Malformed Cards Requiring Review", 0)

        for i, card in enumerate(self.malformed_cards):
            doc.add_heading(f"Card {i + 1}: {card['issue']}", level=1)

            doc.add_paragraph(f"File: {card['filename']}")
            doc.add_paragraph(f"Paragraph Index: {card['paragraph_index']}")
            doc.add_paragraph(f"Tagline: {card['tagline']}")

            doc.add_heading("Context:", level=2)
            for j, context_para in enumerate(card["context"]):
                p = doc.add_paragraph(f"Para {j}: {context_para}")
                if j == 1:
                    p.runs[0].bold = True

        doc.save(output_path)
        print(f"Saved {len(self.malformed_cards)} malformed cards to {output_path}")

    def setup_database(self) -> None:
        """Create database tables if they don't exist"""
        if not self.db_config:
            return

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS debate_cards (
            card_id UUID PRIMARY KEY,
            tagline TEXT NOT NULL,
            tagline_html TEXT,
            citation_raw TEXT NOT NULL,
            citation_html TEXT,
            cite TEXT,
            author_surname TEXT,
            author_year TEXT,
            author_credentials TEXT,
            publication_title TEXT,
            publisher TEXT,
            url TEXT,
            credits TEXT,
            body_text TEXT NOT NULL,
            body_html TEXT,
            highlighted_text TEXT,
            source_filename TEXT NOT NULL,
            paragraph_start INTEGER,
            paragraph_end INTEGER,
            word_count INTEGER,
            highlight_count INTEGER,
            created_at TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS duplicates (
            id SERIAL PRIMARY KEY,
            new_tagline TEXT,
            existing_tagline TEXT,
            author TEXT,
            year TEXT,
            similarity_score FLOAT,
            text_similarity_score FLOAT,
            duplicate_type TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """

        with psycopg2.connect(**self.db_config) as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
            conn.commit()

    def save_to_database(self) -> None:
        """Save parsed cards to PostgreSQL database"""
        if not self.db_config or not self.parsed_cards:
            return

        insert_card_sql = """
        INSERT INTO debate_cards (
            card_id, tagline, tagline_html, citation_raw, citation_html, cite, author_surname,
            author_year, author_credentials, publication_title, publisher,
            url, credits, body_text, body_html, highlighted_text,
            source_filename, paragraph_start, paragraph_end, word_count,
            highlight_count, created_at
        ) VALUES (
            %(card_id)s, %(tagline)s, %(tagline_html)s, %(citation_raw)s, %(citation_html)s, %(cite)s,
            %(author_surname)s, %(author_year)s, %(author_credentials)s,
            %(publication_title)s, %(publisher)s, %(url)s, %(credits)s,
            %(body_text)s, %(body_html)s, %(highlighted_text)s,
            %(source_filename)s, %(paragraph_start)s, %(paragraph_end)s,
            %(word_count)s, %(highlight_count)s, %(created_at)s
        );
        """

        insert_duplicate_sql = """
        INSERT INTO duplicates (
            new_tagline, existing_tagline, author, year, similarity_score, 
            text_similarity_score, duplicate_type
        ) VALUES (%(new_tagline)s, %(existing_tagline)s, %(author)s, %(year)s, 
                  %(similarity)s, %(text_similarity)s, %(duplicate_type)s);
        """

        with psycopg2.connect(**self.db_config) as conn:
            with conn.cursor() as cur:
                try:
                    # Bulk insert cards using execute_values
                    if self.parsed_cards:
                        from psycopg2.extras import execute_values

                        card_values = [
                            (
                                card.card_id,
                                card.tagline,
                                card.tagline_html,
                                card.citation_raw,
                                card.citation_html,
                                card.cite,
                                card.author_surname,
                                card.author_year,
                                card.author_credentials,
                                card.publication_title,
                                card.publisher,
                                card.url,
                                card.credits,
                                card.body_text,
                                card.body_html,
                                card.highlighted_text,
                                card.source_filename,
                                card.paragraph_start,
                                card.paragraph_end,
                                card.word_count,
                                card.highlight_count,
                                card.created_at,
                            )
                            for card in self.parsed_cards
                        ]

                        bulk_insert_card_sql = """
                        INSERT INTO debate_cards (
                            card_id, tagline, tagline_html, citation_raw, citation_html, cite, author_surname,
                            author_year, author_credentials, publication_title, publisher,
                            url, credits, body_text, body_html, highlighted_text,
                            source_filename, paragraph_start, paragraph_end, word_count,
                            highlight_count, created_at
                        ) VALUES %s
                        """

                        execute_values(
                            cur, bulk_insert_card_sql, card_values, page_size=1000
                        )

                    # Bulk insert duplicates
                    if self.near_duplicates:
                        duplicate_values = [
                            (
                                dup["new_tagline"],
                                dup["existing_tagline"],
                                dup["author"],
                                dup["year"],
                                dup["similarity"],
                                dup.get("text_similarity", 0.0),
                                dup["duplicate_type"],
                            )
                            for dup in self.near_duplicates
                        ]

                        bulk_insert_dup_sql = """
                        INSERT INTO duplicates (
                            new_tagline, existing_tagline, author, year, similarity_score,
                            text_similarity_score, duplicate_type
                        ) VALUES %s
                        """

                        execute_values(
                            cur, bulk_insert_dup_sql, duplicate_values, page_size=1000
                        )

                    conn.commit()
                    print(
                        f"Successfully saved {len(self.parsed_cards)} cards to database (bulk insert)"
                    )
                    print(f"Found {len(self.near_duplicates)} duplicates")

                except Exception as e:
                    conn.rollback()
                    print(
                        f"Bulk insert failed ({e}), falling back to individual inserts..."
                    )

                    # Fallback to individual inserts
                    success_count = 0
                    for card in self.parsed_cards:
                        try:
                            cur.execute(insert_card_sql, asdict(card))
                            success_count += 1
                        except Exception as e:
                            print(f"Error inserting card {card.card_id}: {e}")

                    for dup in self.near_duplicates:
                        try:
                            dup_data = {
                                "new_tagline": dup["new_tagline"],
                                "existing_tagline": dup["existing_tagline"],
                                "author": dup["author"],
                                "year": dup["year"],
                                "similarity": dup["similarity"],
                                "text_similarity": dup.get("text_similarity", 0.0),
                                "duplicate_type": dup["duplicate_type"],
                            }
                            cur.execute(insert_duplicate_sql, dup_data)
                        except Exception as e:
                            print(f"Error inserting duplicate record: {e}")

                    conn.commit()
                    print(
                        f"Successfully saved {success_count} cards to database (individual inserts)"
                    )
                    print(f"Found {len(self.near_duplicates)} duplicates")

    def export_to_csv(self, output_path: str) -> None:
        """Export parsed cards to CSV for backup/analysis"""
        if not self.parsed_cards:
            return

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Headers
            headers = list(asdict(self.parsed_cards[0]).keys())
            writer.writerow(headers)

            # Data
            for card in self.parsed_cards:
                row = list(asdict(card).values())
                writer.writerow(row)

        print(f"Exported {len(self.parsed_cards)} cards to {output_path}")


def main():
    arg_parser = argparse.ArgumentParser(
        description="Debate Card Parser - Extracts debate cards from .docx files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    arg_parser.add_argument("input_file", help="Input file (.zip or .docx)")

    # Database configuration arguments
    db_group = arg_parser.add_argument_group("database options")
    db_group.add_argument(
        "--db-host", default="localhost", help="PostgreSQL host (default: localhost)"
    )
    db_group.add_argument(
        "--db-name",
        default="debate_cards",
        help="Database name (default: debate_cards)",
    )
    db_group.add_argument(
        "--db-user", default="postgres", help="Database user (default: postgres)"
    )
    db_group.add_argument("--db-password", default="", help="Database password")

    # Debug options
    debug_group = arg_parser.add_argument_group("debug options")
    debug_group.add_argument(
        "--debug-duplicates",
        action="store_true",
        help="Enable detailed duplicate detection debugging",
    )
    debug_group.add_argument(
        "--skip-duplicates",
        action="store_true",
        help="Disable duplicate checking entirely (faster parsing)",
    )

    args = arg_parser.parse_args()

    input_path = args.input_file

    # Setup database configuration if any db arguments were provided
    db_config = None
    if any(
        [
            args.db_host != "localhost",
            args.db_name != "debate_cards",
            args.db_user != "postgres",
            args.db_password != "",
        ]
    ):
        db_config = {
            "host": args.db_host,
            "database": args.db_name,
            "user": args.db_user,
            "password": args.db_password,
        }

    # Initialize parser
    parser = DebateCardParser(
        db_config,
        debug_duplicates=args.debug_duplicates,
        skip_duplicates=args.skip_duplicates,
    )

    if db_config:
        try:
            parser.setup_database()
            print("Database connection established and tables created")
        except Exception as e:
            print(f"Database setup error: {e}")
            db_config = None  # Fallback to file output only

    print(f"Starting to parse {input_path}...")

    if input_path.lower().endswith(".zip"):
        parser.parse_zip_archive(input_path, use_multiprocessing=True)
    elif input_path.lower().endswith(".docx"):
        parser.parse_single_docx(input_path)
    else:
        print(f"Error: Unsupported file type. Please provide a .zip or .docx file.")
        sys.exit(1)

    print(f"\nParsing complete!")
    print(f"Successfully parsed: {len(parser.parsed_cards)} cards")
    print(f"Malformed cards: {len(parser.malformed_cards)}")

    if args.skip_duplicates:
        print("Duplicate checking was disabled")
    else:
        print(f"Duplicates found: {len(parser.near_duplicates)}")

    # Generate output files
    base_path = Path(input_path).stem

    if parser.malformed_cards:
        malformed_path = f"{base_path}_malformed_cards.docx"
        parser.save_malformed_cards_to_docx(malformed_path)

    csv_path = f"{base_path}_parsed_cards.csv"
    parser.export_to_csv(csv_path)

    if db_config:
        try:
            parser.save_to_database()
        except Exception as e:
            print(f"Database save error: {e}")
            print("Data saved to CSV instead")

    print(f"\nOutput files created:")
    print(f"  - Parsed cards: {csv_path}")
    if parser.malformed_cards:
        print(f"  - Malformed cards: {base_path}_malformed_cards.docx")
    if db_config:
        print(f"  - Database: {db_config['database']} (if connection successful)")


if __name__ == "__main__":
    main()
