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

## Quick Start

The application requires MySQL and Redis. Docker Compose (below) is the easiest path. For a manual local setup:

```bash
cd oshani
./setup.sh                     # installs dependencies and prepares the environment
python manage.py migrate
python manage.py runserver
```

`runserver` is fine for development. For WebSocket chat and ASGI features, run with Daphne, and start Celery for background tasks:

```bash
./start_celery.sh             # Celery worker + beat
```

See `oshani/setup.sh` for detailed local setup instructions.

### Docker Compose (recommended)

Runs the full stack: **web**, **Celery worker**, **Celery beat**, **MySQL**, **Redis**, and **Qdrant**.

```bash
cp .env.example .env
# Edit .env — set DJANGO_SECRET_KEY at minimum

docker compose up --build
```

Open http://localhost:8000

Services:

| Service      | Purpose                          | Port  |
|-------------|-----------------------------------|-------|
| web         | Django + Daphne (ASGI)            | 8000  |
| celery      | Background tasks                  | —     |
| celery-beat | Scheduled tasks                   | —     |
| mysql       | Application database              | 3306  |
| redis       | Cache, Celery broker, Channels    | 6379  |
| qdrant      | Vector store for RAG              | 6333  |

On first startup, an admin user is created automatically (defaults in `.env.example`):

- **Username:** `manoj.sahu`
- **Password:** set via `DJANGO_SUPERUSER_PASSWORD` in `.env`

Log in at http://localhost:8000/accounts/login/ or `/admin/`.

To create additional users manually:

```bash
docker compose exec web python manage.py createsuperuser
```

The image uses `python:3-slim-bookworm` (latest Python 3 on Debian Bookworm).

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
