#!/bin/bash
# Setup script for Django AI Agents Application

echo "Setting up Django AI Agents Application..."

# Install MariaDB Server (MySQL-compatible)
echo "Installing MariaDB Server..."
if command -v dnf &> /dev/null; then
    sudo dnf install -y mariadb105-server
elif command -v yum &> /dev/null; then
    sudo yum install -y mariadb105-server
else
    echo "Error: Neither dnf nor yum package manager found."
    exit 1
fi

# Start and enable MariaDB service
echo "Starting MariaDB service..."
sudo systemctl start mariadb
sudo systemctl enable mariadb

echo "MariaDB Server installed and started successfully."

# Install and start Qdrant vector database
echo "Installing Qdrant vector database..."
if command -v docker &> /dev/null; then
    # Start Docker service if not running
    if ! sudo systemctl is-active --quiet docker; then
        echo "Starting Docker service..."
        sudo systemctl start docker
        sudo systemctl enable docker
    fi
    
    # Check if Qdrant container already exists
    if sudo docker ps -a --format '{{.Names}}' | grep -q '^qdrant$'; then
        echo "Qdrant container already exists. Starting it..."
        sudo docker start qdrant
    else
        echo "Creating and starting Qdrant container..."
        sudo docker run -d --name qdrant -p 6333:6333 -p 6334:6334 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant
    fi
    echo "Qdrant vector database is running on http://localhost:6333"
else
    echo "Warning: Docker not found. Qdrant cannot be installed. Please install Docker first."
fi

# Install and start Ollama
echo "Installing Ollama..."
if command -v ollama &> /dev/null; then
    echo "Ollama is already installed."
    # Ensure Ollama service is running
    if ! sudo systemctl is-active --quiet ollama; then
        echo "Starting Ollama service..."
        sudo systemctl start ollama
        sudo systemctl enable ollama
    fi
else
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "Ollama installed and started. API available at http://127.0.0.1:11434"
fi

# Install build dependencies for Python packages (needed for mysqlclient)
echo "Installing build dependencies..."
if command -v dnf &> /dev/null; then
    sudo dnf install -y mariadb105-devel pkg-config gcc python3-devel
elif command -v yum &> /dev/null; then
    sudo yum install -y mariadb105-devel pkg-config gcc python3-devel
fi

# Install pip if not already installed
if ! command -v pip3 &> /dev/null && ! python3 -m pip --version &> /dev/null; then
    echo "Installing pip..."
    if command -v dnf &> /dev/null; then
        sudo dnf install -y python3-pip
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3-pip
    fi
fi

# Install Python dependencies
echo "Installing Python dependencies..."
if command -v pip3 &> /dev/null; then
    pip3 install -r requirements.txt
elif python3 -m pip --version &> /dev/null; then
    python3 -m pip install -r requirements.txt
else
    echo "Error: pip3 not found and could not be installed."
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cat > .env << EOF
# AWS Configuration
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_REGION=us-east-1
QUICK_SUITE_ACCOUNT_ID=your_quick_suite_account_id

# Django Configuration
SECRET_KEY=$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
DEBUG=True

# Database Configuration
DB_NAME=oshani
DB_USER=root
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=3306
EOF
    echo ".env file created. Please update it with your AWS credentials."
fi

# Create migrations
echo "Creating database migrations..."
python3 manage.py makemigrations

# Apply migrations
echo "Applying database migrations..."
python3 manage.py migrate

# Create superuser prompt
echo ""
echo "Would you like to create a superuser? (y/n)"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    python3 manage.py createsuperuser
fi

echo ""
echo "Setup complete!"
echo ""
echo "To start the server with Daphne (recommended for production), run:"
echo "  daphne -b 0.0.0.0 -p 8000 --access-log - --proxy-headers oshani.asgi:application"
echo ""
echo "Or start the development server:"
echo "  python3 manage.py runserver"
echo ""
echo "Then access:"
echo "  - Dashboard: http://localhost:8000/dashboard/"
echo "  - Admin: http://localhost:8000/admin/"
echo "  - API: http://localhost:8000/api/"

