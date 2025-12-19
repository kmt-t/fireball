# =============================================================================
# Development Container: Ubuntu 24.04 with C/C++ Embedded Development Tools
# =============================================================================
#
# Environment Overview:
#   - Base Image: Ubuntu 24.04 LTS
#   - Development User: developer (sudo NOPASSWD)
#   - Locale: C.UTF-8
#
# Compiler & Build Tools:
#   - Clang/Clang-D/LLD: Latest version (via apt.llvm.org/llvm.sh)
#   - Ninja: Build system
#   - CMake: Build configuration
#   - Build-essential: Basic tools (GCC/Make, etc.)
#
# Build System & Code Analysis:
#   - Meson: Build system (installed via pipx for developer user)
#   - Lizard: Code complexity analysis (installed via pipx for developer user)
#
# Documentation Generation:
#   - Pandoc: Document conversion
#   - TeX Live: LaTeX (texlive-latex-recommended/extra/fonts/xetex)
#   - Mermaid CLI: Diagram generation (Node.js 24.x + npm)
#
# Debug & Profiling:
#   - Valgrind: Memory profiling
#   - QEMU: ARM Cortex-M33, RISC-V 32bit cross-target execution
#   - LLDB: Debugger
#
# Python Environment:
#   - Python 3 + venv + setuptools (distutils not used)
#   - pipx: User-level tool installation (system pip not used)
#
# =============================================================================

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV DEVELOPER_USER=developer
ENV DEVELOPER_HOME=/home/${DEVELOPER_USER}
ENV PATH=${DEVELOPER_HOME}/.local/bin:/root/.local/bin:${PATH}

# Basic packages (build-essential, git, python3, cmake, etc.)
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

# Create development user (no password, sudo NOPASSWD)
RUN set -eux; \
    useradd -m -s /bin/bash ${DEVELOPER_USER}; \
    usermod -aG sudo ${DEVELOPER_USER}; \
    echo "${DEVELOPER_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${DEVELOPER_USER}; \
    chmod 0440 /etc/sudoers.d/${DEVELOPER_USER}

# Node.js 24.x (for Mermaid CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# LLVM latest version (Clang/Clang-D/LLD/LLDB/Ninja)
RUN set -eux; \
    wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh; \
    chmod +x /tmp/llvm.sh; \
    bash /tmp/llvm.sh; \
    apt-get update; \
    apt-get install -y --no-install-recommends clang clangd lld lldb ninja-build; \
    rm -rf /var/lib/apt/lists/* /tmp/llvm.sh

# pipx (for user-level tool installation)
RUN apt-get update \
 && apt-get install -y --no-install-recommends pipx python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pipx ensurepath || true

# Meson + Lizard (installed in developer user's ~/.local/bin)
RUN set -eux; \
    mkdir -p ${DEVELOPER_HOME}/.local/bin; \
    chown -R ${DEVELOPER_USER}:${DEVELOPER_USER} ${DEVELOPER_HOME}; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install meson || true; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install lizard || true

# Pandoc + TeX Live (for documentation generation)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    pandoc \
    texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-xetex \
 && rm -rf /var/lib/apt/lists/*

# Mermaid CLI dependencies + installation
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates fonts-liberation libasound2t64 libatk1.0-0 libatk-bridge2.0-0 libcairo2 \
    libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxrandr2 libxss1 libxtst6 xdg-utils \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --unsafe-perm=true @mermaid-js/mermaid-cli puppeteer

# Valgrind + QEMU (for memory profiling and cross-target execution)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    valgrind \
    qemu-system-arm qemu-system-riscv32 \
 && rm -rf /var/lib/apt/lists/*

# Display tool versions for build log
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
