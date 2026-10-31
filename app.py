import io
import os
import re
import textwrap
from flask import Flask, render_template, request, jsonify, send_file
import pymupdf as fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "term_work_template.pdf")

# Embed a Times-compatible font in the generated PDF. Liberation Serif Bold has the
# same character widths as Times New Roman Bold, so desktop layout stays identical,
# but because the font is embedded, phone PDF viewers can no longer swap in a
# different (wider/narrower) font that pushes text out of its field.
FONT_BOLD = "Times-Bold"  # fallback if the font file is missing
for _font_path in (
    os.path.join(os.path.dirname(__file__), "fonts", "LiberationSerif-Bold.ttf"),
    os.path.join(os.path.dirname(__file__), "LiberationSerif-Bold.ttf"),
):
    if os.path.exists(_font_path):
        pdfmetrics.registerFont(TTFont("TermSerifBold", _font_path))
        FONT_BOLD = "TermSerifBold"
        break

def _extract_titles_iot(pdf_bytes):
    """Extract practical titles, including table-based PDFs with a separate Sr. No. column."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    found = []

    try:
        for page in doc:
            # First try coordinate-aware extraction. This works when PDF text is
            # stored in separate table columns (serial numbers and titles).
            blocks = page.get_text("blocks")
            serials = []
            column_blocks = []

            for block in blocks:
                x0, y0, x1, y1, raw = block[:5]
                text = re.sub(r"\s+", " ", raw).strip()
                if not text:
                    continue

                # Some PDF extractors merge the end of a title and its Aim
                # paragraph into one block. Keep only the part before "Aim:".
                if re.match(r"^aim\s*:", text, re.I):
                    text = ""
                elif re.search(r"\baim\s*:", text, re.I):
                    text = re.split(r"\baim\s*:", text, maxsplit=1, flags=re.I)[0].strip()
                if not text:
                    continue

                # Serial numbers are in the leftmost column, often as "1.",
                # "2." ... or just "4" with no punctuation.
                if x0 < 80 and y0 > 190:
                    m = re.fullmatch(r"(\d{1,3})\s*[.)]?", text)
                    if m:
                        serials.append((int(m.group(1)), float(y0)))

                # Keep all text blocks in the title column so wrapped title
                # lines can be joined before the following "Aim:" paragraph.
                if 80 <= x0 < 320 and y0 > 195:
                    if re.search(r"\b(?:rubrics|total marks|sign\.?|pg\.? no\.?)\b", text, re.I):
                        continue
                    if re.match(r"^(?:index$|title$|sr\.?\s*no\.?$)", text, re.I):
                        continue
                    column_blocks.append((float(y0), text))

            serials.sort(key=lambda item: item[1])
            column_blocks.sort(key=lambda item: item[0])

            used_title_positions = set()
            for number, serial_y in serials:
                candidates = [
                    (abs(title_y - serial_y), idx, title_y, title)
                    for idx, (title_y, title) in enumerate(column_blocks)
                    if idx not in used_title_positions
                    and not re.match(r"^aim\s*:", title, re.I)
                ]
                if not candidates:
                    continue
                distance, idx, title_y, title = min(candidates, key=lambda item: item[0])
                if distance > 24:
                    continue

                # Join wrapped title lines that appear before the Aim paragraph.
                parts = [title]
                used_title_positions.add(idx)
                for next_idx in range(idx + 1, len(column_blocks)):
                    next_y, next_text = column_blocks[next_idx]
                    if re.match(r"^aim\s*:", next_text, re.I):
                        break
                    # Stop if the next serial number belongs to a new row.
                    if any(abs(next_y - sy) < 10 and sn != number for sn, sy in serials):
                        break
                    if next_idx in used_title_positions:
                        continue
                    # Only join lines close to this title; large gaps indicate a new row.
                    if next_y - column_blocks[next_idx - 1][0] > 22:
                        break
                    parts.append(next_text)
                    used_title_positions.add(next_idx)

                title = re.sub(r"\s+", " ", " ".join(parts)).strip(" .\t-–:")
                if title and len(title) > 1:
                    found.append((number, title))

    finally:
        doc.close()

    # If coordinate-aware extraction did not find anything, fall back to common
    # numbered-text formats such as "1. Title" and "Practical 1: Title".
    if not found:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            lines = []
            for page in doc:
                lines.extend(page.get_text("text", sort=True).splitlines())

            patterns = [
                re.compile(r"^\s*(?:practical|experiment|exp\.?)\s*(?:no\.?)?\s*[-:#.]?\s*(\d{1,3})\s*[:.)\-–]\s*(.+?)\s*$", re.I),
                re.compile(r"^\s*(\d{1,3})\s*[.)\-:]\s*(.+?)\s*$"),
                re.compile(r"^\s*(\d{1,3})\s+(.{4,})\s*$"),
            ]
            for raw in lines:
                line = re.sub(r"\s+", " ", raw).strip()
                if not line or re.match(r"^aim\s*:", line, re.I):
                    continue
                if re.search(r"\b(practical list|list of experiments|index|contents|sr\.?\s*no|serial no|experiment title|subject code|page no)\b", line, re.I):
                    continue
                for pattern in patterns:
                    match = pattern.match(line)
                    if match:
                        number, title = match.group(1), match.group(2).strip()
                        title = title.strip(" .\t-–:")
                        if title and len(title) > 1:
                            found.append((int(number), title))
                            break
        finally:
            doc.close()

    # De-duplicate and preserve serial-number order.
    unique = []
    seen = set()
    for number, title in sorted(found, key=lambda item: item[0]):
        key = (number, re.sub(r"\W+", "", title.lower()))
        if key not in seen:
            unique.append((number, title))
            seen.add(key)
    return [title for _, title in unique]

def _clean_title(text):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.split(r"\baim\s*:", text, maxsplit=1, flags=re.I)[0]
    return text.strip(" .\t-–:")


def _titles_from_table_rows(rows):
    """Pull (serial, title) pairs out of one extracted table (list of rows)."""
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    serial_col, title_col = None, None

    # Look for header cells (short text only, so title text is never mistaken for a header).
    for r in rows[:3]:
        for ci, cell in enumerate(r):
            h = re.sub(r"\s+", " ", str(cell or "")).strip().lower()
            if not h or len(h) > 20:
                continue
            if serial_col is None and re.fullmatch(r"(sr\.?|s\.?\s?no\.?|serial)(\s*no\.?)?|no\.?", h):
                serial_col = ci
            elif title_col is None and re.search(r"title|experiment|practical|aim|topic|program", h):
                title_col = ci

    # Serial column default: the leftmost column whose cells are mostly numbers.
    if serial_col is None:
        best, best_hits = 0, -1
        for ci in range(ncols):
            hits = sum(
                1 for r in rows
                if ci < len(r) and re.fullmatch(r"\d{1,3}\s*[.)]?", str(r[ci] or "").strip())
            )
            if hits > best_hits:
                best, best_hits = ci, hits
        serial_col = best

    # Title column default: the column (other than serial) holding the most text.
    if title_col is None or title_col == serial_col:
        best, best_len = None, -1
        for ci in range(ncols):
            if ci == serial_col:
                continue
            total = sum(len(str(r[ci] or "")) for r in rows if ci < len(r))
            if total > best_len:
                best, best_len = ci, total
        title_col = best
    if title_col is None:
        return []

    found = []
    for r in rows:
        if serial_col >= len(r) or title_col >= len(r):
            continue
        m = re.fullmatch(r"(\d{1,3})\s*[.)]?", re.sub(r"\s+", "", str(r[serial_col] or "")))
        if not m:
            continue
        title = _clean_title(r[title_col])
        if title and len(title) > 1:
            found.append((int(m.group(1)), title))
    return found


def _words_in_bbox(words, bbox):
    """Join the words whose centre lies inside bbox, with single spaces, in reading order."""
    x0, y0, x1, y1 = bbox
    picked = [
        w for w in words
        if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1
    ]
    picked.sort(key=lambda w: (w[1] + w[3]) / 2)

    # group into visual lines, then left-to-right inside each line
    lines, current, current_y = [], [], None
    for w in picked:
        ymid = (w[1] + w[3]) / 2
        if current_y is None or abs(ymid - current_y) <= 3:
            current.append(w)
            current_y = ymid if current_y is None else (current_y + ymid) / 2
        else:
            lines.append(current)
            current, current_y = [w], ymid
    if current:
        lines.append(current)

    return " ".join(
        " ".join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in lines
    )


def _table_rows_with_spaces(page_words, tab):
    """Rows of cell text built from the page's words, so spaces are never lost."""
    try:
        rows = []
        for row in tab.rows:
            rows.append([
                _words_in_bbox(page_words, bbox) if bbox else ""
                for bbox in row.cells
            ])
        if rows:
            return rows
    except Exception:
        pass
    return tab.extract()


