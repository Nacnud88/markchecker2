#!/bin/bash

# run.sh - Voila Price Checker High Volume Edition Startup Script
# Optimized for 1GB RAM / 25GB Disk Ubuntu 24.10 x64 server

set -e  # Exit on any error

echo "🚀 Starting Voila Price Checker High Volume Edition..."

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   print_error "This script should not be run as root for security reasons"
   exit 1
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Environment setup
export ENVIRONMENT="${ENVIRONMENT:-production}"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export PYTHONUNBUFFERED=1

print_status "Environment: $ENVIRONMENT"
print_status "Working directory: $SCRIPT_DIR"

# System requirements check
print_status "Checking system requirements..."

# Check available memory
AVAILABLE_MEMORY=$(free -m | awk 'NR==2{printf "%.0f", $7}')
if [ "$AVAILABLE_MEMORY" -lt 200 ]; then
    print_warning "Low available memory: ${AVAILABLE_MEMORY}MB. Consider freeing up memory."
fi

# Check available disk space
AVAILABLE_DISK=$(df . | awk 'NR==2{printf "%.0f", $4/1024}')
if [ "$AVAILABLE_DISK" -lt 1024 ]; then
    print_warning "Low available disk space: ${AVAILABLE_DISK}MB"
fi

print_success "System check completed. Available: ${AVAILABLE_MEMORY}MB RAM, ${AVAILABLE_DISK}MB disk"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
print_status "Python version: $PYTHON_VERSION"

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    print_error "pip3 is not installed. Please install pip3."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    print_status "Creating virtual environment..."
    python3 -m venv venv
    print_success "Virtual environment created"
fi

# Activate virtual environment
print_status "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
print_status "Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1

# Install requirements
if [ -f "requirements.txt" ]; then
    print_status "Installing Python dependencies..."
    pip install -r requirements.txt
    print_success "Dependencies installed"
else
    print_error "requirements.txt not found!"
    exit 1
fi

# Create necessary directories
print_status "Creating necessary directories..."
mkdir -p logs
mkdir -p temp
mkdir -p static
mkdir -p templates

# Set up SQLite database directory permissions
print_status "Setting up database permissions..."
chmod 755 .
touch temp_products.db 2>/dev/null || true
chmod 664 temp_products.db 2>/dev/null || true

# Clean up any existing database files from previous runs
print_status "Cleaning up previous database files..."
rm -f temp_products.db temp_products.db-wal temp_products.db-shm 2>/dev/null || true

# Check if templates/index.html exists
if [ ! -f "templates/index.html" ]; then
    print_warning "templates/index.html not found. Creating templates directory..."
    mkdir -p templates
    if [ -f "index.html" ]; then
        cp index.html templates/
        print_success "Moved index.html to templates/"
    else
        print_error "index.html not found in current directory or templates/"
        exit 1
    fi
fi

# Optimize system settings for high-volume processing
print_status "Optimizing system settings..."

# Increase file descriptor limits
ulimit -n 65536 2>/dev/null || print_warning "Could not increase file descriptor limit"

# Set memory overcommit (if possible)
echo 1 | sudo tee /proc/sys/vm/overcommit_memory > /dev/null 2>&1 || print_warning "Could not set memory overcommit"

# Function to cleanup on exit
cleanup() {
    print_status "Cleaning up..."
    
    # Kill any gunicorn processes
    pkill -f "gunicorn.*app:app" 2>/dev/null || true
    
    # Clean up database files
    rm -f temp_products.db temp_products.db-wal temp_products.db-shm 2>/dev/null || true
    
    # Clean up temp files
    find temp -type f -mmin +60 -delete 2>/dev/null || true
    
    print_success "Cleanup completed"
}

# Register cleanup function
trap cleanup EXIT

# Function to start the application
start_app() {
    print_status "Starting Voila Price Checker High Volume Edition..."
    
    # Check if app.py exists
    if [ ! -f "app.py" ]; then
        print_error "app.py not found!"
        exit 1
    fi
    
    # Start with gunicorn
    if command -v gunicorn &> /dev/null; then
        print_status "Starting with Gunicorn (Production Mode)..."
        exec gunicorn -c gunicorn_config.py app:app
    else
        print_warning "Gunicorn not found. Starting with Flask development server..."
        print_warning "This is not recommended for production use!"
        exec python3 app.py
    fi
}

# Function to run in development mode
dev_mode() {
    print_status "Starting in Development Mode..."
    export ENVIRONMENT="development"
    export FLASK_ENV="development"
    export FLASK_DEBUG="1"
    
    python3 app.py
}

# Function to test the installation
test_installation() {
    print_status "Testing installation..."
    
    # Test Python imports
    python3 -c "
import flask
import requests
import sqlite3
import concurrent.futures
import threading
import uuid
import gc
print('✓ All Python modules imported successfully')
"
    
    # Test SQLite
    python3 -c "
import sqlite3
conn = sqlite3.connect(':memory:')
conn.execute('CREATE TABLE test (id INTEGER PRIMARY KEY)')
conn.execute('INSERT INTO test (id) VALUES (1)')
result = conn.execute('SELECT id FROM test').fetchone()
assert result[0] == 1
conn.close()
print('✓ SQLite functionality verified')
"
    
    print_success "Installation test completed successfully!"
}

# Parse command line arguments
case "${1:-start}" in
    start)
        start_app
        ;;
    dev)
        dev_mode
        ;;
    test)
        test_installation
        ;;
    clean)
        print_status "Cleaning up all temporary files..."
        rm -rf temp_products.db* logs/* temp/* __pycache__ *.pyc 2>/dev/null || true
        print_success "Cleanup completed"
        ;;
    install)
        print_success "Installation completed. Run './run.sh start' to start the application."
        ;;
    *)
        echo "Usage: $0 {start|dev|test|clean|install}"
        echo ""
        echo "Commands:"
        echo "  start   - Start the application in production mode"
        echo "  dev     - Start the application in development mode"
        echo "  test    - Test the installation"
        echo "  clean   - Clean up temporary files"
        echo "  install - Complete installation and setup"
        echo ""
        echo "Environment Variables:"
        echo "  ENVIRONMENT  - Set to 'development' or 'production' (default: production)"
        echo ""
        exit 1
        ;;
esac
