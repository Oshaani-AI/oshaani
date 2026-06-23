#!/bin/bash
# Test runner script for unit tests

echo "Running unit tests for agents_app..."
echo "======================================"

cd "$(dirname "$0")" || exit 1

# Run tests using Django's test runner
python3 manage.py test agents_app.tests_tools --verbosity=2

echo ""
echo "======================================"
echo "Tests completed!"

