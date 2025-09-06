package internal

import (
	"context"
	"encoding/csv"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/schollz/progressbar/v3"
	"github.com/sirupsen/logrus"
)

type CardStorage interface {
	Save(cards []Card) error
	PruneDupes() error
	Close() error
}

var CSVHeaders = []string{"CardID", "Tagline", "TaglineHTML", "Citation", "CitationHTML", "Cite", "AuthorSurname", "AuthorYear", "AuthorCreds", "PublicationTitle", "Publisher", "URL", "Credits", "Body", "BodyHTML", "HighlightCount", "HighlightedText", "WordCount", "CreatedAt", "SourceFilename"}

// sanitizeUTF8 removes or replaces invalid UTF-8 sequences and problematic Unicode characters
func sanitizeUTF8(s string) string {
	// Replace common problematic Unicode characters that can cause database issues
	s = strings.ReplaceAll(s, "\u201C", "\"") // left double quotation mark
	s = strings.ReplaceAll(s, "\u201D", "\"") // right double quotation mark
	s = strings.ReplaceAll(s, "\u2018", "'")  // left single quotation mark
	s = strings.ReplaceAll(s, "\u2019", "'")  // right single quotation mark
	s = strings.ReplaceAll(s, "\u2013", "-")  // en dash
	s = strings.ReplaceAll(s, "\u2014", "--") // em dash
	s = strings.ReplaceAll(s, "\u00AD", "")   // soft hyphen
	s = strings.ReplaceAll(s, "\u00A0", " ")  // non-breaking space
	s = strings.ReplaceAll(s, "\x00", "")     // null bytes
	s = strings.ReplaceAll(s, "\uFFFD", "")   // replacement characters
	s = strings.ReplaceAll(s, "\x92", "'")    // smart quote (Windows-1252)
	s = strings.ReplaceAll(s, "\x93", "\"")   // smart quote (Windows-1252)
	s = strings.ReplaceAll(s, "\x94", "\"")   // smart quote (Windows-1252)
	s = strings.ReplaceAll(s, "\x96", "-")    // en dash (Windows-1252)
	s = strings.ReplaceAll(s, "\x97", "--")   // em dash (Windows-1252)

	// Ensure the result is valid UTF-8
	if !utf8.ValidString(s) {
		var result strings.Builder
		for _, r := range s {
			if r != utf8.RuneError {
				result.WriteRune(r)
			}
		}
		return result.String()
	}

	return s
}

type CSVStorage struct {
	filepath string
}

func NewCSVStorage(filepath string) *CSVStorage {
	return &CSVStorage{
		filepath: filepath,
	}
}

func (c *CSVStorage) Save(cards []Card) error {
	// Read what's currently in the file
	cardsToWrite, err := c.Load()
	if err != nil {
		cardsToWrite = make([]Card, 0)
	}

	cardsToWrite = append(cardsToWrite, cards...)

	// Now just rewrite the entire file
	return c.overwrite(cardsToWrite)
}

func (c *CSVStorage) overwrite(cards []Card) error {
	file, err := os.Create(c.filepath)
	if err != nil {
		return err
	}

	writer := csv.NewWriter(file)

	if err = writer.Write(CSVHeaders); err != nil {
		return fmt.Errorf("failed to write CSV header: %w", err)
	}

	records := Map(cards, func(card Card) []string {
		return []string{
			card.CardID,
			card.Tagline,
			card.TaglineHTML,
			card.Citation,
			card.CitationHTML,
			card.Cite,
			card.AuthorSurname,
			card.AuthorYear,
			card.AuthorCreds,
			card.PublicationTitle,
			card.Publisher,
			card.URL,
			card.Credits,
			card.Body,
			card.BodyHTML,
			fmt.Sprintf("%d", card.HighlightCount),
			card.HighlightedText,
			fmt.Sprintf("%d", card.WordCount),
			card.CreatedAt,
			card.SourceFilename,
		}
	})

	err = writer.WriteAll(records)
	if err != nil {
		return fmt.Errorf("failed to save: %w", err)
	}

	return nil
}

func (c *CSVStorage) Load() ([]Card, error) {
	file, err := os.Open(c.filepath)
	if err != nil {
		return nil, err
	}

	reader := csv.NewReader(file)
	records, err := reader.ReadAll()
	if err != nil {
		return nil, fmt.Errorf("failed to read CSV: %w", err)
	}

	if len(records) == 0 {
		return []Card{}, nil
	}

	// Skip header row
	if len(records) > 0 {
		records = records[1:]
	}

	return c.parseRecords(records)
}

