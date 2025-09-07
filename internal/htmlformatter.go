package internal

import (
	"fmt"
	"html"
	"slices"
	"strings"

	"github.com/fumiama/go-docx"
)

type tagHTML struct {
	Name string
	Vals string
}

func paraToHTML(para *docx.Paragraph) string {
	var paraString strings.Builder
	tags := make([]tagHTML, 0)

	if checkStyle(para, "Heading4") {
		tags = append(tags, tagHTML{"h4", ""})
	} else {
		tags = append(tags, tagHTML{"p", ""})
	}

	for _, tag := range tags {
		paraString.WriteString(fmt.Sprintf("<%v %v>", tag.Name, tag.Vals))
	}

	for _, child := range para.Children {
		switch v := child.(type) {
		case *docx.Run:
			paraString.WriteString(runToHTML(v))
		case *docx.Hyperlink:
			paraString.WriteString(linkToHTML(v))
		default:
			fmt.Printf("Unknown type encountered (%T), skipping...\n", v)
		}
	}

	for _, tag := range tags {
		paraString.WriteString(fmt.Sprintf("</%v>", tag.Name))
	}

	return paraString.String()
}

func runToHTML(run *docx.Run) string {
	var runString strings.Builder
	tags := make([]tagHTML, 0)

	if props := run.RunProperties; props != nil {
		if isBold(props) {
			tags = append(tags, tagHTML{"strong", ""})
		}

		if props.Italic != nil {
			tags = append(tags, tagHTML{"em", ""})
		}

		if isUnderline(props) {
			tags = append(tags, tagHTML{"u", ""})
		}

		if props.Highlight != nil {
			colorHex := getHex(props.Highlight.Val)
			styleString := fmt.Sprintf("style=\"background-color: %v;\"", colorHex)
			tags = append(tags, tagHTML{"mark", styleString})
		}
	}

	for _, tag := range tags {
		runString.WriteString(fmt.Sprintf("<%v %v>", tag.Name, tag.Vals))
	}

	for _, child := range run.Children {
		switch v := child.(type) {
		case *docx.Text:
			runString.WriteString(html.EscapeString(v.Text))
		case *docx.BarterRabbet:
			runString.WriteString("<br>")
		case *docx.Drawing:
			// skip drawing
		case *docx.Tab:
			// skip tab
		default:
			fmt.Printf("Unknown type encountered (%T), skipping...\n", v)
		}
	}

	for _, tag := range slices.Backward(tags) {
		runString.WriteString(fmt.Sprintf("</%v>", tag.Name))
	}

	return runString.String()
}

func linkToHTML(link *docx.Hyperlink) string {
	var linkString strings.Builder

	for _, child := range link.Run.Children {
		switch v := child.(type) {
		case *docx.Text:
			linkString.WriteString(html.EscapeString(v.Text))
		case *docx.BarterRabbet:
			linkString.WriteString("<br>")
		default:
			fmt.Printf("Unknown type encountered (%T), skipping...\n", v)
		}
	}

	return linkString.String()
}

func getHex(colorName string) string {
	switch colorName {
	case "cyan":
		return "#00FFFF"
	case "green":
		return "#00FF00"
	case "yellow":
		return "#FFFF00"
	case "red":
		return "#FF0000"
	case "magenta":
		return "#FF00FF"
	case "blue":
		return "#0000FF"
	default:
		return "#00FFFF"
	}
}

func extractBoldedText(para *docx.Paragraph) string {
	var builder strings.Builder
	for _, child := range para.Children {
		switch v := child.(type) {
		case *docx.Run:
			if props := v.RunProperties; props != nil && isBold(props) {
				for _, child := range v.Children {
					switch v := child.(type) {
					case *docx.Text:
						builder.WriteString(v.Text)
					}
				}
			}
		}
	}
	return builder.String()
}

func extractHighlightedText(para *docx.Paragraph) string {
	var highlights []string
	for _, child := range para.Children {
		switch v := child.(type) {
		case *docx.Run:
			if props := v.RunProperties; props != nil && props.Highlight != nil {
				for _, child := range v.Children {
					switch v := child.(type) {
					case *docx.Text:
						highlights = append(highlights, strings.TrimSpace(v.Text))
					}
				}
			}
		}
	}
	return strings.Join(highlights, " ")
}

func isBold(props *docx.RunProperties) bool {
	if props.Bold != nil {
		return true
	}
	if props.RunStyle != nil {
		styleLower := strings.ToLower(props.RunStyle.Val)
		return strings.Contains(styleLower, "bold") || 
			   strings.Contains(styleLower, "emphasis") || 
			   strings.Contains(styleLower, "strong")
	}
	return false
}

func isUnderline(props *docx.RunProperties) bool {
	return props.Underline != nil || (props.RunStyle != nil && strings.Contains(strings.ToLower(props.RunStyle.Val), "underline"))
}
