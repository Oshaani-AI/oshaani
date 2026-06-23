# Oshaani AI Agents Platform

Open-source platform for building, training, and deploying production AI agents with multi-model support (Amazon Bedrock, Ollama), RAG, custom tools, MCP integration, and API access.

## License

This project is released under a **non-commercial open source license**. You may use, modify, and distribute it for personal, educational, and research purposes. Commercial use requires a separate license.

See [LICENSE](LICENSE) for full terms.

## Features

- **Multi-model AI agents** — Amazon Bedrock and local Ollama models
- **RAG and knowledge bases** — document ingestion (PDF, DOCX, OCR) with Qdrant vector search
- **Custom tools** — web search, code execution, file generation, image/PDF tooling, and more
- **Connectors** — GitHub, GitLab, Jira/Confluence, Google, Microsoft, and social media publishing via OAuth
- **MCP integration** — connect agents to Model Context Protocol servers
- **Real-time chat** — WebSocket streaming (Django Channels) with async background processing (Celery)
- **Agent sharing and public marketplace** — share agents or publish them publicly
- **REST API with per-agent API keys** — interact with published agents programmatically
- **Built-in blog** — content/marketing pages with SEO sitemaps
- **Self-hosted deployment** — full stack via Docker Compose

## Tech Stack

- **Backend:** Django 4.2, Django REST Framework
- **Async/Realtime:** Django Channels, Daphne (ASGI), Celery + Celery Beat
- **Datastores:** MySQL (app data), Redis (cache, Celery broker, Channels), Qdrant (vectors)
- **AI/RAG:** Amazon Bedrock, Ollama, LangChain
- **Auth:** Django auth + OAuth (LinkedIn, Google) via django-allauth

## Getting Started with Docker Compose

Docker Compose is the recommended way to run the app. It starts the full stack — **web**, **Celery worker**, **Celery beat**, **MySQL**, **Redis**, and **Qdrant** — with a single command.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2 (`docker compose`)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set, at minimum, `DJANGO_SECRET_KEY`. Other useful values:

- `MYSQL_USER` — MySQL app username (password is set to the same value)
- `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_PASSWORD` / `DJANGO_SUPERUSER_EMAIL` — initial admin user
- `AWS_*` / `OLLAMA_*` — model providers (optional)

> The `DB_*`, Redis, and Qdrant connection settings are wired automatically by `docker-compose.yml`; you do not need to set them in `.env`.

### 2. Build and start

```bash
docker compose up --build
```

On startup the `web` container automatically waits for MySQL, runs database **migrations**, collects **static files**, and creates the **initial admin user** — no manual steps required.

Run it in the background with `-d`:

```bash
docker compose up --build -d
```

### 3. Open the app

| URL | Description |
|-----|-------------|
| http://localhost:8000 | Login page |
| http://localhost:8000/chat/ | Chat home (after login) |
| http://localhost:8000/admin/ | Django admin |

Sign in with the admin credentials from your `.env` (defaults: username `manoj.sahu`, password from `DJANGO_SUPERUSER_PASSWORD`).

### Services and ports

| Service      | Purpose                          | Port  |
|-------------|-----------------------------------|-------|
| web         | Django + Daphne (ASGI)            | 8000  |
| celery      | Background tasks                  | —     |
| celery-beat | Scheduled tasks                   | —     |
| mysql       | Application database              | 3306  |
| redis       | Cache, Celery broker, Channels    | 6379  |
| qdrant      | Vector store for RAG              | 6333  |

### Common commands

```bash
docker compose logs -f web                      # Tail web logs
docker compose ps                               # Show running services
docker compose exec web python manage.py shell  # Django shell
docker compose exec web python manage.py createsuperuser  # Add another admin
docker compose down                             # Stop the stack
docker compose down -v                          # Stop and remove all data volumes
docker compose up --build -d web                # Rebuild and restart just the web service
```

The image is built from `python:3-slim-bookworm` (latest Python 3 on Debian Bookworm).

### Local development (without Docker)

Requires MySQL and Redis running locally.

```bash
cd oshani
./setup.sh                     # installs dependencies and prepares the environment
python manage.py migrate
python manage.py runserver     # use Daphne + ./start_celery.sh for full ASGI/async features
```

See `oshani/setup.sh` for detailed local setup instructions.

## Configuration

Copy environment variables as needed for your deployment (see `.env.example`):

- `DJANGO_SECRET_KEY` — Django secret key
- `DB_*` — MySQL database settings (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`)
- `REDIS_URL` / `CHANNEL_REDIS_URL` — Redis for cache, Celery, and Channels
- `AWS_*` — Amazon Bedrock and optional SES
- `OLLAMA_*` — Local Ollama instance (base URL, model, embeddings)
- `*_CLIENT_ID` / `*_CLIENT_SECRET` — OAuth credentials for connectors (optional)

> **Note:** Under Docker Compose, the MySQL `DB_*` values are derived from `MYSQL_USER` (username and password are the same). When running Django locally, set the `DB_*` variables explicitly.

## Project Structure

```
.
├── docker-compose.yml          # Full stack: web, celery, beat, mysql, redis, qdrant
├── Dockerfile                  # Application image
├── .env.example                # Sample environment configuration
└── oshani/
    ├── manage.py
    ├── requirements.txt
    ├── oshani/                 # Project settings, ASGI/WSGI, root URLs
    ├── agents_app/             # Core: agents, chat, tools, RAG, AI integrations
    ├── blog_app/               # Blog and content pages
    └── connectors/             # Third-party OAuth connectors and publishing
```

## Running Tests

Tests run against a MySQL test database. The simplest way is inside the Docker `web` container:

```bash
# Run the full agents_app test suite
docker compose exec web python manage.py test agents_app

# Run a specific test module or case
docker compose exec web python manage.py test agents_app.tests_tools
```

The configured database user needs permission to create the `test_<DB_NAME>` database. If you see an access-denied error when the test DB is created, grant it once:

```sql
GRANT ALL PRIVILEGES ON `test_oshani`.* TO 'oshani'@'%';
FLUSH PRIVILEGES;
```

## Contributing

Contributions are welcome for non-commercial use. By contributing, you agree that your contributions will be licensed under the same terms as this project.

## Disclaimer

AI-generated outputs may contain errors. Review outputs before relying on them for important decisions. This software is provided without warranty.