def _titles_from_tables(pdf_bytes):
    """Read the index table cell by cell (works across multiple pages)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    found = []
    try:
        for page in doc:
            try:
                tables = page.find_tables().tables
            except Exception:
                continue
            page_words = page.get_text("words")
            for tab in tables:
                found.extend(_titles_from_table_rows(_table_rows_with_spaces(page_words, tab)))
    finally:
        doc.close()

    by_number = {}
    for number, title in found:
        by_number.setdefault(number, title)
    return [by_number[n] for n in sorted(by_number)]


def extract_titles(pdf_bytes):
    """Run the table reader and the original IoT reader; keep whichever finds
    more practicals. The IoT reader wins ties, so IoT behaviour is unchanged."""
    best = []
    for strategy in (_extract_titles_iot, _titles_from_tables):
        try:
            result = strategy(pdf_bytes)
        except Exception:
            app.logger.exception("Strategy %s failed", strategy.__name__)
            continue
        if len(result) > len(best):
            best = result
    return best


def fit_text(c, text, x, y_top, max_width, font=FONT_BOLD, size=11, clear_line=True):
    """Draw moderately bold text, fitting it inside the target field.

    y_top is measured from the top edge of the A4 page. A small white
    background behind the value hides the template's underline only where
    the value is written, keeping the rest of the form unchanged.
    """
    text = str(text or "").strip()
    if not text:
        return

    current_size = size
    while current_size > 9 and stringWidth(text, font, current_size) > max_width:
        current_size -= 0.25

    text_width = stringWidth(text, font, current_size)
    baseline_y = 842 - y_top - current_size

    if clear_line:
        c.setFillColorRGB(1, 1, 1)
        c.rect(
            x - 1.5,
            baseline_y - 1.5,
            min(max_width, text_width + 4),
            current_size + 4,
            fill=1,
            stroke=0,
        )

    c.setFillColorRGB(0, 0, 0)
    c.setFont(font, current_size)
    c.drawString(x, baseline_y, text)


def fit_text_centered(c, text, left, right, y_top, font=FONT_BOLD, size=13, clear_line=False):
    """Draw a value horizontally centered in its field without unnecessarily covering the printed underline."""
    text = str(text or "").strip()
    if not text:
        return

    field_width = right - left
    current_size = size
    while current_size > 10 and stringWidth(text, font, current_size) > field_width - 8:
        current_size -= 0.25

    text_width = stringWidth(text, font, current_size)
    x = left + (field_width - text_width) / 2
    fit_text(c, text, x, y_top, field_width, font=font, size=current_size, clear_line=clear_line)


TITLE_FIRST_WIDTH = 560.5 - 317.0
TITLE_CONT_WIDTH = 560.5 - 66.0


def _wrap_words(words, font, font_size):
    """Greedy wrap into the short first line, then full-width continuation lines."""
    lines = []
    current = ""
    available = TITLE_FIRST_WIDTH

    for word in words:
        trial = f"{current} {word}".strip()
        if stringWidth(trial, font, font_size) <= available:
            current = trial
        elif not current:
            # A single unusually long word: allow fit_text to shrink it.
            current = word
        else:
            lines.append(current)
            current = word
            available = TITLE_CONT_WIDTH

    if current:
        lines.append(current)
    return lines


BULLET_SPLIT = re.compile(r"\s*[•●▪■◦]\s*")
SUBSTEP_SPLIT = re.compile(r"\s+\d{1,2}\)\s+")


def shorten_title_to_fit(title, font=FONT_BOLD, min_size=10.5):
    """Cut an over-long title so it fits the template's 3 title lines.

    1. Drop numbered sub-steps ("1) ...", "2) ...").
    2. Keep as many whole sentences as fit.
    3. If even the first sentence is too long, cut at a word and add "...".
    """
    def fits(text):
        return len(_wrap_words(text.split(), font, min_size)) <= 3

    title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not title or fits(title):
        return title

    head = SUBSTEP_SPLIT.split(title)[0].strip() or title
    sentences = re.split(r"(?<=[.!?])\s+", head)
    kept = []
    for sentence in sentences:
        if fits(" ".join(kept + [sentence])):
            kept.append(sentence)
        else:
            break
    # Don't end on a dangling "as below:" style sentence.
    while len(kept) > 1 and kept[-1].rstrip().endswith(":"):
        kept.pop()
    if kept:
        return " ".join(kept).strip(" :;,-–")

    # Even the first sentence is too long: cut at a word boundary.
    words = head.split()
    while words and not fits(" ".join(words) + "..."):
        words.pop()
    return (" ".join(words).rstrip(" ,;:-–") + "...") if words else head[:60] + "..."


def wrap_title_for_template(title, font=FONT_BOLD, size=13):
    """Wrap title for the short first line and two full-width continuation lines.

    Titles that fit are wrapped exactly as before. Titles that cannot fit in
    the three template lines are automatically shortened first.
    """
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    # Bullet lists ("• step one • step two") never belong in the title line.
    title = BULLET_SPLIT.split(title)[0].strip(" :;,-–") or title
    if not title.split():
        return [], size

    sizes = [size, 12.5, 12, 11.5, 11, 10.5, 10, 9.5]

    def try_fit(text):
        words = text.split()
        for font_size in sizes:
            lines = _wrap_words(words, font, font_size)
            if len(lines) <= 3:
                return lines, font_size
        return None

    result = try_fit(title)
    if result:
        return result

    shortened = shorten_title_to_fit(title, font)
    result = try_fit(shortened)
    if result:
        return result

    # Last-resort safety net (should not normally be reached).
    lines = _wrap_words(shortened.split(), font, 9.5)
    return lines[:3], 9.5


def parse_practical_numbers(spec):
    """'7' -> [7];  '2,5,7-9' -> [2, 5, 7, 8, 9].  Blank -> []."""
    numbers = set()
    for token in re.split(r"[,\s;]+", str(spec or "").strip()):
        if not token:
            continue
        m = re.fullmatch(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?", token)
        if not m:
            raise ValueError(f"'{token}' is not a valid practical number. Use e.g. 7 or 2,5,7-9.")
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else low
        if low < 1 or high < low or high > 99:
            raise ValueError(f"'{token}' is not a valid practical number range.")
        numbers.update(range(low, high + 1))
    return sorted(numbers)


def select_practicals(titles, spec):
    """Decide which practicals to generate and which number each page prints.

    * blank                       -> every title, numbered 1, 2, 3 ...
    * numbers that exist in list  -> only those titles, keeping their list number
                                     (list of 10, "7" -> only practical 7)
    * numbers beyond the list     -> labels for typed titles, in order
                                     (one typed title, "7" -> printed as No. 7)
    """
    numbers = parse_practical_numbers(spec)
    if not numbers:
        return list(enumerate(titles, start=1))
    if all(n <= len(titles) for n in numbers):
        return [(n, titles[n - 1]) for n in numbers]
    if len(numbers) == len(titles):
        return list(zip(numbers, titles))
    raise ValueError(
        f"You asked for practical number(s) {', '.join(map(str, numbers))}, "
        f"but the list below has {len(titles)} title(s). Enter one title for each "
        "number, or pick numbers that exist in the list."
    )


def build_pdf(data, titles):
    # Keep the original template bytes and load a fresh page for every practical.
    # A shallow copy of a pypdf PageObject can share internal content/resources;
    # merging overlays into such copies can cause pages to repeat or accumulate
    # the same title/number. Reloading the template for each page avoids that.
    with open(TEMPLATE_PATH, "rb") as template_file:
        template_bytes = template_file.read()

    first_reader = PdfReader(io.BytesIO(template_bytes))
    first_page = first_reader.pages[0]
    page_width = float(first_page.mediabox.width)
    page_height = float(first_page.mediabox.height)
    writer = PdfWriter()

    # `titles` is a list of (practical_no, title) pairs; plain strings are
    # numbered 1, 2, 3 ... for backward compatibility.
    items = [
        item if isinstance(item, (tuple, list)) else (position, item)
        for position, item in enumerate(titles, start=1)
    ]

    for practical_no, title in items:
        template_reader = PdfReader(io.BytesIO(template_bytes))
        template_page = template_reader.pages[0]
        overlay_io = io.BytesIO()
        c = canvas.Canvas(overlay_io, pagesize=(page_width, page_height))

        # Center values inside their designated spaces. Slightly smaller text
        # for TERM and Class keeps it from covering the printed underline.
        # Text is placed a little above the underline so the original line stays visible.
        fit_text_centered(c, data.get("term", ""), 290, 378, 188.9, size=11.5)
        fit_text(c, data.get("subject", ""), 115, 222.8, 445, size=12.5)
        fit_text(c, data.get("pen", ""), 98, 246.7, 218, size=12.5)
        fit_text_centered(c, data.get("semester", ""), 424, 558, 246.7, size=12.5)
        fit_text_centered(c, data.get("student_name", ""), 170, 558, 270.1, size=13.5)
        fit_text_centered(c, data.get("class_name", ""), 65, 278, 295.7, size=11.5)
        fit_text_centered(c, data.get("batch", ""), 312, 558, 294.7, size=12.5)

        title_lines, title_size = wrap_title_for_template(title)
        title_positions = [
            (317, 319.25, 243),
            (66, 343.6, 494),
            (66, 368, 494),
        ]
        for line, (x, y_top, max_width) in zip(title_lines, title_positions):
            fit_text(
                c, line, x, y_top, max_width,
                size=title_size, clear_line=True
            )

        fit_text_centered(c, str(practical_no), 442, 568, 417.8, size=12.5)
        fit_text(c, data.get("checked_by", ""), 142, 544, 416, size=13)

        c.save()
        overlay_io.seek(0)
        overlay_page = PdfReader(overlay_io).pages[0]

        # Each loop iteration owns a newly loaded template page, so this merge
        # changes only the current page and every practical keeps its own title
        # and sequential number.
        template_page.merge_page(overlay_page)
        writer.add_page(template_page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/extract", methods=["POST"])
def extract():
    file = request.files.get("practical_pdf")
    if not file or not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a practical-list PDF file."}), 400
    try:
        titles = extract_titles(file.read())
    except Exception as exc:
        app.logger.exception("PDF extraction failed")
        return jsonify({
            "error": f"PDF extraction failed ({type(exc).__name__}). Please try again or contact support."
        }), 400
    if not titles:
        return jsonify({
            "error": "No numbered practical titles were detected. This may be a scanned PDF or use a different format. You can type/paste the titles into the list below, one title per line."
        }), 200
    return jsonify({"titles": titles, "count": len(titles)})

@app.route("/generate", methods=["POST"])
def generate():
    fields = ["term", "subject", "pen", "semester", "student_name", "class_name", "batch", "checked_by"]
    data = {key: request.form.get(key, "").strip() for key in fields}
    titles_text = request.form.get("titles", "")
    titles = [line.strip() for line in titles_text.splitlines() if line.strip()]
    if not titles:
        return "Please upload a practical list and provide at least one practical title.", 400
    required = ["term", "subject", "pen", "semester", "student_name", "class_name", "batch", "checked_by"]
    if any(not data[k] for k in required):
        return "Please complete all eight student/detail fields.", 400
    try:
        titles = select_practicals(titles, request.form.get("practical_numbers", ""))
    except ValueError as exc:
        return str(exc), 400
    try:
        output = build_pdf(data, titles)
    except Exception as exc:
        app.logger.exception("PDF generation failed")
        return f"PDF generation failed: {exc}", 500
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", data["student_name"]).strip("_") or "student"
    return send_file(output, mimetype="application/pdf", as_attachment=True,
                     download_name=f"Term_Work_{safe_name}.pdf")

@app.errorhandler(413)
def too_large(_error):
    return "The uploaded file is too large. Please use a PDF under 20 MB.", 413

if __name__ == "__main__":
    app.run(debug=False)
