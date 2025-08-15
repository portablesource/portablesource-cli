#!/bin/bash

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
    print_warning "Running as root. Installing to /usr/local/bin"
    INSTALL_DIR="/usr/local/bin"
else
    print_info "Running as regular user. You may need sudo privileges for installation."
    INSTALL_DIR="/usr/local/bin"
fi

REPO="portablesource/portablesource-cli"
BINARY_NAME="portablesource-rs"
INSTALL_NAME="portables"

print_info "Installing PortableSource CLI..."

print_info "Fetching latest release information..."
LATEST_RELEASE_URL=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest" | grep "browser_download_url.*${BINARY_NAME}" | cut -d '"' -f 4)

if [ -z "$LATEST_RELEASE_URL" ]; then
    print_error "Failed to get latest release URL"
    exit 1
fi

print_info "Latest release URL: $LATEST_RELEASE_URL"

TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

print_info "Downloading binary..."
if ! curl -L -o "$BINARY_NAME" "$LATEST_RELEASE_URL"; then
    print_error "Failed to download binary"
    rm -rf "$TEMP_DIR"
    exit 1
fi

if [ ! -f "$BINARY_NAME" ]; then
    print_error "Downloaded file not found"
    rm -rf "$TEMP_DIR"
    exit 1
fi

chmod +x "$BINARY_NAME"

print_info "Installing binary to $INSTALL_DIR/$INSTALL_NAME..."
if [[ $EUID -eq 0 ]]; then
    # Running as root
    cp "$BINARY_NAME" "$INSTALL_DIR/$INSTALL_NAME"
else
    if ! sudo cp "$BINARY_NAME" "$INSTALL_DIR/$INSTALL_NAME"; then
        print_error "Failed to install binary. Make sure you have sudo privileges."
        rm -rf "$TEMP_DIR"
        exit 1
    fi
fi

if [[ $EUID -eq 0 ]]; then
    chmod +x "$INSTALL_DIR/$INSTALL_NAME"
else
    sudo chmod +x "$INSTALL_DIR/$INSTALL_NAME"
fi

rm -rf "$TEMP_DIR"
# Verify installation
if [ -x "$INSTALL_DIR/$INSTALL_NAME" ]; then
    print_info "Installation successful!"
    print_info "You can now use 'portables' command"
    
    # Setup environment automatically
    print_info "Setting up PortableSource environment..."
    if "$INSTALL_DIR/$INSTALL_NAME" setup-env; then
        print_info "Environment setup completed successfully!"
        print_info "PortableSource is ready to use."
        print_info "Try running: portables --help"
    else
        print_warning "Environment setup failed. You can run 'portables setup-env' manually later."
        print_info "Basic installation completed. Try running: portables --help"
    fi
else
    print_error "Installation failed - binary not found or not executable"
    exit 1
fi