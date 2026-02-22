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
#   - Python 3 + setuptools (distutils not used)
#   - pipx: User-level tool installation (system pip not used)
#
# =============================================================================

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV DEVELOPER_USER=developer
ENV DEVELOPER_HOME=/home/${DEVELOPER_USER}
ENV PATH=${DEVELOPER_HOME}/.local/bin:${DEVELOPER_HOME}/.cargo/bin:${PATH}

# Basic packages (build-essential, git, python3, cmake, etc.)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends software-properties-common; \
    add-apt-repository -y universe || true; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates wget curl gnupg lsb-release software-properties-common \
      build-essential git python3 python3-setuptools \
      unzip fontconfig locales less pkg-config cmake sudo; \
    rm -rf /var/lib/apt/lists/*

# Create development user (no password, sudo NOPASSWD)
RUN set -eux; \
    useradd -m -s /bin/bash ${DEVELOPER_USER}; \
    usermod -aG users ${DEVELOPER_USER};

# Node.js 24.x (for Mermaid CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# LLVM latest version (Clang/Clang-D/LLD/LLDB/Ninja/Format/Tidy)
RUN set -eux; \
    wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh; \
    chmod +x /tmp/llvm.sh; \
    bash /tmp/llvm.sh; \
    apt-get update; \
    apt-get install -y --no-install-recommends clang clangd lld lldb clang-format clang-tidy ninja-build; \
    rm -rf /var/lib/apt/lists/* /tmp/llvm.sh

# pipx (for user-level tool installation)
RUN apt-get update \
 && apt-get install -y --no-install-recommends pipx \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m pipx ensurepath || true

# Meson + Lizard (installed in developer user's ~/.local/bin)
RUN set -eux; \
    mkdir -p ${DEVELOPER_HOME}/.local/bin; \
    chown -R ${DEVELOPER_USER}:${DEVELOPER_USER} ${DEVELOPER_HOME}; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install meson || true; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install lizard || true; \
    runuser -u ${DEVELOPER_USER} -- /usr/bin/pipx install gprof2dot || true

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

RUN npm install -g @google/gemini-cli @mermaid-js/mermaid-cli puppeteer

# Valgrind + QEMU + Cross-compilation toolchains (ARM & RISC-V)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    valgrind \
    qemu-system-arm qemu-system-riscv32 \
    # ARM Cortex-M/R toolchain
    gcc-arm-none-eabi \
    libstdc++-arm-none-eabi-dev \
    picolibc-arm-none-eabi \
    # RISC-V toolchain
    gcc-riscv64-unknown-elf \
    picolibc-riscv64-unknown-elf \
    libstdc++-13-dev-riscv64-cross \
    # LLVM base
    llvm \
 && rm -rf /var/lib/apt/lists/*

# Call Graph & Documentation Tools
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    doxygen graphviz cflow \
 && rm -rf /var/lib/apt/lists/*

# Rust + WIT tools
ENV RUSTUP_HOME=${DEVELOPER_HOME}/.rustup \
    CARGO_HOME=${DEVELOPER_HOME}/.cargo

RUN set -eux; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | runuser -u ${DEVELOPER_USER} -- sh -s -- -y --no-modify-path; \
    runuser -u ${DEVELOPER_USER} -- ${DEVELOPER_HOME}/.cargo/bin/cargo install wit-bindgen-cli wasm-tools;

# TLA+ Toolbox and TLC model checker for formal verification
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre \
    && rm -rf /var/lib/apt/lists/*

# Install TLA+ command-line tools (tla2tools.jar)
RUN wget https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar -O /opt/tla2tools.jar
COPY <<__EOF__ /usr/local/bin/tlc
#!/bin/bash \
java -cp /opt/tla2tools.jar tlc2.TLC "$@"
__EOF__
COPY <<__EOF__ /usr/local/bin/sany
#!/bin/bash \
java -cp /opt/tla2tools.jar tla2sany.SANY "$@"
__EOF__

RUN sed -i 's/\r//g' /usr/local/bin/tlc /usr/local/bin/sany && \
    chmod +x /usr/local/bin/tlc /usr/local/bin/sany

# Display tool versions for build log
RUN echo "=== Tools versions ===" \
 && clang --version || true \
 && clangd --version || true \
 && clang-format --version || true \
 && clang-tidy --version || true \
 && lld --version || true \
 && su - ${DEVELOPER_USER} -c "${DEVELOPER_HOME}/.local/bin/meson --version" || true \
 && ninja --version || true \
 && pandoc --version || true \
  && node --version || true \
  && npm --version || true \
  && mmdc --version || true \
  && gemini --version || true \
  && doxygen --version || true \
  && dot -V || true \
  && cflow --version || true \
  && su - ${DEVELOPER_USER} -c "cargo --version" || true \
  && su - ${DEVELOPER_USER} -c "wit-bindgen --version" || true \
  && tlc 2>&1 | head -n 1 || true \
  && su - ${DEVELOPER_USER} -c "/usr/bin/pipx list" || true

CMD ["/bin/bash"]
