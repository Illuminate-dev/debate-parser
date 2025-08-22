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
from .duplicate_detector import DuplicateDetector, FastDuplicateDetector
from .html_formatter import HTMLFormatter
from .models import DebateCard


class SkipFileException(Exception):
    """Exception raised when file is skipped in testing mode"""

    pass


class DebateCardParser:
    def __init__(
        self,
        debug_duplicates: bool = False,
        skip_duplicates: bool = False,
        testing_mode: bool = False,
        database_handler=None,
    ):
        self.parsed_cards = []
        self.malformed_cards = []
        self.debug_duplicates = debug_duplicates
        self.skip_duplicates = skip_duplicates
        self.testing_mode = testing_mode
        self.database_handler = database_handler

        self.duplicate_detector = DuplicateDetector()
        self.is_worker_process = False  # Track if this is a worker process

        self.num_perm = 64
        self.similarity_threshold = 0.75

        self.html_formatter = HTMLFormatter()
        self.citation_parser = CitationParser()

    def _save_cards_to_database(self, cards: List[DebateCard]) -> None:
        """Save cards to database"""
        if self.database_handler and cards:
            try:
                saved_count = self.database_handler.save_debate_cards_batch(cards)
                if saved_count > 0:
                    print(f"Saved {saved_count} cards to database")
            except Exception as e:
                print(f"Warning: Failed to save cards to database: {e}")

    def parse_docx(self, file, filename: str) -> List[DebateCard]:
        """Parse a single .docx file and extract debate cards"""
        doc = docx.Document(file)
        cards = []

        paragraph_count = len(doc.paragraphs)

        for i in range(paragraph_count - 1):
            current_para = doc.paragraphs[i]
            if current_para.style.name != "Heading 4":
                continue

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
                    card_info = {
                        "tagline": card.tagline,
                        "author_surname": card.author_surname,
                        "author_year": card.author_year,
                        "body_text": (
                            card.body_text[:200] + "..."
                            if len(card.body_text) > 200
                            else card.body_text
                        ),
                        "source_filename": filename,
                        "card_id": card.card_id,
                    }

                    if not self._is_duplicate(
                        card.tagline,
                        card.author_surname,
                        card.author_year,
                        card.body_text,
                        card_info,
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
        citation_html, cite_bolded = (
            self.html_formatter.extract_formatting_and_bolded_text(
                citation_para, is_citation_paragraph=True
            )
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
                for p in doc.paragraphs[max(0, context_index - 1) : context_index + 4]
            ],
        }

        self.malformed_cards.append(malformed_card)
        if self.testing_mode:
            self._handle_testing_mode_pause(malformed_card, filename)

    def _save_single_malformed_card(self, card: dict, output_path: str) -> None:
        """Save a single malformed card to a docx file"""
        doc = docx.Document()
        doc.add_heading("Malformed Card - Testing Mode", 0)

        doc.add_heading(f"Issue: {card['issue']}", level=1)
        doc.add_paragraph(f"File: {card['filename']}")
        doc.add_paragraph(f"Paragraph Index: {card['paragraph_index']}")
        doc.add_paragraph(f"Tagline: {card['tagline']}")

        doc.add_heading("Context:", level=2)
        for j, context_para in enumerate(card["context"]):
            p = doc.add_paragraph(f"Para {j}: {context_para}")
            if j == 1:  # Highlight the problematic paragraph
                p.runs[0].bold = True

        doc.save(output_path)

    def _is_duplicate(
        self,
        tagline: str,
        author_surname: str,
        author_year: str,
        body_text: str,
        card_info: Optional[dict] = None,
    ) -> bool:
        """Check if this card is a duplicate using hash-based detection and fuzzy hash approximate detection"""

        if self.skip_duplicates:
            return False

        content_key = f"{tagline} {author_surname} {author_year} {body_text}"

        if card_info is None:
            card_info = {
                "tagline": tagline,
                "author_surname": author_surname,
                "author_year": author_year,
                "body_text": (
                    body_text[:200] + "..." if len(body_text) > 200 else body_text
                ),
            }

        if self.duplicate_detector.check(content_key, card_info):
            return True

        content_id = f"{author_surname}_{author_year}_{len(self.parsed_cards)}"
        self.duplicate_detector.add(content_key, content_id)

        return False

    @property
    def near_duplicates(self):
        """Property to access detected duplicates for compatibility with main.py"""
        return self.duplicate_detector.detected_duplicates

    def save_duplicates_to_docx(self, output_path: str) -> None:
        """Save detected duplicate cards to a separate .docx file for review"""
        duplicates = self.duplicate_detector.detected_duplicates
        if not duplicates:
            return

        doc = docx.Document()
        doc.add_heading("Duplicate Cards Detected by MinHashLSH", 0)

        doc.add_paragraph(
            f"Found {len(duplicates)} duplicate cards with similarity threshold ≥ 75%"
        )
        doc.add_paragraph("")

        for i, dup_info in enumerate(duplicates):
            doc.add_heading(f"Duplicate Card {i + 1}", level=1)

            card = dup_info["duplicate_card"]

            # Card details
            doc.add_paragraph(f"Source File: {card.get('source_filename', 'Unknown')}")
            doc.add_paragraph(f"Card ID: {card.get('card_id', 'N/A')}")
            doc.add_paragraph(f"Similarity Score: {dup_info['similarity_score']:.2%}")
            doc.add_paragraph(
                f"Detection Method: {dup_info.get('detection_method', 'fuzzy_minhashlsh')}"
            )
            if "original_content_id" in dup_info:
                doc.add_paragraph(
                    f"Original Content ID: {dup_info['original_content_id']}"
                )
            if "original_content_hash" in dup_info:
                doc.add_paragraph(
                    f"Content Hash: {dup_info['original_content_hash'][:16]}..."
                )

            # Card content
            doc.add_heading("Tagline:", level=2)
            doc.add_paragraph(card.get("tagline", "N/A"))

            doc.add_heading("Author & Year:", level=2)
            author_info = f"{card.get('author_surname', 'Unknown')} ({card.get('author_year', 'Unknown')})"
            doc.add_paragraph(author_info)

            doc.add_heading("Body Text (Preview):", level=2)
            doc.add_paragraph(card.get("body_text", "N/A"))

            doc.add_paragraph("")  # Add spacing between entries

        doc.save(output_path)
        print(f"Saved {len(duplicates)} duplicate cards to {output_path}")

    def post_process_fuzzy_duplicates(self) -> None:
        """Post-process all parsed cards for fuzzy duplicate detection using MinHashLSH"""
        if not self.parsed_cards:
            return

        print(f"Post-processing {len(self.parsed_cards)} cards for fuzzy duplicates...")

        # Create fresh MinHashLSH detector for fuzzy matching
        fuzzy_detector = DuplicateDetector()
        fuzzy_duplicates = []
        unique_cards = []

        for card in self.parsed_cards:
            content_key = f"{card.tagline} {card.author_surname} {card.author_year} {card.body_text}"
            card_info = {
                "tagline": card.tagline,
                "author_surname": card.author_surname,
                "author_year": card.author_year,
                "body_text": (
                    card.body_text[:200] + "..."
                    if len(card.body_text) > 200
                    else card.body_text
                ),
                "source_filename": card.source_filename,
                "card_id": card.card_id,
            }

            if fuzzy_detector.check(content_key, card_info):
                # This is a fuzzy duplicate
                fuzzy_duplicates.extend(
                    fuzzy_detector.detected_duplicates[-1:]
                )  # Get the last added duplicate
            else:
                # Not a duplicate, keep it and add to detector
                unique_cards.append(card)
                fuzzy_detector.add(content_key, card.card_id)

        # Update parsed cards to only include unique ones
        removed_count = len(self.parsed_cards) - len(unique_cards)
        self.parsed_cards = unique_cards

        # Combine with exact duplicates from workers if they exist
        all_duplicates = fuzzy_duplicates
        if hasattr(self, "_worker_exact_duplicates"):
            all_duplicates.extend(self._worker_exact_duplicates)

        # Update the duplicate detector's detected duplicates for output
        self.duplicate_detector.detected_duplicates = all_duplicates

        print(
            f"Fuzzy duplicate detection removed {removed_count} additional duplicates"
        )
        if hasattr(self, "_worker_exact_duplicates"):
            print(
                f"Total duplicates detected: {len(all_duplicates)} (exact: {len(self._worker_exact_duplicates)}, fuzzy: {len(fuzzy_duplicates)})"
            )

    def parse_single_docx(self, docx_path: str) -> None:
        """Parse a single .docx file"""
        filename = Path(docx_path).name
        print(f"Parsing single document: {filename}")

        try:
            with open(docx_path, "rb") as docx_file:
                cards = self.parse_docx(docx_file, filename)
                self.parsed_cards.extend(cards)
                self._save_cards_to_database(cards)
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

            if self.testing_mode:
                print(
                    "Testing mode enabled - using single-threaded processing for interactive experience"
                )

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

                self._save_cards_to_database(self.parsed_cards)
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
                                DebateCardParser._parse_docx_data,
                                file_data,
                                filename,
                                self.debug_duplicates,
                            ): filename
                            for file_data, filename in temp_files
                        }

                        worker_exact_duplicates = []
                        for future in as_completed(future_to_file):
                            filename = future_to_file[future]
                            try:
                                cards, malformed, exact_duplicates = future.result()
                                self.parsed_cards.extend(cards)
                                self.malformed_cards.extend(malformed)
                                worker_exact_duplicates.extend(exact_duplicates)
                            except Exception as e:
                                print(f"Error processing {filename}: {e}")
                            bar()

                # Store exact duplicates found by workers
                self._worker_exact_duplicates = worker_exact_duplicates

                # Post-process for fuzzy duplicates across all workers
                if not self.skip_duplicates:
                    self.post_process_fuzzy_duplicates()

                # Save all unique cards to database after duplicate removal
                self._save_cards_to_database(self.parsed_cards)

    @staticmethod
    def _parse_docx_data(
        file_data: bytes, filename: str, debug_duplicates: bool = False
    ) -> Tuple[List[DebateCard], List[Dict], List[Dict]]:
        """Parse docx data"""
        with BytesIO(file_data) as file_obj:
            try:
                doc = docx.Document(file_obj)
                cards = []
                malformed_cards = []

                temp_parser = DebateCardParser(
                    debug_duplicates=debug_duplicates,
                    skip_duplicates=False,
                    testing_mode=False,
                    database_handler=None,
                )
                temp_parser.duplicate_detector = FastDuplicateDetector()
                temp_parser.is_worker_process = True

                for i in range(len(doc.paragraphs) - 1):
                    if doc.paragraphs[i].style.name != "Heading 4":
                        continue

                    try:
                        card = temp_parser._parse_card_at_index(doc, i, filename)
                        if card:
                            # Check for exact duplicates using fast detector
                            content_key = f"{card.tagline} {card.author_surname} {card.author_year} {card.body_text}"
                            card_info = {
                                "tagline": card.tagline,
                                "author_surname": card.author_surname,
                                "author_year": card.author_year,
                                "body_text": (
                                    card.body_text[:200] + "..."
                                    if len(card.body_text) > 200
                                    else card.body_text
                                ),
                                "source_filename": filename,
                                "card_id": card.card_id,
                            }

                            if not temp_parser.duplicate_detector.check(
                                content_key, card_info
                            ):
                                cards.append(card)
                                temp_parser.duplicate_detector.add(
                                    content_key, card.card_id
                                )
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

                # Include exact duplicates found by the worker
                exact_duplicates = temp_parser.duplicate_detector.detected_duplicates
                return cards, malformed_cards, exact_duplicates
            except Exception as e:
                return (
                    [],
                    [
                        {
                            "filename": filename,
                            "paragraph_index": -1,
                            "tagline": "Document error",
                            "issue": f"Document parsing error: {str(e)}",
                            "context": [],
                        }
                    ],
                    [],
                )

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
        citation_html, cite_bolded = (
            self.html_formatter.extract_formatting_and_bolded_text(
                citation_para, is_citation_paragraph=True
            )
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
