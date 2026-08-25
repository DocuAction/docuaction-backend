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

# Run as a non-root user. Nothing in the application writes outside UPLOAD_DIR
# and /tmp, and the build steps above are already complete by this point.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# PORT ALIGNMENT - read this before changing it.
#
# Three places have to agree or the container starts and App Service never
# routes to it:
#
#   1. this CMD / EXPOSE
#   2. the App Service `appCommandLine` (Configuration > Startup Command),
#      which OVERRIDES this CMD entirely when it is set
#   3. the WEBSITES_PORT app setting, which tells App Service which port to
#      probe inside the container
#
# On DEV as of 2026-08-24 they did NOT agree: appCommandLine bound gunicorn to
# :8000 while this file exposed 8080 and WEBSITES_PORT was unset. Under the
# built-in Python stack that was harmless because appCommandLine is simply the
# startup command; in a container it means the app listens on 8000 while App
# Service probes 8080, and the site never comes up.
#
# Before a container rehearsal: clear appCommandLine (so this CMD is used) and
# set WEBSITES_PORT=8080, or align all three on one port.
EXPOSE 8080

# gunicorn with the uvicorn worker, matching the startup command the built-in
# stack has been running. The 600s timeout is not decorative: report generation
# renders charts and a 300k+ document, and the default 30s kills it.
CMD ["sh", "-c", "python -m gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind=0.0.0.0:${PORT:-8080} --timeout 600 --forwarded-allow-ips='*'"]
