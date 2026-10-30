import io
import os
import re
import textwrap
from flask import Flask, render_template, request, jsonify, send_file
import pymupdf as fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from pypdf import PdfReader, PdfWriter

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "term_work_template.pdf")

def extract_titles(pdf_bytes):
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

def fit_text(c, text, x, y_top, max_width, font="Times-Roman", size=10.5):
    """Draw a line of text at x and a top-origin y coordinate."""
    c.setFont(font, size)
    text = str(text or "").strip()
    while size > 7 and stringWidth(text, font, size) > max_width:
        size -= 0.25
        c.setFont(font, size)
    c.drawString(x, 842 - y_top - size, text)

def wrap_title(title, max_width=475, font="Times-Roman", size=10.5):
    words = str(title).split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if stringWidth(trial, font, size) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]

def build_pdf(data, titles):
    template_reader = PdfReader(TEMPLATE_PATH)
    template_page = template_reader.pages[0]
    writer = PdfWriter()
    page_width = float(template_page.mediabox.width)
    page_height = float(template_page.mediabox.height)

    for index, title in enumerate(titles, start=1):
        overlay_io = io.BytesIO()
        c = canvas.Canvas(overlay_io, pagesize=(page_width, page_height))

        # Fill only the fields students want pre-filled. Dates, marks, remarks,
        # signature and other assessment fields remain blank for manual completion.
        fit_text(c, data.get("term", ""), 300, 198, 100, size=10.5)
        fit_text(c, data.get("subject", ""), 118, 232, 445, size=10.5)
        fit_text(c, data.get("pen", ""), 100, 256, 195, size=10.5)
        fit_text(c, data.get("semester", ""), 385, 256, 170, size=10.5)
        fit_text(c, data.get("student_name", ""), 173, 281, 390, size=10.5)
        fit_text(c, data.get("class_name", ""), 104, 306, 190, size=10.5)
        fit_text(c, data.get("batch", ""), 344, 306, 210, size=10.5)

        title_lines = wrap_title(title)
        positions = [(318, 329), (65, 354), (65, 378)]
        for line, (x, y) in zip(title_lines, positions):
            fit_text(c, line, x, y, 490 if x < 100 else 245, size=10.5)

        fit_text(c, str(index), 442, 427, 100, size=10.5)
        fit_text(c, data.get("checked_by", ""), 145, 555, 410, size=10.5)

        c.save()
        overlay_io.seek(0)
        overlay_page = PdfReader(overlay_io).pages[0]
        page = template_page
        # Copy template page each time so overlays never accumulate.
        import copy
        page = copy.copy(template_page)
        page.merge_page(overlay_page)
        writer.add_page(page)

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