func (c *CSVStorage) PruneDupes() error {
	existingCards, err := c.Load()
	if err != nil {
		logrus.WithError(err).Error("Failed to load existing cards")
		return err
	}

	logrus.WithField("total_cards", len(existingCards)).Info("Starting duplicate removal from CSV")

	dedupedCards := make([]Card, 0, len(existingCards))
	minHashLSH := NewMinHashLSH(128, 16)

	bar := progressbar.NewOptions(len(existingCards),
		progressbar.OptionSetDescription("Removing duplicates"),
		progressbar.OptionShowCount(),
		progressbar.OptionSetWidth(50),
	)

	duplicatesFound := 0

	for i, card := range existingCards {
		bar.Set(i)

		key := fmt.Sprintf("%v %v %v %v", card.Tagline, card.AuthorSurname, card.AuthorYear, FirstN(card.Body, 200))
		potentialDupes := minHashLSH.CheckForDuplicates(key, ShingleSize)
		
		isDuplicate := false
		if len(potentialDupes) > 0 {
			for _, dupe := range potentialDupes {
				similarity := minHashLSH.EstimateJaccardSimilarityWithText(dupe, key, ShingleSize)
				if similarity > 0.85 {
					logrus.WithFields(logrus.Fields{
						"card_id":    card.CardID,
						"dupe_id":    dupe,
						"similarity": fmt.Sprintf("%.2f", similarity),
					}).Debug("Duplicate found")
					duplicatesFound++
					isDuplicate = true
					break
				}
			}
		}

		if !isDuplicate {
			dedupedCards = append(dedupedCards, card)
			minHashLSH.ComputeMinHash(card.CardID, key, ShingleSize)
		}
	}

	bar.Finish()

	logrus.WithFields(logrus.Fields{
		"original_count":  len(existingCards),
		"duplicates":      duplicatesFound,
		"remaining_count": len(dedupedCards),
	}).Info("Duplicate removal completed")

	return c.overwrite(dedupedCards)
}

// Close does nothing because of the way we handle files right now
func (c *CSVStorage) Close() error {
	return nil
}

// Helper function to parse CSV records into Card structs
func (c *CSVStorage) parseRecords(records [][]string) ([]Card, error) {
	var cards []Card

	for i, record := range records {
		card, err := c.parseRecord(record)
		if err != nil {
			return nil, fmt.Errorf("failed to parse record %d: %w", i+2, err) // +2 because of 0-index and header
		}
		cards = append(cards, card)
	}

	return cards, nil
}

// Helper function to parse a single CSV record into a Card
func (c *CSVStorage) parseRecord(record []string) (Card, error) {
	if len(record) != len(CSVHeaders) {
		return Card{}, fmt.Errorf("expected %d fields, got %d", len(CSVHeaders), len(record))
	}

	var card Card

	// Parse fields in the same order as CSVHeaders
	card.CardID = record[0]
	card.Tagline = record[1]
	card.TaglineHTML = record[2]
	card.Citation = record[3]
	card.CitationHTML = record[4]
	card.Cite = record[5]
	card.AuthorSurname = record[6]
	card.AuthorYear = record[7]
	card.AuthorCreds = record[8]
	card.PublicationTitle = record[9]
	card.Publisher = record[10]
	card.URL = record[11]
	card.Credits = record[12]
	card.Body = record[13]
	card.BodyHTML = record[14]

	// Parse highlight_count
	if record[15] != "" {
		highlightCount, err := strconv.Atoi(record[15])
		if err != nil {
			return Card{}, fmt.Errorf("invalid highlight_count: %w", err)
		}
		card.HighlightCount = highlightCount
	}

	card.HighlightedText = record[16]

	// Parse word_count
	if record[17] != "" {
		wordCount, err := strconv.Atoi(record[17])
		if err != nil {
			return Card{}, fmt.Errorf("invalid word_count: %w", err)
		}
		card.WordCount = wordCount
	}

	// Parse created_at - you might need to adjust this based on your date format
	if record[18] != "" {
		createdAt, err := time.Parse(time.RFC3339, record[18])
		if err != nil {
			// Try alternative date formats if RFC3339 fails
			createdAt, err = time.Parse("2006-01-02 15:04:05", record[18])
			if err != nil {
				return Card{}, fmt.Errorf("invalid created_at format: %w", err)
			}
		}
		card.CreatedAt = createdAt.String()
	}

	card.SourceFilename = record[19]

	return card, nil
}

type PostgresStorage struct {
	connection *pgx.Conn
}

func NewPostgresStorage(url string) (*PostgresStorage, error) {
	conn, err := pgx.Connect(context.Background(), url)
	if err != nil {
		return nil, err
	}

	createCardsTabelQuery := `
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
	`

	_, err = conn.Exec(context.Background(), createCardsTabelQuery)
	if err != nil {
		return nil, err
	}

	return &PostgresStorage{conn}, nil
}

