#!/usr/bin/env bash
# Email Outreach App - Development Server Launcher
# Bootstraps the environment (venv, deps, env file) and starts the dev server.

set -euo pipefail

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

print_header() {    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  Email Outreach App - Dev Server${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
}

print_status() {
    echo -e "${CYAN}→${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Check if we're in the right directory
check_project() {
    if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
        print_error "pyproject.toml not found. Are you in the project root?"
        exit 1
    fi
    print_success "Found project at ${PROJECT_ROOT}"
}

# Setup virtual environment
setup_venv() {
    local venv_path="${PROJECT_ROOT}/venv"
    
    if [[ ! -d "$venv_path" ]]; then
        print_status "Creating virtual environment (venv)..."
        python3 -m venv "$venv_path"
        print_success "Virtual environment created"
    else
        print_success "Virtual environment exists"
    fi
    
    # Activate virtual environment
    source "$venv_path/bin/activate"
    print_success "Activated virtual environment"
}

# Install dependencies
install_deps() {
    print_status "Checking dependencies..."
    
    # Verify an installed runtime dependency, not the local source package.
    if python -c "import uvicorn" 2>/dev/null; then
        print_success "Dependencies already installed"
        return
    fi
    
    print_status "Installing dependencies (this may take a minute)..."
    pip install -q --upgrade pip
    pip install -q -e "${PROJECT_ROOT}[dev]"
    print_success "Dependencies installed"
}

# Setup environment file
setup_env() {
    local env_file="${PROJECT_ROOT}/.env"
    local env_example="${PROJECT_ROOT}/.env.example"
    
    if [[ ! -f "$env_file" ]]; then
        if [[ -f "$env_example" ]]; then
            print_status "Creating .env from .env.example..."
            cp "$env_example" "$env_file"
            print_warning ".env file created. Please edit it with your API keys!"
            echo ""
            echo -e "${YELLOW}Required keys:${NC}"
            echo "  - FIREWORKS_API_KEY (from fireworks.ai)"
            echo "  - GOOGLE_CLIENT_ID (from Google Cloud Console)"
            echo "  - GOOGLE_CLIENT_SECRET (from Google Cloud Console)"
            echo ""
            echo -e "${CYAN}Edit .env file now? (y/n)${NC}"
            read -r response
            if [[ "$response" =~ ^[Yy]$ ]]; then
                ${EDITOR:-nano} "$env_file"
            fi
        else
            print_warning ".env.example not found, skipping .env creation"
        fi
    else
        print_success ".env file exists"
    fi
}

# Start the development server
start_server() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Starting Development Server${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "${CYAN}Server will be available at:${NC} http://localhost:8000"
    echo -e "${CYAN}API docs at:${NC} http://localhost:8000/docs"
    echo -e "${CYAN}Press Ctrl+C to stop${NC}"
    echo ""
    
    # Use environment variables for host/port if provided
    local host="${HOST:-0.0.0.0}"
    local port="${PORT:-8000}"
    
    cd "$PROJECT_ROOT"
    exec python -m uvicorn app.main:app --reload --host "$host" --port "$port"
}

# Main execution
main() {
    print_header
    check_project
    setup_venv
    install_deps
    setup_env
    start_server
}

main "$@"
