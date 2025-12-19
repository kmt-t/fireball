# -----------------------------------------------------------------------------
# Ubuntu 24.04 + rolling clang (via apt.llvm.org/llvm.sh)
# meson/lizard installed into 'developer' user via pipx (no distutils)
# plus: clang/clangd/lld, ninja, pandoc+TeX, mermaid-cli, Node 24.x
# -----------------------------------------------------------------------------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV DEVELOPER_USER=developer
ENV DEVELOPER_HOME=/home/${DEVELOPER_USER}
ENV PATH=${DEVELOPER_HOME}/.local/bin:/root/.local/bin:${PATH}

# 1) Basic packages — avoid python3-distutils; use python3-venv / setuptools
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends software-properties-common; \
    add-apt-repository -y universe || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates wget curl gnupg lsb-release software-properties-common \
      build-essential git python3 python3-pip python3-venv python3-setuptools \
      unzip fontconfig locales less pkg-config cmake sudo; \
    rm -rf /var/lib/apt/lists/*

# 2) Create developer user (no password, with sudo NOPASSWD)
RUN set -eux; \
    useradd -m -s /bin/bash ${DEVELOPER_USER}; \
    usermod -aG sudo ${DEVELOPER_USER}; \
    echo "${DEVELOPER_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${DEVELOPER_USER}; \
    chmod 0440 /etc/sudoers.d/${DEVELOPER_USER}

# 3) Node.js 24.x for mermaid-cli
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# 4) LLVM repo via official script -> install rolling clang/clangd/lld
RUN set -eux; \
    wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh; \
    chmod +x /tmp/llvm.sh; \
    bash /tmp/llvm.sh; \
    apt-get update; \
    apt-get install -y --no-install-recommends clang clangd lld lldb ninja-build; \
    rm -rf /var/lib/apt/lists/* /tmp/llvm.sh

# 5) Install pipx (system) so we can install per-developer-user tools (no system pip installs)
RUN apt-get update \
 && apt-get install -y --no-install-recommends pipx python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pipx ensurepath || true

# 6) Install meson and lizard into developer's account via pipx (user-level)
RUN set -eux; \
    mkdir -p ${DEVELOPER_HOME}/.local/bin; \
    chown -R ${DEVELOPER_USER}:${DEVELOPER_USER} ${DEVELOPER_HOME}; \
    # run pipx as developer so installs go to /home/developer/.local
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install meson || true; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install lizard || true

# 7) pandoc + TeX (mid-sized texlive set)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    pandoc \
    texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-xetex \
 && rm -rf /var/lib/apt/lists/*

# 8) mermaid-cli deps + install (puppeteer will download Chromium during npm install)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates fonts-liberation libasound2t64 libatk1.0-0 libatk-bridge2.0-0 libcairo2 \
    libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxrandr2 libxss1 libxtst6 xdg-utils \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --unsafe-perm=true @mermaid-js/mermaid-cli puppeteer

RUN chown -R ${DEVELOPER_USER}:${DEVELOPER_USER} ${DEVELOPER_HOME} /workspace || true

# Show versions for build logs (non-fatal)
RUN echo "=== Tools versions ===" \
 && clang --version || true \
 && clangd --version || true \
 && lld --version || true \
 && su - ${DEVELOPER_USER} -c "${DEVELOPER_HOME}/.local/bin/meson --version" || true \
 && ninja --version || true \
 && pandoc --version || true \
 && node --version || true \
 && npm --version || true \
 && mmdc --version || true \
 && su - ${DEVELOPER_USER} -c "/usr/bin/pipx list" || true

CMD ["/bin/bash"]
