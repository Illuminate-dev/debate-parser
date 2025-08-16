import hashlib
import multiprocessing
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import docx
from alive_progress import alive_bar
from datasketch import MinHash

from .citation_parser import CitationParser
from .html_formatter import HTMLFormatter
from .models import DebateCard


class DebateCardParser:
    def __init__(
        self,
        debug_duplicates: bool = False,
        skip_duplicates: bool = False,
    ):
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

        self.html_formatter = HTMLFormatter()
        self.citation_parser = CitationParser()

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
            if not self.citation_parser.is_valid_citation(citation_para):
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
                    if not self._is_duplicate(
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

    def _create_card_from_paragraphs(
        self, doc, tagline_index: int, filename: str, paragraph_count: int
    ) -> Optional[DebateCard]:
        """Create a DebateCard from document paragraphs starting at tagline_index"""
        tagline_para = doc.paragraphs[tagline_index]
        citation_para = doc.paragraphs[tagline_index + 1]
        citation_index = tagline_index + 1

        # Extract tagline and citation
        tagline = tagline_para.text
        tagline_html = self.html_formatter.extract_formatting_as_html(tagline_para)
        citation_raw = citation_para.text
        citation_html, cite_bolded = self.html_formatter.extract_formatting_and_bolded_text(
            citation_para, is_citation_paragraph=True
        )
        citation_parts = self.citation_parser.parse_citation(citation_raw, cite_bolded)

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
            body_html_parts.append(self.html_formatter.extract_formatting_as_html(para))

            # Extract highlighted text if present
            highlighted = self.html_formatter.extract_highlighted_text(para)
            if highlighted:
                highlighted_parts.append(highlighted)

            end_para = j

        return body_paras, body_html_parts, highlighted_parts, end_para

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
        malformed_card = {
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

        self.malformed_cards.append(malformed_card)

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

    def _is_duplicate(
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
        if not self.citation_parser.is_valid_citation(citation_para):
            return None

        # Extract tagline
        tagline_para = doc.paragraphs[i]
        tagline = tagline_para.text
        tagline_html = self.html_formatter.extract_formatting_as_html(tagline_para)

        # Extract citation
        citation_raw = citation_para.text
        citation_html, cite_bolded = self.html_formatter.extract_formatting_and_bolded_text(
            citation_para, is_citation_paragraph=True
        )
        citation_parts = self.citation_parser.parse_citation(citation_raw, cite_bolded)

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
            body_html_parts.append(self.html_formatter.extract_formatting_as_html(para))

            highlighted = self.html_formatter.extract_highlighted_text(para)
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
