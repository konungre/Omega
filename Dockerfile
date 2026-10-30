# syntax=docker/dockerfile:1.7

# For maximum integrity, set this to an immutable digest in CI/CD.
ARG SWIPL_IMAGE=docker.io/library/swipl:10.0.2

FROM ${SWIPL_IMAGE} AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/opt/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/opt/sentence_transformers

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      build-essential \
      cmake \
      pkg-config \
      python3 \
      python3-dev \
      python3-pip \
      libopenblas-dev \
      libblas-dev \
      liblapack-dev \
      gfortran \
      libgflags-dev \
      nano \
 && rm -rf /var/lib/apt/lists/*

# Build dependencies from source. Pin refs at build time for reproducibility.
ARG PETTA_REPO=https://github.com/trueagi-io/PeTTa.git
ARG PETTA_REF=v1.0.4
ARG FAISS_REPO=https://github.com/facebookresearch/faiss.git
ARG FAISS_REF=v1.8.0
ARG CHROMADB_REPO=https://github.com/patham9/petta_lib_chromadb.git
ARG CHROMADB_REF=master

# Embedding model to pre-download at build time.
ARG EMBEDDING_MODEL=intfloat/e5-large-v2

RUN git clone --depth 1 --branch "${PETTA_REF}" "${PETTA_REPO}" /PeTTa
RUN git clone --depth 1 --branch "${FAISS_REF}" "${FAISS_REPO}" /faiss

WORKDIR /faiss
RUN cmake -B build -DFAISS_ENABLE_GPU=OFF -DFAISS_ENABLE_PYTHON=OFF -DBUILD_SHARED_LIBS=OFF \
 && cmake --build build --config Release --parallel \
 && cmake --install build

WORKDIR /PeTTa
RUN sh build.sh
RUN mkdir -p /PeTTa/repos \
 && git clone --depth 1 --branch "${CHROMADB_REF}" "${CHROMADB_REPO}" /PeTTa/repos/petta_lib_chromadb

COPY ./requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.org/simple/ \
    torch==2.12.1 \
 && python3 -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Pre-download the sentence-transformers model so runtime does not need network access.
RUN mkdir -p "${HF_HOME}" "${SENTENCE_TRANSFORMERS_HOME}" \
 && python3 - <<PY
from sentence_transformers import SentenceTransformer
model_name = "${EMBEDDING_MODEL}"
print(f"Downloading embedding model: {model_name}")
SentenceTransformer(model_name)
print("Model download complete.")
PY

# Native reasoner and local CPU model runtime, pinned to audited source revisions.
FROM ${SWIPL_IMAGE} AS native-tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential cmake python3 && rm -rf /var/lib/apt/lists/*
# Serialize the memory-heavy Python/embedding and native compilation stages.
COPY --from=builder /PeTTa/run.sh /tmp/omega-builder-ready
ARG ONA_REF=dc4efd0abd520cdb79bf53bfa3c285ebb24f2e8a
ARG LLAMA_REF=a34de8618b25dfc32ee43749db47d767a9f0a25d
RUN git init /ona \
 && git -C /ona remote add origin https://github.com/opennars/OpenNARS-for-Applications.git \
 && git -C /ona fetch --depth=1 origin "$ONA_REF" \
 && git -C /ona checkout FETCH_HEAD \
 && cd /ona && sh build.sh
RUN git init /llama \
 && git -C /llama remote add origin https://github.com/ggml-org/llama.cpp.git \
 && git -C /llama fetch --depth=1 origin "$LLAMA_REF" \
 && git -C /llama checkout FETCH_HEAD
# Keep compilation objects across interrupted builds; only the finished binary
# is copied into the image layer. The cache is never required at runtime.
RUN --mount=type=cache,id=omega-llama-build,target=/llama/build \
    cmake -S /llama -B /llama/build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF \
      -DGGML_CUDA=OFF -DGGML_VULKAN=OFF -DLLAMA_CURL=OFF \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DBUILD_SHARED_LIBS=OFF \
 && cmake --build /llama/build --target llama-server -j 2 \
 && mkdir -p /native-bin && cp /llama/build/bin/llama-server /native-bin/omega-llama-server

FROM ${SWIPL_IMAGE} AS local-model
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY scripts/fetch-default-model.py /tmp/fetch-default-model.py
RUN python3 /tmp/fetch-default-model.py

FROM builder AS versioned-source

WORKDIR /omega-source
COPY . .
RUN : > /tmp/omega-ignored-tracked \
 && if [ -e .git ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
      git ls-files -ci --exclude-from=.dockerignore -z > /tmp/omega-ignored-tracked; \
      git checkout-index --force --stdin -z < /tmp/omega-ignored-tracked; \
    fi \
 && python3 -c 'from src.helper import omega_version; print(omega_version())' > /tmp/omega-version \
 && mv /tmp/omega-version ./version \
 && while IFS= read -r -d '' path; do rm -f -- "$path"; done < /tmp/omega-ignored-tracked \
 && rm -rf ./.git \
 && chmod 0444 ./version

FROM ${SWIPL_IMAGE} AS runtime

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    SENTENCE_TRANSFORMERS_HOME=/opt/sentence_transformers

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      python3 \
      libopenblas0-pthread \
      libblas3 \
      liblapack3 \
      libgfortran5 \
      libgomp1 \
      libgflags2.2 \
      nano \
      git \
      nginx-light \
      gettext-base \
      poppler-utils \
      util-linux \
      curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /PeTTa

COPY --from=native-tools /ona/NAR /usr/local/bin/omega-nar
COPY --from=native-tools /ona/LICENSE /usr/share/licenses/omega-ona/LICENSE
COPY --from=native-tools /native-bin/omega-llama-server /usr/local/bin/omega-llama-server
COPY --from=native-tools /llama/LICENSE /usr/share/licenses/omega-llama/LICENSE
RUN python3 - <<'PYDEPS'
import subprocess
for binary in ('/usr/local/bin/omega-nar', '/usr/local/bin/omega-llama-server'):
    result = subprocess.run(['ldd', binary], text=True, capture_output=True)
    print(binary + ':\n' + result.stdout)
    if result.returncode or 'not found' in result.stdout:
        raise SystemExit('Missing native runtime dependencies: ' + binary)
PYDEPS
COPY --link --from=local-model /opt/omega/models /opt/omega/models
COPY --from=builder /usr/local /usr/local
COPY --from=builder /PeTTa /PeTTa
COPY --chown=65534:65534 --from=builder /opt/huggingface /opt/huggingface
COPY --chown=65534:65534 --from=builder /opt/sentence_transformers /opt/sentence_transformers

# setup nginx proxy
RUN usermod -a -G tty www-data
RUN mkdir /opt/nginx
RUN chown www-data:www-data /opt/nginx
RUN chmod 0700 /opt/nginx
COPY --chown=www-data:www-data --chmod=0600 ./proxy/* /opt/nginx/

ENV OMEGA_DIR=/PeTTa/repos/Omega
ENV MEMORY_DIR=${OMEGA_DIR}/memory
# Start defaults for import-kb
ENV IMPORT_KB_ON_START=0

# Bring in the clean source tree and its version generated from Git metadata.
COPY --from=versioned-source /omega-source ${OMEGA_DIR}

RUN cp ${OMEGA_DIR}/run.metta /PeTTa/run.metta \
 && mkdir -p ${MEMORY_DIR}/chroma_db \
 && mkdir -p /memory-transfer \
 && ln -s ${MEMORY_DIR}/chroma_db ./chroma_db \
 && chmod +x ${OMEGA_DIR}/entrypoint.sh \
 && chmod +x ${OMEGA_DIR}/scripts/import_knowledge.sh \
 && chmod +x ${OMEGA_DIR}/scripts/omega \
 && chown -R 65534:65534 ${MEMORY_DIR} \
 && chown 65534:65534 /memory-transfer \
 && chmod 0700 /memory-transfer \
 && find ${MEMORY_DIR} -type f -exec chmod 0644 {} \; \
 && chmod 0444 ${MEMORY_DIR}/prompt.txt

EXPOSE 8088
VOLUME ["/PeTTa/repos/Omega/memory"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8081/health || exit 1
ENTRYPOINT ["python3", "/PeTTa/repos/Omega/scripts/docker-entrypoint.py"]
CMD []
