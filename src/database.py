import logging
from dataclasses import asdict
from typing import List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from .models import DebateCard


class DatabaseHandler:
    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.connection = None
        self.logger = logging.getLogger(__name__)

    def connect(self) -> bool:
        """Establish database connection"""
        try:
            self.connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                cursor_factory=RealDictCursor,
            )
            self.logger.info(f"Connected to PostgreSQL database: {self.database}")
            return True
        except psycopg2.Error as e:
            self.logger.error(f"Failed to connect to database: {e}")
            return False

    def disconnect(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.logger.info("Database connection closed")

    def create_tables(self):
        """Create necessary database tables"""
        create_debate_cards_table = """
        CREATE TABLE IF NOT EXISTS debate_cards (
            id SERIAL PRIMARY KEY,
            card_id VARCHAR(255) UNIQUE NOT NULL,
            tagline TEXT,
            tagline_html TEXT,
            citation_raw TEXT,
            citation_html TEXT,
            cite TEXT,
            author_surname VARCHAR(255),
            author_year VARCHAR(50),
            author_credentials TEXT,
            publication_title TEXT,
            publisher TEXT,
            url TEXT,
            credits VARCHAR(255),
            body_text TEXT,
            body_html TEXT,
            highlighted_text TEXT,
            source_filename VARCHAR(255),
            paragraph_start INTEGER,
            paragraph_end INTEGER,
            word_count INTEGER,
            highlight_count INTEGER,
            created_at TIMESTAMP,
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """

        create_duplicates_table = """
        CREATE TABLE IF NOT EXISTS duplicates (
            id SERIAL PRIMARY KEY,
            original_card_id VARCHAR(255) REFERENCES debate_cards(card_id),
            duplicate_card_id VARCHAR(255) REFERENCES debate_cards(card_id),
            similarity_score FLOAT,
            detection_method VARCHAR(50),
            detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(create_debate_cards_table)
                cursor.execute(create_duplicates_table)
                self.connection.commit()
                self.logger.info("Database tables created successfully")
        except psycopg2.Error as e:
            self.logger.error(f"Failed to create tables: {e}")
            self.connection.rollback()
            raise

    def save_debate_card(self, card: DebateCard) -> bool:
        """Save a single debate card to the database"""
        insert_query = """
        INSERT INTO debate_cards (
            card_id, tagline, tagline_html, citation_raw, citation_html, cite,
            author_surname, author_year, author_credentials, publication_title,
            publisher, url, credits, body_text, body_html, highlighted_text,
            source_filename, paragraph_start, paragraph_end, word_count,
            highlight_count, created_at
        ) VALUES (
            %(card_id)s, %(tagline)s, %(tagline_html)s, %(citation_raw)s, %(citation_html)s, %(cite)s,
            %(author_surname)s, %(author_year)s, %(author_credentials)s, %(publication_title)s,
            %(publisher)s, %(url)s, %(credits)s, %(body_text)s, %(body_html)s, %(highlighted_text)s,
            %(source_filename)s, %(paragraph_start)s, %(paragraph_end)s, %(word_count)s,
            %(highlight_count)s, %(created_at)s
        ) ON CONFLICT (card_id) DO NOTHING;
        """

        try:
            with self.connection.cursor() as cursor:
                card_dict = asdict(card)
                # Truncate fields that exceed VARCHAR limits
                if len(card_dict.get("credits", "")) > 255:
                    card_dict["credits"] = card_dict["credits"][:255]
                    self.logger.warning(
                        f"Truncated credits field for card {card.card_id} from {len(card.credits)} to 255 characters"
                    )
                if len(card_dict.get("author_surname", "")) > 255:
                    card_dict["author_surname"] = card_dict["author_surname"][:255]
                    self.logger.warning(
                        f"Truncated author_surname field for card {card.card_id} from {len(card.author_surname)} to 255 characters"
                    )
                cursor.execute(insert_query, card_dict)
                self.connection.commit()
                return True
        except psycopg2.Error as e:
            self.logger.error(f"Failed to save debate card {card.card_id}: {e}")
            self.connection.rollback()
            return False

    def save_debate_cards_batch(self, cards: List[DebateCard]) -> int:
        """Save multiple debate cards in a batch operation"""
        if not cards:
            return 0

        insert_query = """
        INSERT INTO debate_cards (
            card_id, tagline, tagline_html, citation_raw, citation_html, cite,
            author_surname, author_year, author_credentials, publication_title,
            publisher, url, credits, body_text, body_html, highlighted_text,
            source_filename, paragraph_start, paragraph_end, word_count,
            highlight_count, created_at
        ) VALUES (
            %(card_id)s, %(tagline)s, %(tagline_html)s, %(citation_raw)s, %(citation_html)s, %(cite)s,
            %(author_surname)s, %(author_year)s, %(author_credentials)s, %(publication_title)s,
            %(publisher)s, %(url)s, %(credits)s, %(body_text)s, %(body_html)s, %(highlighted_text)s,
            %(source_filename)s, %(paragraph_start)s, %(paragraph_end)s, %(word_count)s,
            %(highlight_count)s, %(created_at)s
        ) ON CONFLICT (card_id) DO NOTHING;
        """

        saved_count = 0
        for card in cards:
            try:
                with self.connection.cursor() as cursor:
                    card_dict = asdict(card)
                    # Truncate fields that exceed VARCHAR limits
                    if len(card_dict.get("credits", "")) > 255:
                        card_dict["credits"] = card_dict["credits"][:255]
                        self.logger.warning(
                            f"Truncated credits field for card {card.card_id} from {len(card.credits)} to 255 characters"
                        )
                    if len(card_dict.get("author_surname", "")) > 255:
                        card_dict["author_surname"] = card_dict["author_surname"][:255]
                        self.logger.warning(
                            f"Truncated author_surname field for card {card.card_id} from {len(card.author_surname)} to 255 characters"
                        )
                    cursor.execute(insert_query, card_dict)
                    if cursor.rowcount > 0:
                        saved_count += 1
                    self.connection.commit()
            except psycopg2.Error as e:
                # Debug logging to identify which field is too long
                card_dict = asdict(card)
                long_fields = []
                for field_name, value in card_dict.items():
                    if isinstance(value, str) and len(value) > 255:
                        if field_name in [
                            "author_surname",
                            "credits",
                            "source_filename",
                        ]:
                            long_fields.append(f"{field_name}({len(value)})")

                if long_fields:
                    self.logger.warning(
                        f"Failed to save card {card.card_id}: {e}. Long fields: {', '.join(long_fields)}"
                    )
                else:
                    self.logger.warning(f"Failed to save card {card.card_id}: {e}")

                self.connection.rollback()
                continue

        self.logger.info(
            f"Successfully saved {saved_count}/{len(cards)} debate cards to database"
        )
        return saved_count

    def save_duplicate_record(
        self, original_id: str, duplicate_id: str, similarity: float, method: str
    ):
        """Record a duplicate detection result"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'duplicates' AND table_schema = 'public'
                """)
                columns = [row["column_name"] for row in cursor.fetchall()]

                if not columns:
                    self.logger.warning(
                        "Duplicates table does not exist, creating it..."
                    )
                    self.create_tables()
                    return self.save_duplicate_record(
                        original_id, duplicate_id, similarity, method
                    )

                expected_columns = [
                    "original_card_id",
                    "duplicate_card_id",
                    "similarity_score",
                    "detection_method",
                ]
                if not all(col in columns for col in expected_columns):
                    self.logger.warning(
                        f"Duplicates table schema mismatch. Found columns: {columns}"
                    )
                    self.logger.warning(
                        "Recreating duplicates table with correct schema..."
                    )
                    cursor.execute("DROP TABLE IF EXISTS duplicates")
                    self.connection.commit()
                    self.create_tables()
                    return self.save_duplicate_record(
                        original_id, duplicate_id, similarity, method
                    )
        except psycopg2.Error as e:
            self.logger.error(f"Failed to check duplicates table schema: {e}")
            return

        insert_query = """
        INSERT INTO duplicates (original_card_id, duplicate_card_id, similarity_score, detection_method)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT DO NOTHING;
        """

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    insert_query, (original_id, duplicate_id, similarity, method)
                )
                self.connection.commit()
        except psycopg2.Error as e:
            self.logger.error(f"Failed to save duplicate record: {e}")
            self.connection.rollback()

    def get_card_count(self) -> int:
        """Get total number of cards in database"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM debate_cards")
                result = cursor.fetchone()
                return result[0] if result else 0
        except psycopg2.Error as e:
            self.logger.error(f"Failed to get card count: {e}")
            return 0

    def get_all_cards(self) -> List[DebateCard]:
        """Load all existing cards from the database"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT card_id, tagline, tagline_html, citation_raw, citation_html, cite,
                           author_surname, author_year, author_credentials, publication_title,
                           publisher, url, credits, body_text, body_html, highlighted_text,
                           source_filename, paragraph_start, paragraph_end, word_count,
                           highlight_count, created_at
                    FROM debate_cards
                    ORDER BY inserted_at
                """)
                rows = cursor.fetchall()

                cards = []
                for row in rows:
                    card = DebateCard(
                        card_id=row["card_id"],
                        tagline=row["tagline"] or "",
                        tagline_html=row["tagline_html"] or "",
                        citation_raw=row["citation_raw"] or "",
                        citation_html=row["citation_html"] or "",
                        cite=row["cite"] or "",
                        author_surname=row["author_surname"] or "",
                        author_year=row["author_year"] or "",
                        author_credentials=row["author_credentials"] or "",
                        publication_title=row["publication_title"] or "",
                        publisher=row["publisher"] or "",
                        url=row["url"] or "",
                        credits=row["credits"] or "",
                        body_text=row["body_text"] or "",
                        body_html=row["body_html"] or "",
                        highlighted_text=row["highlighted_text"] or "",
                        source_filename=row["source_filename"] or "",
                        paragraph_start=row["paragraph_start"] or 0,
                        paragraph_end=row["paragraph_end"] or 0,
                        word_count=row["word_count"] or 0,
                        highlight_count=row["highlight_count"] or 0,
                        created_at=row["created_at"] or "",
                    )
                    cards.append(card)

                self.logger.info(f"Loaded {len(cards)} existing cards from database")
                return cards
        except psycopg2.Error as e:
            self.logger.error(f"Failed to load cards from database: {e}")
            return []

    def delete_cards_by_ids(self, card_ids: List[str]) -> int:
        """Delete cards from database by their IDs"""
        if not card_ids:
            return 0

        try:
            deleted_count = 0
            with self.connection.cursor() as cursor:
                delete_duplicates_query = """
                DELETE FROM duplicates 
                WHERE original_card_id = ANY(%s) OR duplicate_card_id = ANY(%s)
                """
                cursor.execute(delete_duplicates_query, (card_ids, card_ids))

                delete_cards_query = """
                DELETE FROM debate_cards 
                WHERE card_id = ANY(%s)
                """
                cursor.execute(delete_cards_query, (card_ids,))
                deleted_count = cursor.rowcount

                self.connection.commit()
                self.logger.info(
                    f"Successfully deleted {deleted_count} cards from database"
                )
                return deleted_count
        except psycopg2.Error as e:
            self.logger.error(f"Failed to delete cards from database: {e}")
            self.connection.rollback()
            return 0

    def get_duplicate_records(self) -> List[dict]:
        """Get all duplicate records from the duplicates table"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("""
                    SELECT original_card_id, duplicate_card_id, similarity_score, detection_method
                    FROM duplicates
                    ORDER BY similarity_score DESC
                """)
                rows = cursor.fetchall()

                duplicates = []
                for row in rows:
                    duplicates.append(
                        {
                            "original_card_id": row["original_card_id"],
                            "duplicate_card_id": row["duplicate_card_id"],
                            "similarity_score": row["similarity_score"],
                            "detection_method": row["detection_method"],
                        }
                    )

                return duplicates
        except psycopg2.Error as e:
            self.logger.error(f"Failed to get duplicate records: {e}")
            return []


def create_database_handler(
    host: str, port: int, database: str, user: str, password: str
) -> Optional[DatabaseHandler]:
    """Factory function to create and initialize database handler"""
    handler = DatabaseHandler(host, port, database, user, password)

    if handler.connect():
        try:
            handler.create_tables()
            return handler
        except Exception as e:
            logging.error(f"Failed to initialize database: {e}")
            handler.disconnect()
            return None
    else:
        return None
