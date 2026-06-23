#!/bin/bash
# Script to start Celery worker and beat

# Change to project directory
cd /home/ec2-user/manoj/oshani

# Check if Redis is running
if ! redis-cli ping > /dev/null 2>&1; then
    echo "Error: Redis is not running. Please start Redis first:"
    echo "  sudo systemctl start redis"
    echo "  or"
    echo "  redis-server"
    exit 1
fi

echo "Starting Celery worker and beat..."
echo "Press Ctrl+C to stop"

# Start Celery worker with beat (periodic tasks)
celery -A oshani worker --beat --loglevel=info

