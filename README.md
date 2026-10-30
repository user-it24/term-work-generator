# Term Work Generator

A local web app that reads a practical-list PDF, extracts numbered experiment titles, and generates one combined PDF using the supplied college term-work template.

## Features
- Upload a practical list PDF.
- Automatically extract numbered practical titles from text-based PDFs.
- Edit extracted titles before generation.
- Enter TERM, Subject, PEN, Semester, Name of Student, Class, Batch, and Checked By once.
- Generate one PDF with one report page per practical.
- Keeps dates, marks, remarks, and signature blank for manual completion.

## Requirements
- Python 3.10 or newer
- Internet is not required after dependencies are installed.
- The supplied `term_work_template.pdf` is included.

## Run on Windows
1. Extract the ZIP file.
2. Open Command Prompt in the extracted `term_work_generator` folder.
3. Create and activate a virtual environment (recommended):

   ```bat
   py -m venv .venv
   .venv\Scripts\activate
   ```

4. Install dependencies:

   ```bat
   pip install -r requirements.txt
   ```

5. Start the app:

   ```bat
   python app.py
   ```

6. Open `http://127.0.0.1:5000` in your browser.

## Use
1. Select the practical-list PDF and click **Read practical list**.
2. Check the extracted titles in the title box. If the PDF format is unusual, type or paste one title per line.
3. Complete all eight student/detail fields.
4. Click **Generate & Download PDF**.

## Important notes
- Text-based PDFs work best. A scanned/image-only practical list needs OCR; this starter version does not include OCR.
- Title extraction supports common formats such as `1. Title`, `1) Title`, `Experiment 1: Title`, and `Practical No. 1 - Title`. Always review extracted titles.
- The generator uses the supplied one-page college template as the background. The placement of filled text may need small adjustment if the college changes the template.
- The app runs locally on your computer; it does not upload student information to a remote server.
- This is a starter project. Test the generated pages and compare them with your official college format before using them for submission.
