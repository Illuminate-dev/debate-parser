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
from src.database import create_database_handler


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

    arg_parser.add_argument("input_file", nargs="?", help="Input file (.zip or .docx)")

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
    debug_group.add_argument(
        "--testing-mode",
        action="store_true",
        help="Pause when malformed cards are encountered and save them immediately for review",
    )

    db_group = arg_parser.add_argument_group("database options")
    db_group.add_argument(
        "--db-host",
        default="localhost",
        help="PostgreSQL database host (default: localhost)",
    )
    db_group.add_argument(
        "--db-port",
        type=int,
        default=5432,
        help="PostgreSQL database port (default: 5432)",
    )
    db_group.add_argument(
        "--db-name",
        help="PostgreSQL database name",
    )
    db_group.add_argument(
        "--db-user",
        default="postgres",
        help="PostgreSQL database username (default: postgres)",
    )
    db_group.add_argument(
        "--db-password",
        help="PostgreSQL database password",
    )
    db_group.add_argument(
        "--clear-duplicates",
        action="store_true",
        help="Clear duplicate cards from the database using duplicate detection",
    )

    args = arg_parser.parse_args()

    if args.clear_duplicates:
        if not (args.db_host and args.db_name and args.db_user):
            print(
                "Error: Database connection parameters required for --clear-duplicates"
            )
            print("Required: --db-host, --db-name, --db-user")
            sys.exit(1)

        password = args.db_password or ""
        database_handler = create_database_handler(
            host=args.db_host,
            port=args.db_port,
            database=args.db_name,
            user=args.db_user,
            password=password,
        )

        if not database_handler:
            print("Failed to connect to database")
            sys.exit(1)

        print(f"Connected to database: {args.db_name}")

        from src.document_parser import DebateCardParser

        parser = DebateCardParser(database_handler=database_handler)
        cleared_count = parser.clear_database_duplicates()

        print(f"Cleared {cleared_count} duplicate cards from database")
        database_handler.disconnect()
        return

    if not args.input_file:
        print("Error: input_file is required when not using --clear-duplicates")
        arg_parser.print_help()
        sys.exit(1)

    input_path = args.input_file

    # Initialize database handler if database options are provided
    database_handler = None
    if args.db_host and args.db_name and args.db_user:
        password = args.db_password or ""
        database_handler = create_database_handler(
            host=args.db_host,
            port=args.db_port,
            database=args.db_name,
            user=args.db_user,
            password=password,
        )
        if database_handler:
            print(f"Connected to database: {args.db_name}")
        else:
            print("Failed to connect to database - continuing without database storage")

    # Initialize parser
    parser = DebateCardParser(
        debug_duplicates=args.debug_duplicates,
        skip_duplicates=args.skip_duplicates,
        testing_mode=args.testing_mode,
        database_handler=database_handler,
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
        print(f"Duplicates found: {len(parser.duplicate_detector.detected_duplicates)}")

    # Generate output files
    base_path = Path(input_path).stem

    if parser.malformed_cards:
        malformed_path = f"{base_path}_malformed_cards.docx"
        parser.save_malformed_cards_to_docx(malformed_path)

    # Save duplicate cards
    if not args.skip_duplicates and parser.duplicate_detector.detected_duplicates:
        duplicates_path = f"{base_path}_duplicate_cards.docx"
        parser.save_duplicates_to_docx(duplicates_path)

    # Export to CSV only if not using database
    if not database_handler:
        csv_path = f"{base_path}_parsed_cards.csv"
        export_to_csv(parser.parsed_cards, csv_path)

    print(f"\nOutput files created:")
    if not database_handler:
        print(f"  - Parsed cards: {csv_path}")
    else:
        print(f"  - Parsed cards saved to database: {args.db_name}")
    if parser.malformed_cards:
        print(f"  - Malformed cards: {base_path}_malformed_cards.docx")
    if not args.skip_duplicates and parser.duplicate_detector.detected_duplicates:
        print(f"  - Duplicate cards: {base_path}_duplicate_cards.docx")

    # Clean up database connection
    if database_handler:
        database_handler.disconnect()


if __name__ == "__main__":
    main()
