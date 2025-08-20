#!/usr/bin/env python3
"""
Debate Card Parser
"""

import argparse
import csv
import sys
from dataclasses import asdict
from pathlib import Path

from src import DebateCard, DebateCardParser


def export_to_csv(cards: list, output_path: str) -> None:
    """Export parsed cards to CSV for backup/analysis"""
    if not cards:
        return

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Headers
        headers = list(asdict(cards[0]).keys())
        writer.writerow(headers)

        # Data
        for card in cards:
            row = list(asdict(card).values())
            writer.writerow(row)

    print(f"Exported {len(cards)} cards to {output_path}")


def main():
    arg_parser = argparse.ArgumentParser(
        description="Debate Card Parser - Extracts debate cards from .docx files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    arg_parser.add_argument("input_file", help="Input file (.zip or .docx)")

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

    # Initialize parser
    parser = DebateCardParser(
        debug_duplicates=args.debug_duplicates,
        skip_duplicates=args.skip_duplicates,
    )

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

    # Save duplicate cards
    if not args.skip_duplicates and parser.near_duplicates:
        duplicates_path = f"{base_path}_duplicate_cards.docx"
        parser.save_duplicates_to_docx(duplicates_path)

    csv_path = f"{base_path}_parsed_cards.csv"
    export_to_csv(parser.parsed_cards, csv_path)

    print(f"\nOutput files created:")
    print(f"  - Parsed cards: {csv_path}")
    if parser.malformed_cards:
        print(f"  - Malformed cards: {base_path}_malformed_cards.docx")
    if not args.skip_duplicates and parser.near_duplicates:
        print(f"  - Duplicate cards: {base_path}_duplicate_cards.docx")


if __name__ == "__main__":
    main()
