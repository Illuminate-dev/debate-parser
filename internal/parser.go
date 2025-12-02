// Package internal provides utilities for reading docx files into a manageable format
package internal

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/fumiama/go-docx"
	"github.com/schollz/progressbar/v3"
	"github.com/sirupsen/logrus"
)

type Parser struct {
	Cards   []Card
	Storage CardStorage
}

func NewParser(storage CardStorage) *Parser {
	return &Parser{
		Cards:   make([]Card, 0),
		Storage: storage,
	}
}

// headerContext tracks the current header hierarchy as we parse the document
type headerContext struct {
	h1, h2, h3 string
}

// buildGlobalPath constructs the complete hierarchical path for a card
func (h *headerContext) buildGlobalPath(sourceFile string) string {
	path := sourceFile
	if h.h1 != "" {
		path += "/" + h.h1
	}
	if h.h2 != "" {
		path += "/" + h.h2
	}
	if h.h3 != "" {
		path += "/" + h.h3
	}
	return path
}

// updateHeader updates the header context based on the heading level
func (h *headerContext) updateHeader(level int, text string) {
	switch level {
	case 1:
		h.h1 = text
		h.h2 = ""
		h.h3 = ""
	case 2:
		h.h2 = text
		h.h3 = ""
	case 3:
		h.h3 = text
	}
}

// isHeading checks if a paragraph is a heading and returns the level (1-3), or 0 if not a heading
func isHeading(para *docx.Paragraph) int {
	if checkStyle(para, "Heading1") {
		return 1
	}
	if checkStyle(para, "Heading2") {
		return 2
	}
	if checkStyle(para, "Heading3") {
		return 3
	}
	return 0
}

func (p *Parser) parseDocx(doc *docx.Docx, sourceFile string) {
	totalItems := len(doc.Document.Body.Items)
	logrus.WithFields(logrus.Fields{
		"file":       sourceFile,
		"paragraphs": totalItems,
	}).Debug("Starting document parsing")

	i := 0
	cardsFound := 0
	malformedCount := 0
	panicCount := 0
	headers := headerContext{}

	for i < totalItems {
		// Recover from panics in malformed documents
		func() {
			defer func() {
				if r := recover(); r != nil {
					logrus.WithFields(logrus.Fields{
						"file":      sourceFile,
						"paragraph": i,
						"panic":     r,
					}).Warn("Panic recovered while parsing paragraph")
					panicCount++
					i++ // Skip this paragraph and continue
				}
			}()

			// Check if current item is a heading and update context
			if para, ok := doc.Document.Body.Items[i].(*docx.Paragraph); ok {
				if level := isHeading(para); level > 0 {
					headers.updateHeader(level, para.String())
					logrus.WithFields(logrus.Fields{
						"level": level,
						"text":  para.String(),
					}).Debug("Header detected")
					i++
					return
				}
			}

			globalPath := headers.buildGlobalPath(sourceFile)
			card, endIdx, err := ParseCard(doc.Document.Body.Items[i:], globalPath)
			switch err {
			case nil:
				card.SourceFilename = filepath.Base(sourceFile)
				card.ParagraphStart = i
				card.ParagraphEnd = i + endIdx - 1
				p.Cards = append(p.Cards, card)
				cardsFound++
				i += endIdx
				logrus.WithFields(logrus.Fields{
					"card_id":     card.CardID,
					"tagline":     card.Tagline[:min(50, len(card.Tagline))],
					"global_path": card.GlobalPath,
				}).Debug("Parsed card")
			case ErrNotCitation:
				logrus.WithFields(logrus.Fields{
					"paragraph": i,
					"error":     err.Error(),
				}).Debug("Malformed citation encountered")
				malformedCount++
				i += 1
			default:
				logrus.WithFields(logrus.Fields{
					"paragraph": i,
					"error":     err.Error(),
				}).Debug("Skipping paragraph")
				i += 1
			}
		}()
	}

	logrus.WithFields(logrus.Fields{
		"file":            sourceFile,
		"cards_found":     cardsFound,
		"malformed":       malformedCount,
		"panics_recovered": panicCount,
		"total_processed": totalItems,
	}).Debug("Document parsing completed")
}

