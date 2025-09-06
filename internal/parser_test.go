package internal

import (
	"fmt"
	"testing"

	"debate-parser/testutil"
)

func TestParser_parseDocx(t *testing.T) {
	// Test should not error
	storage := NewCSVStorage(testutil.TestDataPath("out.csv"))
	p := NewParser(storage)
	err := p.ParseDocx(testutil.TestDataPath("1AC TEK.docx"))

	// fmt.Printf("Cards: %v", p.Cards)
	fmt.Printf("Card 1: %v\n", p.Cards[0].HighlightedText)

	if err != nil {
		t.Error("parsing errored")
	}

	expectLen := 16
	if len(p.Cards) != expectLen {
		t.Errorf("incorrect number of cards: expected %v, receieved %v", expectLen, len(p.Cards))
	}

	p.Save()
	if err != nil {
		t.Error("saving errored")
	}
}

func TestParser_reportDocx(t *testing.T) {
	return
	storage := NewCSVStorage(testutil.TestDataPath("out.csv"))
	p := NewParser(storage)
	err := p.reportDocx(testutil.TestDataPath("1AC TEK.docx"))
	if err != nil {
		t.Error("reporting errored")
	}
}
