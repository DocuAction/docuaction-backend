FROM python:3.12-slim
WORKDIR /app

# WeasyPrint renders every PDF deliverable, and it is a binding to the
# Pango/Cairo/GObject stack rather than a pure-Python library. Without these the
# import succeeds and rendering fails at request time with
# "cannot load library 'libgobject-2.0-0'" — which is exactly what happens on a
# Windows host without the GTK3 runtime.
#
# This image previously installed only ffmpeg, so PDF generation would have
# failed here too. The code's own skip message claimed these libraries "are
# present in the project's Linux container image"; they were not. Phase 7.5
# found that and this is the fix.
#
# fonts-dejavu-core is the fallback face. Reports inline their own WOFF so the
# document is self-contained, but a missing fallback turns any unexpected glyph
# into a blank box rather than a substituted character.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fail the BUILD if the PDF engine cannot start. A container that boots happily
# and then 503s on every PDF request is the worse outcome: the failure surfaces
# to whoever asked for a deliverable rather than to whoever built the image.
RUN python -c "\
from weasyprint import HTML; \
pdf = HTML(string='<html><body><h1>build check</h1></body></html>').write_pdf(); \
assert pdf[:5] == b'%PDF-', 'WeasyPrint did not emit a PDF'; \
print('PDF engine OK, %d bytes' % len(pdf))"

COPY . .
EXPOSE 8080
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
