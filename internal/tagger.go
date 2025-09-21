package internal

import (
	"fmt"
	"strings"

	"github.com/sirupsen/logrus"
)

type Tagger struct {
	storage CardStorage
}

func NewTagger(storage CardStorage) *Tagger {
	return &Tagger{
		storage: storage,
	}
}

// TagFromDocx parses cards from a single DOCX file and tags them
func (t *Tagger) TagFromDocx(filepath string, tags []string) error {
	// Parse cards from the file
	parser := NewParser(t.storage)
	err := parser.ParseDocx(filepath)
	if err != nil {
		return fmt.Errorf("failed to parse docx: %w", err)
	}

	// Get the newly parsed cards
	newCards := parser.GetCards()
	
	return t.tagCards(newCards, tags)
}

// TagFromZip parses cards from a ZIP archive and tags them
func (t *Tagger) TagFromZip(filepath string, tags []string) error {
	// Parse cards from the zip file
	parser := NewParser(t.storage)
	err := parser.ParseZip(filepath)
	if err != nil {
		return fmt.Errorf("failed to parse zip: %w", err)
	}

	// Get the newly parsed cards
	newCards := parser.GetCards()
	
	return t.tagCards(newCards, tags)
}

// tagCards processes a slice of cards and adds tags to them
func (t *Tagger) tagCards(newCards []Card, tags []string) error {
	logrus.WithFields(logrus.Fields{
		"new_cards_count": len(newCards),
		"tags":            tags,
	}).Info("Processing cards for tagging")

	// Load existing cards from storage for matching
	existingCards, err := t.storage.Load()
	if err != nil {
		logrus.WithError(err).Warn("Failed to load existing cards, will treat all as new")
		existingCards = []Card{}
	}

	cardsToUpdate := make([]Card, 0)
	cardsToAdd := make([]Card, 0)

	for _, newCard := range newCards {
		// Try to find matching existing card
		matchingCard, found := t.findMatchingCard(newCard, existingCards)
		
		if found {
			// Card exists, add tags to it
			logrus.WithFields(logrus.Fields{
				"card_id":  matchingCard.CardID,
				"tagline":  FirstN(matchingCard.Tagline, 50),
			}).Debug("Found matching existing card")
			
			// Merge tags (avoid duplicates)
			existingTags := matchingCard.Tags
			for _, tag := range tags {
				if !contains(existingTags, tag) {
					existingTags = append(existingTags, tag)
				}
			}
			matchingCard.Tags = existingTags
			cardsToUpdate = append(cardsToUpdate, matchingCard)
		} else {
			// Card doesn't exist, add it with tags
			logrus.WithFields(logrus.Fields{
				"tagline": FirstN(newCard.Tagline, 50),
			}).Debug("Adding new card with tags")
			
			newCard.Tags = append(newCard.Tags, tags...)
			cardsToAdd = append(cardsToAdd, newCard)
		}
	}

	logrus.WithFields(logrus.Fields{
		"cards_to_update": len(cardsToUpdate),
		"cards_to_add":    len(cardsToAdd),
	}).Info("Saving tagged cards")

	// Update existing cards with new tags
	if len(cardsToUpdate) > 0 {
		err = t.storage.UpdateTags(cardsToUpdate)
		if err != nil {
			return fmt.Errorf("failed to update card tags: %w", err)
		}
	}

	// Save new cards with tags
	if len(cardsToAdd) > 0 {
		err = t.storage.Save(cardsToAdd)
		if err != nil {
			return fmt.Errorf("failed to save new tagged cards: %w", err)
		}
	}

	logrus.WithField("total_tagged", len(cardsToUpdate)+len(cardsToAdd)).Info("Tagging completed successfully")
	return nil
}

// findMatchingCard looks for a card in existing cards that matches the new card
func (t *Tagger) findMatchingCard(newCard Card, existingCards []Card) (Card, bool) {
	for _, existing := range existingCards {
		if t.cardsMatch(newCard, existing) {
			return existing, true
		}
	}
	return Card{}, false
}

// cardsMatch determines if two cards are the same card
func (t *Tagger) cardsMatch(card1, card2 Card) bool {
	// Use the same matching logic as duplicate detection
	// Compare tagline, author surname, author year, and first part of body
	return strings.TrimSpace(card1.Tagline) == strings.TrimSpace(card2.Tagline) &&
		strings.TrimSpace(card1.AuthorSurname) == strings.TrimSpace(card2.AuthorSurname) &&
		strings.TrimSpace(card1.AuthorYear) == strings.TrimSpace(card2.AuthorYear) &&
		FirstN(strings.TrimSpace(card1.Body), 200) == FirstN(strings.TrimSpace(card2.Body), 200)
}

// Helper function to check if a slice contains a string
func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}