func (p *PostgresStorage) scanCards(rows pgx.Rows) ([]Card, error) {
	var cards []Card

	for rows.Next() {
		var card Card
		err := p.scanCard(rows, &card)
		if err != nil {
			return nil, fmt.Errorf("failed to scan card: %w", err)
		}
		cards = append(cards, card)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating rows: %w", err)
	}

	return cards, nil
}

// Helper function to scan a single row into a Card
func (p *PostgresStorage) scanCard(scanner interface {
	Scan(dest ...any) error
}, card *Card,
) error {
	var createdAt time.Time
	err := scanner.Scan(
		&card.CardID,
		&card.Tagline,
		&card.TaglineHTML,
		&card.Citation,
		&card.CitationHTML,
		&card.Cite,
		&card.AuthorSurname,
		&card.AuthorYear,
		&card.AuthorCreds,
		&card.PublicationTitle,
		&card.Publisher,
		&card.URL,
		&card.Credits,
		&card.Body,
		&card.BodyHTML,
		&card.HighlightedText,
		&card.SourceFilename,
		&card.ParagraphStart,
		&card.ParagraphEnd,
		&card.WordCount,
		&card.HighlightCount,
		&createdAt,
	)
	if err != nil {
		return err
	}

	// Convert time.Time to string format matching the Card struct
	card.CreatedAt = createdAt.Format("2006-01-02 15:04:05.000000")
	return nil
}

func (p *PostgresStorage) Save(cards []Card) error {
	if len(cards) == 0 {
		logrus.Debug("No cards to save")
		return nil
	}

	logrus.WithField("card_count", len(cards)).Info("Saving cards to database")

	insertQuery := `INSERT INTO debate_cards (
            card_id, tagline, tagline_html, citation_raw, citation_html, cite,
            author_surname, author_year, author_credentials, publication_title,
            publisher, url, credits, body_text, body_html, highlighted_text,
            source_filename, paragraph_start, paragraph_end, word_count,
            highlight_count, created_at
        ) VALUES (
  					$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22
        ) ON CONFLICT (card_id) DO NOTHING;`

	batch := &pgx.Batch{}
	truncatedCount := 0

	for _, card := range cards {
		// Sanitize all text fields for UTF-8 validity
		card.Tagline = sanitizeUTF8(card.Tagline)
		card.TaglineHTML = sanitizeUTF8(card.TaglineHTML)
		card.Citation = sanitizeUTF8(card.Citation)
		card.CitationHTML = sanitizeUTF8(card.CitationHTML)
		card.Cite = sanitizeUTF8(card.Cite)
		card.AuthorSurname = sanitizeUTF8(card.AuthorSurname)
		card.AuthorYear = sanitizeUTF8(card.AuthorYear)
		card.AuthorCreds = sanitizeUTF8(card.AuthorCreds)
		card.PublicationTitle = sanitizeUTF8(card.PublicationTitle)
		card.Publisher = sanitizeUTF8(card.Publisher)
		card.URL = sanitizeUTF8(card.URL)
		card.Credits = sanitizeUTF8(card.Credits)
		card.Body = sanitizeUTF8(card.Body)
		card.BodyHTML = sanitizeUTF8(card.BodyHTML)
		card.HighlightedText = sanitizeUTF8(card.HighlightedText)
		card.SourceFilename = sanitizeUTF8(card.SourceFilename)

		// Truncate fields that have length limits
		if len(card.Credits) > 255 {
			originalLen := len(card.Credits)
			card.Credits = card.Credits[:255]
			logrus.WithFields(logrus.Fields{
				"card_id":      card.CardID,
				"field":        "credits",
				"original_len": originalLen,
				"truncated_to": 255,
			}).Debug("Truncated field")
			truncatedCount++
		}
		if len(card.AuthorSurname) > 255 {
			originalLen := len(card.AuthorSurname)
			card.AuthorSurname = card.AuthorSurname[:255]
			logrus.WithFields(logrus.Fields{
				"card_id":      card.CardID,
				"field":        "author_surname",
				"original_len": originalLen,
				"truncated_to": 255,
			}).Debug("Truncated field")
			truncatedCount++
		}
		if len(card.AuthorYear) > 50 {
			originalLen := len(card.AuthorYear)
			card.AuthorYear = card.AuthorYear[:50]
			logrus.WithFields(logrus.Fields{
				"card_id":      card.CardID,
				"field":        "author_year",
				"original_len": originalLen,
				"truncated_to": 50,
			}).Debug("Truncated field")
			truncatedCount++
		}

		batch.Queue(insertQuery,
			card.CardID,
			card.Tagline,
			card.TaglineHTML,
			card.Citation,
			card.CitationHTML,
			card.Cite,
			card.AuthorSurname,
			card.AuthorYear,
			card.AuthorCreds,
			card.PublicationTitle,
			card.Publisher,
			card.URL,
			card.Credits,
			card.Body,
			card.BodyHTML,
			card.HighlightedText,
			card.SourceFilename,
			card.ParagraphStart,
			card.ParagraphEnd,
			card.WordCount,
			card.HighlightCount,
			card.CreatedAt,
		)
	}

	results := p.connection.SendBatch(context.Background(), batch)
	defer results.Close()

	savedCount := 0
	errorCount := 0

	for i := range cards {
		res, err := results.Exec()
		if err != nil {
			logrus.WithError(err).WithField("card_index", i).Warn("Error saving card")
			errorCount++
			continue
		}

		if res.RowsAffected() > 0 {
			savedCount++
		}
	}

	logrus.WithFields(logrus.Fields{
		"saved":     savedCount,
		"total":     len(cards),
		"errors":    errorCount,
		"truncated": truncatedCount,
	}).Info("Database save completed")

	return nil
}

