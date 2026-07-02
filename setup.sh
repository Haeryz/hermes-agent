#!/bin/bash
# ============================================================================
# Nizam / Hermes Agent — macOS Setup
# ============================================================================
# One shot: from a fresh clone to a running gateway.
#
#   1. Verify macOS + install system deps via Homebrew (node, ripgrep, ffmpeg)
#   2. Install uv + Python 3.11, create the venv, install Python deps
#   3. Create .env from template
#   4. Symlink the `hermes` CLI onto PATH
#   5. Pair WhatsApp via QR code   (hermes whatsapp)
#   6. Run the Nizam gateway       (hermes gateway run)
#
# Usage:
#   ./setup.sh                 # full flow: install → WhatsApp QR → gateway run
#   ./setup.sh --no-gateway    # stop after WhatsApp pairing
#   ./setup.sh --skip-whatsapp # install only, skip pairing + gateway
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
die()   { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_VERSION="3.11"

# Prevent uv from discovering stray config files from another user's home.
export UV_NO_CONFIG=1

# ── Args ────────────────────────────────────────────────────────────────────
RUN_WHATSAPP=true
RUN_GATEWAY=true
for arg in "$@"; do
    case "$arg" in
        --skip-whatsapp) RUN_WHATSAPP=false; RUN_GATEWAY=false ;;
        --no-gateway)    RUN_GATEWAY=false ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "Unknown argument: $arg (see --help)" ;;
    esac
done

echo ""
echo -e "${CYAN}⚕ Nizam / Hermes Agent — macOS Setup${NC}"
echo ""

# ── Step 0: Verify macOS ─────────────────────────────────────────────────────
if [ "$(uname -s)" != "Darwin" ]; then
    die "This script is for macOS. On Linux/WSL use ./setup-hermes.sh instead."
fi
ok "macOS detected ($(sw_vers -productVersion 2>/dev/null || echo unknown))"

# ── Step 1: Homebrew + system dependencies ───────────────────────────────────
info "Checking Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
    warn "Homebrew not found."
    echo "    Install it from https://brew.sh, then re-run this script:"
    echo '      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    die "Homebrew required for Node.js / ripgrep / ffmpeg."
fi
ok "Homebrew found"

# Node.js is REQUIRED — the WhatsApp Baileys bridge is a Node process.
install_brew_pkg() {
    local pkg="$1" bin="$2"
    if command -v "$bin" >/dev/null 2>&1; then
        ok "$bin found ($($bin --version 2>/dev/null | head -n1))"
    else
        info "Installing $pkg via Homebrew..."
        brew install "$pkg"
        ok "$pkg installed"
    fi
}

install_brew_pkg node node          # required for WhatsApp bridge
install_brew_pkg ripgrep rg         # faster file search
install_brew_pkg ffmpeg ffmpeg      # voice memo transcription

command -v node >/dev/null 2>&1 || die "node still not on PATH after install"
command -v npm  >/dev/null 2>&1 || die "npm still not on PATH after install"

# ── Step 2: uv + Python ──────────────────────────────────────────────────────
info "Checking for uv..."
UV_CMD=""
if command -v uv >/dev/null 2>&1; then
    UV_CMD="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_CMD="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
    UV_CMD="$HOME/.cargo/bin/uv"
fi

if [ -z "$UV_CMD" ]; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_CMD="$HOME/.local/bin/uv"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        UV_CMD="$HOME/.cargo/bin/uv"
    else
        die "uv installer finished but binary not found — add ~/.local/bin to PATH and retry."
    fi
fi
ok "uv ready ($($UV_CMD --version 2>/dev/null))"

info "Ensuring Python $PYTHON_VERSION..."
if ! $UV_CMD python find "$PYTHON_VERSION" >/dev/null 2>&1; then
    info "Installing Python $PYTHON_VERSION via uv..."
    $UV_CMD python install "$PYTHON_VERSION"
fi
ok "Python $PYTHON_VERSION available"

# ── Step 3: Virtual environment + dependencies ───────────────────────────────
if [ -d "venv" ]; then
    info "Removing old venv..."
    rm -rf venv
