# Debate Card Parser

A Go CLI tool for parsing Verbatim `.docx` debate documents into databases and deduplicating cards into said databases.

## Commands

- **`parse <docx|zip>`** - Extract cards from a Verbatim `.docx` file or a `.zip` archive containing the said files
- **`dedupe`** - Deduplicate parsed cards
- **`tag <docx> <tag1,tag2,...>`** - Tag a document's cards with given tags

## Output

Results can be written to:

- `--csv-path <path>` - a CSV file
- `--db-url <url>` - a Postgres database

## Logging

- `-v` - verbose/debug output
- `-q` - suppress most output

## Parsing

The parser reads a `.docx` file and uses the styles unique to Verbatim tags and headings in order to extract card data.

The following rules are used for extraction:

1. **Tag:** paragraphs styled as `Heading4`
2. **Citation:** paragraphs with no style, immediately following a tagline
3. **Body:** consecutive paragraphs next heading

Each card is given a UUID and a global path derived from the document's heading hierarchy (e.g. `DocumentName.docx/Heading1/Heading2/Heading3/Tag`) primarily for traceability.

For `.zip` archives, the parser globs all `.docx` files in the archive and processes each.

## Duplicate Detection

Many times in debate, people will slighly modify cards, or the way they copy them in their documents means that duplicates of a card will not be processed exactly the same.
Specifically, variation in the citation, the tagline, etc. makes it difficult to identify duplicates based on value comparison, or even hash comparison.
However, with locality-sensitive hashing, we can find near duplicates in an efficient manner when dealing with hundreds of thousands of cards.

Thus, duplicate detection is implemented using MinHash Locality-Sensitive Hashing:

1. For each card, a key is built from the tagline, author surname, author year, and the first 200 words of the body.
2. The key is tokenized into word-level shingles of size 3 (basically trigrams), and a MinHash signature comprised of 128 hash functions is computed.
3. Cards are compared by **Jaccard similarity**, and if two cards share a MinHash bucket and their similarity exceeds **0.85**, we have found a duplicate.

## DB Structure

For reference:

### `debate_cards`

| Column               | Type           |
| -------------------- | -------------- |
| `id`                 | `SERIAL`       |
| `card_id`            | `VARCHAR(255)` |
| `tagline`            | `TEXT`         |
| `tagline_html`       | `TEXT`         |
| `citation_raw`       | `TEXT`         |
| `citation_html`      | `TEXT`         |
| `cite`               | `TEXT`         |
| `author_surname`     | `VARCHAR(255)` |
| `author_year`        | `VARCHAR(50)`  |
| `author_credentials` | `TEXT`         |
| `publication_title`  | `TEXT`         |
| `publisher`          | `TEXT`         |
| `url`                | `TEXT`         |
| `credits`            | `VARCHAR(255)` |
| `body_text`          | `TEXT`         |
| `body_html`          | `TEXT`         |
| `highlighted_text`   | `TEXT`         |
| `source_filename`    | `VARCHAR(255)` |
| `global_path`        | `TEXT`         |
| `paragraph_start`    | `INTEGER`      |
| `paragraph_end`      | `INTEGER`      |
| `word_count`         | `INTEGER`      |
| `highlight_count`    | `INTEGER`      |
| `created_at`         | `TIMESTAMP`    |
| `inserted_at`        | `TIMESTAMP`    |

### `tags`

| Column       | Type          |
| ------------ | ------------- |
| `id`         | `SERIAL`      |
| `name`       | `VARCHAR(50)` |
| `created_at` | `TIMESTAMP`   |

### `debate_cards_tags`

| Column       | Type        |
| ------------ | ----------- |
| `id`         | `SERIAL`    |
| `card_id`    | `INTEGER`   |
| `tag_id`     | `INTEGER`   |
| `created_at` | `TIMESTAMP` |
