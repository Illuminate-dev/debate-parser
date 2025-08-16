class HTMLFormatter:
    def __init__(self):
        self._xpath_cache = {}

    def _check_xpath_cached(self, element, query):
        if query not in self._xpath_cache:
            try:
                self._xpath_cache[query] = element.xpath(query)
            except:
                self._xpath_cache[query] = []
        return self._xpath_cache[query]

    def extract_formatting_as_html(
        self, paragraph, is_citation_paragraph: bool = False
    ) -> str:
        html_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            tags = []
            close_tags = []

            is_bold = (
                run.bold
                or (hasattr(run.font, "bold") and run.font.bold is True)
                or (
                    run.style
                    and run.style.name
                    and any(
                        keyword in run.style.name.lower()
                        for keyword in ["bold", "strong", "emphasis"]
                    )
                )
            )

            if not is_bold and run._element.rPr is not None:
                try:
                    bold_elements = self._check_xpath_cached(run._element.rPr, ".//w:b")
                    if bold_elements:
                        is_bold = True
                except:
                    pass

            # Use <cite> tags for bold text in citation paragraphs, <strong> elsewhere
            if is_bold:
                if is_citation_paragraph:
                    tags.append("<cite>")
                    close_tags.insert(0, "</cite>")
                else:
                    tags.append("<strong>")
                    close_tags.insert(0, "</strong>")

            is_italic = run.italic or (
                hasattr(run.font, "italic") and run.font.italic is True
            )

            # Only check XML if other methods failed
            if not is_italic and run._element.rPr is not None:
                try:
                    italic_elements = self._check_xpath_cached(
                        run._element.rPr, ".//w:i"
                    )
                    if italic_elements:
                        is_italic = True
                except:
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

    def extract_formatting_and_bolded_text(
        self, paragraph, is_citation_paragraph: bool = False
    ) -> tuple[str, str]:
        """Convert a paragraph with rich formatting to HTML and extract bolded text"""
        html_parts = []
        bolded_parts = []

        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            tags = []
            close_tags = []

            # Check for bold formatting
            is_bold = (
                run.bold
                or (hasattr(run.font, "bold") and run.font.bold is True)
                or (
                    run.style
                    and run.style.name
                    and any(
                        keyword in run.style.name.lower()
                        for keyword in ["bold", "strong", "emphasis"]
                    )
                )
            )

            if not is_bold and run._element.rPr is not None:
                try:
                    bold_elements = self._check_xpath_cached(run._element.rPr, ".//w:b")
                    if bold_elements:
                        is_bold = True
                except:
                    pass

            if is_bold:
                bolded_parts.append(text)
                if is_citation_paragraph:
                    tags.append("<cite>")
                    close_tags.insert(0, "</cite>")
                else:
                    tags.append("<strong>")
                    close_tags.insert(0, "</strong>")

            # Check for italic formatting
            is_italic = run.italic or (
                hasattr(run.font, "italic") and run.font.italic is True
            )

            if not is_italic and run._element.rPr is not None:
                try:
                    italic_elements = self._check_xpath_cached(
                        run._element.rPr, ".//w:i"
                    )
                    if italic_elements:
                        is_italic = True
                except:
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

            if run.font.color and run.font.color.rgb:
                color_hex = str(run.font.color.rgb)
                tags.append(f'<span style="color: #{color_hex};">')
                close_tags.insert(0, "</span>")

            formatted_text = "".join(tags) + text + "".join(close_tags)
            html_parts.append(formatted_text)

        return "".join(html_parts), " ".join(bolded_parts)

    def debug_run_formatting(self, paragraph) -> None:
        """Debug function to print formatting information for each run in a paragraph"""
        print(f"\nDebugging paragraph: {paragraph.text[:100]}...")

        for i, run in enumerate(paragraph.runs):
            print(f"  Run {i}: '{run.text}'")
            print(f"    Direct bold: {run.bold}")
            print(f"    Font bold: {getattr(run.font, 'bold', None)}")
            print(f"    Style: {run.style.name if run.style else 'None'}")
            if run.style:
                print(f"    Style ID: {getattr(run.style, 'style_id', 'None')}")
                print(f"    Style type: {getattr(run.style, 'type', 'None')}")
                if hasattr(run.style, "_element") and run.style._element is not None:
                    print(f"    Style element: {run.style._element.tag}")

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
    ) -> tuple[str, str]:
        """Extract citation with enhanced formatting detection"""

        # self.debug_run_formatting(citation_para)

        citation_raw = citation_para.text
        citation_html = self.extract_formatting_as_html(
            citation_para, is_citation_paragraph=True
        )

        return citation_raw, citation_html

    def extract_highlighted_text(self, paragraph) -> str:
        """Extract only the highlighted text from a paragraph"""
        highlighted_parts = []

        for run in paragraph.runs:
            if run._element.rPr is not None and run._element.rPr.highlight_val:
                highlighted_parts.append(run.text)

        return " ".join(highlighted_parts)
