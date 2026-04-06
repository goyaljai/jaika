"""Markdown to PDF conversion using pandoc + pdflatex."""

import os
import subprocess
import tempfile
import uuid


def markdown_to_pdf(markdown_text, output_dir):
    """Convert markdown text to PDF. Returns output file path or None."""
    filename = f"{uuid.uuid4().hex[:8]}.pdf"
    output_path = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
        tmp.write(markdown_text)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "pandoc", tmp_path,
                "-o", output_path,
                "--pdf-engine=pdflatex",
                "-V", "geometry:margin=1in",
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return None, result.stderr
        return output_path, None
    except FileNotFoundError:
        return None, "pandoc not installed"
    except subprocess.TimeoutExpired:
        return None, "PDF generation timed out"
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