func (p *PostgresStorage) Load() ([]Card, error) {
	query := `SELECT 
		card_id, tagline, tagline_html, citation_raw, citation_html, cite,
		author_surname, author_year, author_credentials, publication_title,
		publisher, url, credits, body_text, body_html, highlighted_text,
		source_filename, paragraph_start, paragraph_end, word_count,
		highlight_count, created_at
	FROM debate_cards
	ORDER BY created_at DESC`

	rows, err := p.connection.Query(context.Background(), query)
	if err != nil {
		return nil, fmt.Errorf("failed to query cards: %w", err)
	}
	defer rows.Close()

	return p.scanCards(rows)
}

func (p *PostgresStorage) deleteIDs(ids []string) error {
	if len(ids) == 0 {
		logrus.Debug("No cards to delete")
		return nil
	}

	logrus.WithField("card_count", len(ids)).Info("Deleting duplicate cards from database")

	deleteQuery := `DELETE FROM debate_cards WHERE card_id = $1`
	batch := &pgx.Batch{}

	for _, id := range ids {
		batch.Queue(deleteQuery, id)
	}

	results := p.connection.SendBatch(context.Background(), batch)
	defer results.Close()

	deletedCount := 0
	errorCount := 0

	for i := range ids {
		res, err := results.Exec()
		if err != nil {
			logrus.WithError(err).WithFields(logrus.Fields{
				"card_index": i,
				"card_id":    ids[i],
			}).Warn("Error deleting card")
			errorCount++
			continue
		}

		if res.RowsAffected() > 0 {
			deletedCount++
		}
	}

	logrus.WithFields(logrus.Fields{
		"deleted": deletedCount,
		"total":   len(ids),
		"errors":  errorCount,
	}).Info("Card deletion completed")

	return nil
}

func (p *PostgresStorage) PruneDupes() error {
	// Load current cards
	cards, err := p.Load()
	if err != nil {
		logrus.WithError(err).Error("Failed to load cards from database")
		return err
	}

	logrus.WithField("total_cards", len(cards)).Info("Starting duplicate removal from database")

	cardIDsToDelete := make([]string, 0, len(cards))
	minHashLSH := NewMinHashLSH(128, 16)

	bar := progressbar.NewOptions(len(cards),
		progressbar.OptionSetDescription("Removing duplicates"),
		progressbar.OptionShowCount(),
		progressbar.OptionSetWidth(50),
	)

	for i, card := range cards {
		bar.Set(i)

		key := fmt.Sprintf("%v %v %v %v", card.Tagline, card.AuthorSurname, card.AuthorYear, FirstN(card.Body, 200))
		potentialDupes := minHashLSH.CheckForDuplicates(key, ShingleSize)
		
		isDuplicate := false
		if len(potentialDupes) > 0 {
			for _, dupe := range potentialDupes {
				similarity := minHashLSH.EstimateJaccardSimilarityWithText(dupe, key, ShingleSize)
				if similarity > 0.85 {
					logrus.WithFields(logrus.Fields{
						"card_id":    card.CardID,
						"dupe_id":    dupe,
						"similarity": fmt.Sprintf("%.2f", similarity),
					}).Debug("Duplicate found")
					cardIDsToDelete = append(cardIDsToDelete, card.CardID)
					isDuplicate = true
					break
				}
			}
		}

		if !isDuplicate {
			minHashLSH.ComputeMinHash(card.CardID, key, ShingleSize)
		}
	}

	bar.Finish()

	logrus.WithFields(logrus.Fields{
		"total_cards":       len(cards),
		"duplicates_found":  len(cardIDsToDelete),
		"remaining_cards":   len(cards) - len(cardIDsToDelete),
	}).Info("Duplicate detection completed")

	return p.deleteIDs(cardIDsToDelete)
}

func (p *PostgresStorage) Close() error {
	return p.connection.Close(context.Background())
}
