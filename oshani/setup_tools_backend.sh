#!/bin/bash
# Setup script for tool backends
# Installs required Python packages and checks configuration

echo "=========================================="
echo "Tool Backend Setup Script"
echo "=========================================="

# Check Python version
echo "Checking Python version..."
python3 --version

# Install basic packages
echo ""
echo "Installing basic packages..."
pip3 install --upgrade pip
pip3 install requests

# Install PDF processing packages
echo ""
echo "Installing PDF processing packages..."
pip3 install PyPDF2
pip3 install pdfplumber || echo "Warning: pdfplumber installation failed (optional)"

# Install OCR packages
echo ""
echo "Installing OCR packages..."
pip3 install pytesseract || echo "Warning: pytesseract installation failed (optional)"
pip3 install pdf2image || echo "Warning: pdf2image installation failed (optional)"

# Install OpenAI package (optional)
echo ""
read -p "Install OpenAI package for DALL-E support? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    pip3 install openai
    echo "OpenAI package installed"
else
    echo "Skipping OpenAI package installation"
fi

# Check Tesseract OCR installation
echo ""
echo "Checking Tesseract OCR installation..."
if command -v tesseract &> /dev/null; then
    echo "✓ Tesseract OCR is installed"
    tesseract --version
else
    echo "✗ Tesseract OCR is not installed"
    echo "  Install with:"
    echo "    Ubuntu/Debian: sudo apt-get install tesseract-ocr poppler-utils"
    echo "    macOS: brew install tesseract poppler"
    echo "    Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki"
fi

# Check poppler (for pdf2image)
echo ""
echo "Checking poppler installation..."
if command -v pdftoppm &> /dev/null; then
    echo "✓ poppler is installed"
else
    echo "✗ poppler is not installed (required for pdf2image)"
    echo "  Install with:"
    echo "    Ubuntu/Debian: sudo apt-get install poppler-utils"
    echo "    macOS: brew install poppler"
fi

# Run configuration checker
echo ""
echo "=========================================="
echo "Running configuration check..."
echo "=========================================="
python3 configure_tools_backend.py

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Configure Google Search API keys (if needed):"
echo "   export GOOGLE_SEARCH_API_KEY='your-key'"
echo "   export GOOGLE_SEARCH_ENGINE_ID='your-id'"
echo ""
echo "2. Configure OpenAI API key (optional, for DALL-E):"
echo "   export OPENAI_API_KEY='sk-...'"
echo ""
echo "3. Verify AWS credentials are configured"
echo "4. Test tools with: python3 configure_tools_backend.py"







