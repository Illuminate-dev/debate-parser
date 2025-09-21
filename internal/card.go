package internal

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/fumiama/go-docx"
	"github.com/google/uuid"
)

type Card struct {
	Tagline     string
	TaglineHTML string

	// Soure detail components
	Citation         string
	CitationHTML     string
	Cite             string
	AuthorSurname    string
	AuthorYear       string
	AuthorCreds      string
	PublicationTitle string
	Publisher        string
	URL              string
	Credits          string

	Body            string
	BodyHTML        string
	HighlightedText string
	HighlightCount  int
	WordCount       int

	CardID         string
	CreatedAt      string
	SourceFilename string
	ParagraphStart int
	ParagraphEnd   int
	Tags           []string // New field for tags
}

var (
	ErrNotHeading4  error = fmt.Errorf("no heading4 found")
	ErrNotParagraph error = fmt.Errorf("no paragraph found")
	ErrNotCitation  error = fmt.Errorf("no citation found")
	ErrNoBody       error = fmt.Errorf("no body found")
)

var (
	URLRegex         *regexp.Regexp = regexp.MustCompile(`https?://[^\s,)]+`)
	TitleRegex       *regexp.Regexp = regexp.MustCompile(`["""]([^"""]+)["""]`)
	AuthorCredsRegex *regexp.Regexp = regexp.MustCompile(`\[([^\]]+)\]`)
	CreditsRegex     *regexp.Regexp = regexp.MustCompile(`(?://\s*(.+)|(?:^|\s)-\s*([A-Z][A-Za-z\s]*))$`)
)

// ParseCard takes a slice of items, then attempts to parse a card starting that that position,
// returning an error on failure. Also returns an index at which the card stopped parsing
func ParseCard(items []any) (Card, int, error) {
	var card Card
	var endIdx int

	// Check if the current paragraph is a header4
	para := items[0]
	switch v := para.(type) {
	case *docx.Paragraph:
		if isTagline(v) {
			card.Tagline = v.String()
			card.TaglineHTML = paraToHTML(v)
			endIdx++
		} else {
			return card, 0, ErrNotHeading4
		}
	default:
		return card, 0, ErrNotParagraph
	}

	// We have a header, now attempt to parse the author citation part
	para = items[endIdx]
	switch v := para.(type) {
	case *docx.Paragraph:
		if isCitation(v) {
			card.Citation = v.String()
			card.CitationHTML = paraToHTML(v)
			card.Cite = extractBoldedText(v)
			endIdx++
		} else {
			return card, 0, ErrNotCitation
		}
	default:
		return card, 0, ErrNotParagraph
	}

	// With our header and citation, now we can get the rest of the body
	// the best strat is to greedy gobble everything until there we encounter another header

	var bodyString strings.Builder
	var bodyHTMLString strings.Builder
	var bodyHighlightedText []string

loop:
	for endIdx < len(items) {
		switch v := items[endIdx].(type) {
		case *docx.Paragraph:
			if isBody(v) {
				bodyString.WriteString(v.String())
				bodyString.WriteString("\n")
				bodyHTMLString.WriteString(paraToHTML(v))
				bodyHTMLString.WriteString("\n")
				bodyHighlightedText = append(bodyHighlightedText, extractHighlightedText(v))
				endIdx++
			} else {
				break loop
			}
		default:
			break loop
		}
	}
	card.Body = bodyString.String()
	card.BodyHTML = bodyHTMLString.String()
	card.HighlightedText = strings.Join(bodyHighlightedText, " ")
	card.HighlightCount = len(strings.Fields(card.HighlightedText))
	card.WordCount = len(strings.Fields(card.Body))

	if card.WordCount < 5 {
		return card, 0, ErrNoBody
	}

	// Now with this text we can further parse it to extract some details
	_ = card.parseCitationDetails()

	card.CardID = uuid.NewString()
	card.CreatedAt = time.Now().Format("2006-01-02 15:04:05.000000")

	return card, endIdx, nil
}

func (c *Card) parseCitationDetails() error {
	if c.Citation == "" {
		return fmt.Errorf("no citation found when parsing citation details")
	}

	if c.Cite != "" {
		parts := strings.Fields(c.Cite)
		if len(parts) > 0 {
			var authorParts []string
			for _, part := range parts {
				if strings.ContainsAny(part, "0123456789") {
					c.AuthorYear = part
				} else {
					authorParts = append(authorParts, part)
				}
			}
			c.AuthorSurname = strings.Join(authorParts, " ")
		}
	}

	c.URL = URLRegex.FindString(c.Citation)
	c.PublicationTitle = TitleRegex.FindString(c.Citation)
	c.AuthorCreds = AuthorCredsRegex.FindString(c.Citation)
	if matches := CreditsRegex.FindStringSubmatch(c.Credits); matches != nil {
		c.Credits = matches[0]
	} else {
		words := strings.Fields(c.Citation)
		if len(words) > 0 {
			c.Credits = strings.Fields(c.Citation)[len(words)-1]
		}
	}
	return nil
}

func isStyleNone(para *docx.Paragraph) bool {
	return para.Properties == nil || para.Properties.Style == nil || para.Properties.Style.Val == ""
}

func checkStyle(para *docx.Paragraph, val string) bool {
	// Check for nil pointers before accessing style properties
	return para.Properties != nil && para.Properties.Style != nil && para.Properties.Style.Val == val
}

func isTagline(para *docx.Paragraph) bool {
	return checkStyle(para, "Heading4")
}

func isCitation(para *docx.Paragraph) bool {
	return isStyleNone(para)
}

func isBody(para *docx.Paragraph) bool {
	return isStyleNone(para)
}
