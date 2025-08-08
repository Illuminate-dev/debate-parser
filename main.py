#!/usr/bin/env python3
"""
Debate Card Parser
"""

import argparse
import csv
import json
import re
import sys
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import docx
import psycopg2
from alive_progress import alive_bar


@dataclass
class DebateCard:
    card_id: str
    tagline: str
    tagline_html: str
    citation_raw: str
    citation_html: str
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
    def __init__(self, db_config: Optional[Dict] = None):
        self.db_config = db_config
        self.parsed_cards = []
        self.malformed_cards = []
        self.seen_cards = {}
        self.near_duplicates = []

    def extract_formatting_as_html(self, paragraph) -> str:
        """Convert a paragraph with rich formatting to HTML"""
        html_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            tags = []
            close_tags = []

            # Check for bold formatting
            is_bold = False

            if run.bold:
                is_bold = True

            if run.style and run.style.name:
                style_name = run.style.name.lower()
                if any(
                    keyword in style_name
                    for keyword in ["cite", "bold", "strong", "emphasis"]
                ):
                    is_bold = True

            if hasattr(run.font, "bold") and run.font.bold is True:
                is_bold = True

            if run._element.rPr is not None:
                # Check for <w:b/> element in the run properties
                try:
                    bold_elements = run._element.rPr.xpath(".//w:b")
                    if bold_elements:
                        is_bold = True
                except:
                    # Fallback if xpath fails
                    pass

            if is_bold:
                tags.append("<strong>")
                close_tags.insert(0, "</strong>")

            # Check for italic formatting
            is_italic = False

            if run.italic:
                is_italic = True

            if hasattr(run.font, "italic") and run.font.italic is True:
                is_italic = True

            if run._element.rPr is not None:
                try:
                    italic_elements = run._element.rPr.xpath(".//w:i")
                    if italic_elements:
                        is_italic = True
                except:
                    # Fallback if xpath fails
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

    def debug_run_formatting(self, paragraph) -> None:
        """Debug function to print formatting information for each run in a paragraph"""
        print(f"\nDebugging paragraph: {paragraph.text[:100]}...")

        for i, run in enumerate(paragraph.runs):
            print(f"  Run {i}: '{run.text}'")
            print(f"    Direct bold: {run.bold}")
            print(f"    Font bold: {getattr(run.font, 'bold', None)}")
            print(f"    Style: {run.style.name if run.style else 'None'}")

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
        citation_html = self.extract_formatting_as_html(citation_para)

        return citation_raw, citation_html

    def extract_highlighted_text(self, paragraph) -> str:
        """Extract only the highlighted text from a paragraph"""
        highlighted_parts = []

        for run in paragraph.runs:
            if run._element.rPr is not None and run._element.rPr.highlight_val:
                highlighted_parts.append(run.text)

        return " ".join(highlighted_parts)

    def parse_citation(self, citation_text: str) -> Dict[str, str]:
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

        author_match = re.match(
            r"^([A-Za-z]+(?:\s[A-Za-z]+)*)\s+(\d{2,4})", citation_text
        )
        if author_match:
            result["author_surname"] = author_match.group(1)
            result["author_year"] = author_match.group(2)

        url_pattern = r"https?://[^\s,)]+"
        url_match = re.search(url_pattern, citation_text)
        if url_match:
            result["url"] = url_match.group()

        title_pattern = r'["""]([^"""]+)["""]'
        title_match = re.search(title_pattern, citation_text)
        if title_match:
            result["publication_title"] = title_match.group(1)

        cred_pattern = r"\[([^\]]+)\]"
        cred_match = re.search(cred_pattern, citation_text)
        if cred_match:
            result["author_credentials"] = cred_match.group(1)

        credits_pattern = r"//\s*(.+)$"
        credits_match = re.search(credits_pattern, citation_text)
        if credits_match:
            result["credits"] = credits_match.group(1)

        return result

    def is_duplicate(
        self, tagline: str, author_surname: str, author_year: str, body_text: str
    ) -> bool:
        """Check if this card is a duplicate"""
        duplicate_key = (
            tagline.strip().lower(),
            author_surname.strip().lower(),
            author_year.strip(),
        )

        for existing_key, existing_body in self.seen_cards.items():
            existing_tagline, existing_author, existing_year = existing_key

            # Exact match on tagline, author, year
            if (
                existing_tagline == tagline.strip().lower()
                and existing_author == author_surname.strip().lower()
                and existing_year == author_year.strip()
            ):
                # Check text similarity for exact duplicates
                text_similarity = self._similarity_score(
                    body_text.lower(), existing_body.lower()
                )

                if text_similarity > 0.8:
                    # This is a true duplicate
                    self.near_duplicates.append(
                        {
                            "new_tagline": tagline,
                            "existing_tagline": existing_tagline,
                            "author": author_surname,
                            "year": author_year,
                            "similarity": text_similarity,
                            "duplicate_type": "exact",
                        }
                    )
                    return True

            # Check for near duplicates (similar tagline, same author+year)
            elif (
                existing_author == author_surname.strip().lower()
                and existing_year == author_year.strip()
                and self._similarity_score(tagline.lower(), existing_tagline) > 0.8
            ):
                text_similarity = self._similarity_score(
                    body_text.lower(), existing_body.lower()
                )

                self.near_duplicates.append(
                    {
                        "new_tagline": tagline,
                        "existing_tagline": existing_tagline,
                        "author": author_surname,
                        "year": author_year,
                        "similarity": self._similarity_score(
                            tagline.lower(), existing_tagline
                        ),
                        "text_similarity": text_similarity,
                        "duplicate_type": "near",
                    }
                )

        self.seen_cards[duplicate_key] = body_text.strip().lower()
        return False

    def _similarity_score(self, str1: str, str2: str) -> float:
        """Calculate similarity between two strings using simple Jaccard similarity"""
        words1 = set(str1.split())
        words2 = set(str2.split())

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union) if union else 0

    def parse_docx(self, file, filename: str) -> List[DebateCard]:
        """Parse a single .docx file and extract debate cards"""
        doc = docx.Document(file)
        cards = []

        for i in range(len(doc.paragraphs) - 1):
            # Look for Heading 4 taglines
            if doc.paragraphs[i].style.name != "Heading 4":
                continue

            # Check if next paragraph is citation (must be immediate next paragraph)
            if i + 1 >= len(doc.paragraphs):
                self.malformed_cards.append(
                    {
                        "filename": filename,
                        "paragraph_index": i,
                        "tagline": doc.paragraphs[i].text,
                        "issue": "No next paragraph found",
                        "context": [
                            p.text for p in doc.paragraphs[max(0, i - 1) : i + 2]
                        ],
                    }
                )
                continue

            citation_para = doc.paragraphs[i + 1]
            citation_index = i + 1

            if not (
                citation_para.style.name in ["Normal", "Normal (Web)"]
                and len(citation_para.text) >= 10
                and (
                    re.search(r"[A-Za-z]+\s+\d{2,4}", citation_para.text)  # Author Year
                    or re.search(
                        r"\[.*\]", citation_para.text
                    )  # Credentials in brackets
                    or "http" in citation_para.text.lower()
                    or ".org" in citation_para.text.lower()
                    or ".com" in citation_para.text.lower()
                    or ".edu" in citation_para.text.lower()
                    or any(
                        word in citation_para.text.lower()
                        for word in [
                            "professor",
                            "university",
                            "phd",
                            "dr.",
                            "institute",
                        ]
                    )
                )
            ):
                self.malformed_cards.append(
                    {
                        "filename": filename,
                        "paragraph_index": i,
                        "tagline": doc.paragraphs[i].text,
                        "issue": "No valid citation found",
                        "context": [
                            p.text for p in doc.paragraphs[max(0, i - 1) : i + 4]
                        ],
                    }
                )
                continue

            try:
                tagline_para = doc.paragraphs[i]
                tagline = tagline_para.text
                tagline_html = self.extract_formatting_as_html(tagline_para)

                citation_raw = citation_para.text
                citation_html = self.extract_formatting_as_html(citation_para)
                citation_parts = self.parse_citation(citation_raw)

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

                if self.is_duplicate(
                    tagline,
                    citation_parts["author_surname"],
                    citation_parts["author_year"],
                    body_text,
                ):
                    continue

                body_html = "<p>" + "</p>\n<p>".join(body_html_parts) + "</p>"
                highlighted_text = " ".join(highlighted_parts)

                card = DebateCard(
                    card_id=str(uuid.uuid4()),
                    tagline=tagline,
                    tagline_html=tagline_html,
                    citation_raw=citation_raw,
                    citation_html=citation_html,
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

                cards.append(card)

            except Exception as e:
                # Handle parsing errors
                self.malformed_cards.append(
                    {
                        "filename": filename,
                        "paragraph_index": i,
                        "tagline": doc.paragraphs[i].text[:100],
                        "issue": f"Parsing error: {str(e)}",
                        "context": [
                            p.text[:100] for p in doc.paragraphs[max(0, i - 1) : i + 3]
                        ],
                    }
                )
                continue

        return cards

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

    def parse_zip_archive(self, zip_path: str) -> None:
        """Parse all .docx files in a zip archive"""
        with zipfile.ZipFile(zip_path, "r") as zf:
            docx_files = [
                f
                for f in zf.infolist()
                if f.filename.endswith(".docx")
                and not f.filename.startswith("__MACOSX/")
                and not f.filename.split("/")[-1].startswith("._")
            ]

            print(f"Found {len(docx_files)} .docx files in archive")

            with alive_bar(len(docx_files), title="Parsing documents") as bar:
                for file_info in docx_files:
                    try:
                        with zf.open(file_info.filename) as docx_file:
                            cards = self.parse_docx(docx_file, file_info.filename)
                            self.parsed_cards.extend(cards)
                    except Exception as e:
                        print(f"Error processing {file_info.filename}: {e}")

                    bar()

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
            card_id, tagline, tagline_html, citation_raw, citation_html, author_surname,
            author_year, author_credentials, publication_title, publisher,
            url, credits, body_text, body_html, highlighted_text,
            source_filename, paragraph_start, paragraph_end, word_count,
            highlight_count, created_at
        ) VALUES (
            %(card_id)s, %(tagline)s, %(tagline_html)s, %(citation_raw)s, %(citation_html)s,
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
                # Insert cards
                for card in self.parsed_cards:
                    try:
                        cur.execute(insert_card_sql, asdict(card))
                    except Exception as e:
                        print(f"Error inserting card {card.card_id}: {e}")

                # Insert duplicates
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

        print(f"Successfully saved {len(self.parsed_cards)} cards to database")
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
    parser = DebateCardParser(db_config)

    if db_config:
        try:
            parser.setup_database()
            print("Database connection established and tables created")
        except Exception as e:
            print(f"Database setup error: {e}")
            db_config = None  # Fallback to file output only

    print(f"Starting to parse {input_path}...")

    if input_path.lower().endswith(".zip"):
        parser.parse_zip_archive(input_path)
    elif input_path.lower().endswith(".docx"):
        parser.parse_single_docx(input_path)
    else:
        print(f"Error: Unsupported file type. Please provide a .zip or .docx file.")
        sys.exit(1)

    print(f"\nParsing complete!")
    print(f"Successfully parsed: {len(parser.parsed_cards)} cards")
    print(f"Malformed cards: {len(parser.malformed_cards)}")
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
