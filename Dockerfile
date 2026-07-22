# avar2-studio — full-tool container (Flask server + fontc builds +
# embedded Fontra). One container = one studio session: fine for a
# limited-time shared demo; per-visitor isolation would mean one
# container per session.
#
#   docker build -t avar2-studio .
#   docker run --rm -p 8080:8080 avar2-studio
#
# ---- builder: frontend bundle + Fontra wheels (both need node) ----
FROM python:3.11-bookworm AS builder
# Node 22 via NodeSource — Fontra's bundle build (and CRA) want a
# newer node than bookworm's apt v18.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# The real Fontra lives on GitHub — the PyPI name "fontra" is an
# unrelated package. Its client bundle builds with node during the
# wheel build, which is why it happens in this stage. First, so the
# layer caches across frontend edits.
RUN pip wheel --no-deps -w /wheels \
      git+https://github.com/fontra/fontra.git \
      git+https://github.com/fontra/fontra-glyphs.git
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci
COPY frontend frontend
RUN cd frontend && npm run build

# ---- runtime ----
FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src src
COPY examples examples
COPY --from=builder /build/frontend/build src/avar2_studio/static
COPY --from=builder /wheels /wheels
# Editable: hatchling's wheel build would double-add static/ here
# (no .git in the build context, so its gitignore-based exclusion of
# src/avar2_studio/static can't kick in against the force-include).
# Editable serves straight from /app/src and skips wheel assembly.
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir /wheels/*.whl && \
    rm -rf /wheels

ENV AVAR2_STUDIO_DEMO=1
EXPOSE 8080
# Boot straight into the bundled exemplar so visitors land on real
# instances. Container filesystem is ephemeral: every restart is a
# pristine demo.
CMD ["avar2-studio", "examples/crispy-mini/sources/CrispyMini.glyphs", \
     "--host", "0.0.0.0", "--port", "8080"]
