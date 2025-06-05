# Voila Price Checker High Volume Edition

This project provides a Flask-based tool for checking product prices on [voila.ca](https://voila.ca) in bulk. It is designed to process thousands of article or product identifiers efficiently, store results in a SQLite database and offer an easy to use web interface.

## Features

- REST API and HTML interface for submitting search terms
- Processes search terms in chunks using a worker pool
- Stores results in `temp_products.db` for later retrieval
- Supports multiple configuration presets for different environments

## Quick Start

1. Ensure Python 3.8+ is installed.
2. Install dependencies and run the application:

```bash
./run.sh start
```

The script sets up a virtual environment, installs packages from `requirements.txt` and launches the server (Gunicorn in production mode if available). Open <http://localhost:5000> to access the web UI.

For development you can run:

```bash
./run.sh dev
```

Use `./run.sh test` to verify the installation.

## Configuration

Settings are defined in `config.py`. The `get_config` helper selects a configuration class based on the `ENVIRONMENT` environment variable. Available presets include:

- `DevelopmentConfig`
- `ProductionConfig`
- `HighVolumeConfig`
- `LowMemoryConfig`

You can export `ENVIRONMENT` before running `run.sh` or set individual variables such as `CHUNK_SIZE`, `MAX_WORKERS`, and others listed in `Config`.

## Deployment

A full deployment script is provided via `deploy.sh`. It installs system packages, sets up a virtual environment, configures systemd and nginx, and copies application files to `/opt/voila-price-checker`. A sample service file is also available at `voila-price-checker.service`.

Run the script with root privileges on a fresh Ubuntu host:

```bash
sudo ./deploy.sh
```

After deployment the service can be managed with `systemctl`.

## Usage Example

The main interface is a web page served at the root URL where you can paste a `global_sid` cookie from voila.ca and enter article codes. You can also interact with the API directly. Example request to start a session:

```bash
curl -X POST http://localhost:5000/api/start-search \
     -H 'Content-Type: application/json' \
     -d '{"searchTerm": "123456EA", "sessionId": "<global_sid>"}'
```

Subsequent requests to `/api/process-chunk` process the terms and results can be retrieved via `/api/get-results/<session_id>`.

## License

This project is released under the MIT License. See [LICENSE](LICENSE) for details.