fi
info "Creating virtual environment..."
$UV_CMD venv venv --python "$PYTHON_VERSION"
ok "venv created"

export VIRTUAL_ENV="$SCRIPT_DIR/venv"
VENV_PY="$SCRIPT_DIR/venv/bin/python"

info "Installing Python dependencies (first run can take a few minutes)..."
if [ -f "uv.lock" ] && \
   UV_PROJECT_ENVIRONMENT="$SCRIPT_DIR/venv" $UV_CMD sync --extra all --locked; then
    ok "Dependencies installed (hash-verified via uv.lock)"
else
    warn "Lockfile sync unavailable/failed — falling back to PyPI resolve."
    $UV_CMD pip install -e ".[all]" \
        || $UV_CMD pip install -e "." \
        || die "Dependency install failed."
    ok "Dependencies installed"
fi

# ── Step 4: .env ─────────────────────────────────────────────────────────────
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    chmod 600 .env 2>/dev/null || true
    ok "Created .env from template"
else
    chmod 600 .env 2>/dev/null || true
    ok ".env present"
fi

# ── Step 5: Symlink hermes onto PATH ─────────────────────────────────────────
COMMAND_LINK_DIR="$HOME/.local/bin"
mkdir -p "$COMMAND_LINK_DIR"
ln -sf "$SCRIPT_DIR/venv/bin/hermes" "$COMMAND_LINK_DIR/hermes"
ok "Symlinked hermes → ~/.local/bin/hermes"

# Ensure ~/.local/bin is on PATH (zsh is the macOS default shell).
SHELL_CONFIG="$HOME/.zshrc"
[[ "${SHELL:-}" == *"bash"* ]] && SHELL_CONFIG="$HOME/.bash_profile"
touch "$SHELL_CONFIG" 2>/dev/null || true
if ! echo ":$PATH:" | grep -q ":$COMMAND_LINK_DIR:"; then
    if ! grep -q '\.local/bin' "$SHELL_CONFIG" 2>/dev/null; then
        {
            echo ""
            echo "# Nizam / Hermes Agent — ensure ~/.local/bin is on PATH"
            echo 'export PATH="$HOME/.local/bin:$PATH"'
        } >> "$SHELL_CONFIG"
        ok "Added ~/.local/bin to PATH in $SHELL_CONFIG"
    fi
    export PATH="$COMMAND_LINK_DIR:$PATH"
fi

# Seed bundled skills (best effort).
info "Syncing bundled skills..."
if "$VENV_PY" "$SCRIPT_DIR/tools/skills_sync.py" >/dev/null 2>&1; then
    ok "Skills synced"
else
    warn "Skill sync skipped (non-fatal)"
fi

echo ""
ok "Install complete."
echo ""

# ── Step 6: WhatsApp QR pairing ──────────────────────────────────────────────
if [ "$RUN_WHATSAPP" = true ]; then
    echo -e "${CYAN}──────────────────────────────────────────────────${NC}"
    echo -e "${CYAN}📱 WhatsApp pairing — scan the QR code with your phone${NC}"
    echo -e "${CYAN}   (WhatsApp → Settings → Linked Devices → Link a Device)${NC}"
    echo -e "${CYAN}──────────────────────────────────────────────────${NC}"
    echo ""
    "$VENV_PY" -m hermes_cli.main whatsapp
else
    warn "Skipping WhatsApp pairing (--skip-whatsapp)."
fi

# ── Step 7: Run the Nizam gateway ────────────────────────────────────────────
if [ "$RUN_GATEWAY" = true ]; then
    echo ""
    echo -e "${CYAN}──────────────────────────────────────────────────${NC}"
    echo -e "${CYAN}🚀 Starting the Nizam gateway (Ctrl+C to stop)${NC}"
    echo -e "${CYAN}──────────────────────────────────────────────────${NC}"
    echo ""
    exec "$VENV_PY" -m hermes_cli.main gateway run
else
    echo ""
    echo "Next steps:"
    echo "  hermes whatsapp       # pair WhatsApp via QR (if you skipped it)"
    echo "  hermes gateway run    # start the gateway in the foreground"
    echo ""
fi