func (p *Parser) ParseDocx(path string) error {
	if filepath.Ext(path) != ".docx" {
		return fmt.Errorf("path is not a .docx file")
	}

	readFile, err := os.Open(path)
	if err != nil {
		return err
	}
	defer readFile.Close()

	fileinfo, err := readFile.Stat()
	if err != nil {
		return err
	}
	size := fileinfo.Size()
	doc, err := docx.Parse(readFile, size)
	if err != nil {
		return err
	}

	p.parseDocx(doc, filepath.Base(path))
	return nil
}

func (p *Parser) ParseZip(path string) error {
	if filepath.Ext(path) != ".zip" {
		return fmt.Errorf("path is not a .zip file")
	}

	reader, err := zip.OpenReader(path)
	if err != nil {
		logrus.WithError(err).WithField("path", path).Error("Failed to open ZIP file")
		return err
	}
	defer reader.Close()

	// Count DOCX files first
	docxFiles := make([]*zip.File, 0)
	for _, file := range reader.File {
		if filepath.Ext(file.Name) == ".docx" {
			docxFiles = append(docxFiles, file)
		}
	}

	logrus.WithFields(logrus.Fields{
		"zip_file":    filepath.Base(path),
		"total_files": len(reader.File),
		"docx_files":  len(docxFiles),
	}).Info("Processing ZIP archive")

	bar := progressbar.NewOptions(len(docxFiles),
		progressbar.OptionSetDescription("Processing DOCX files"),
		progressbar.OptionShowCount(),
		progressbar.OptionSetWidth(50),
	)

	successCount := 0
	errorCount := 0

	for i, file := range docxFiles {
		bar.Set(i)

		logrus.WithField("file", file.Name).Debug("Processing DOCX file")

		r, err := file.Open()
		if err != nil {
			logrus.WithError(err).WithField("file", file.Name).Warn("Failed to open DOCX file")
			errorCount++
			continue
		}

		content, err := io.ReadAll(r)
		r.Close()
		if err != nil {
			logrus.WithError(err).WithField("file", file.Name).Warn("Failed to read DOCX file")
			errorCount++
			continue
		}

		readerAt := bytes.NewReader(content)

		doc, err := docx.Parse(readerAt, int64(len(content)))
		if err != nil {
			logrus.WithError(err).WithField("file", file.Name).Warn("Failed to parse DOCX file")
			errorCount++
			continue
		}

		// Build source file path as: zipfile.zip/internal/path.docx
		// Clean the file name to ensure it's relative (strip any absolute path prefixes)
		cleanFileName := filepath.Clean(file.Name)
		if filepath.IsAbs(cleanFileName) {
			cleanFileName = filepath.Base(cleanFileName)
		}
		sourceFile := filepath.Base(path) + "/" + cleanFileName
		p.parseDocx(doc, sourceFile)
		successCount++
	}

	bar.Finish()

	logrus.WithFields(logrus.Fields{
		"zip_file":    filepath.Base(path),
		"processed":   successCount,
		"errors":      errorCount,
		"total_cards": len(p.Cards),
	}).Info("ZIP archive processing completed")

	return nil
}

func (p *Parser) Save() error {
	return p.Storage.Save(p.Cards)
}

func (p *Parser) GetCards() []Card {
	return p.Cards
}

func (p *Parser) RemoveDuplicates() error {
	return p.Storage.PruneDupes()
}

func (p *Parser) reportDocx(path string) error {
	if filepath.Ext(path) != ".docx" {
		return fmt.Errorf("path is not a .docx file")
	}

	readFile, err := os.Open(path)
	if err != nil {
		return err
	}
	defer readFile.Close()

	fileinfo, err := readFile.Stat()
	if err != nil {
		return err
	}
	size := fileinfo.Size()
	doc, err := docx.Parse(readFile, size)
	if err != nil {
		return err
	}

	i := 0
	for i < len(doc.Document.Body.Items) {
		switch v := doc.Document.Body.Items[i].(type) {
		case *docx.Paragraph:
			fmt.Printf("%v: %v\n", getStyle(v), v.String()[:min(len(v.String()), 50)])
		}
		i++
	}

	return nil
}

func getStyle(para *docx.Paragraph) string {
	if para.Properties != nil && para.Properties.Style != nil {
		return para.Properties.Style.Val
	}
	return ""
}
