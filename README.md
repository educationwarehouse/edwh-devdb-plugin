# EDWH Development Database (`devdb`) Plugin

## Overview

The EDWH Development Database Plugin provides a comprehensive set of tools for managing snapshots of the development
database. This plugin allows developers to create, push, pop, recover, and list database snapshots efficiently.

## Features

- **Snapshot Management**: Create and manage snapshots of the development database.
- **Selective Exclusion**: Optionally exclude specific tables during snapshot creation.
- **Remote Operations**: Push and pop snapshots to/from a remote server.
- **Database Recovery**: Recover the database from previously created snapshots.
- **Environment Setup**: Easy setup of environment variables for PostgreSQL connections.

## Requirements

- Python 3.12+
- Docker Compose
- PostgreSQL
- EDWH environment variables configured (usually via `edwh setup`)

## Installation

### Development Installation

To install the plugin in development mode, run:

```bash
uv pip install -e .[dev]
```

### Production Installation

For production use, you can install the plugin using:

```bash
uvenv install edwh[devdb]
```

Alternatively, you can add the plugin directly with:

```bash
edwh plugin.add devdb
```

## Usage

### Environment Variables

Ensure the following environment variables are set:

- `POSTGRES_USERNAME`: Username for PostgreSQL (default: `postgres`)
- `POSTGRES_PASSWORD`: Password for PostgreSQL (default: `password`)
- `PGPOOL_PORT`: Port for pgpool (default: `5432`)
- `POSTGRES_DATABASE`: Name of the PostgreSQL database (default: `backend`)

Then you can run `edwh setup` and the other required variables can be infered.

### Commands

#### Create a Snapshot

To create a snapshot of the development database:

```bash
ew devdb.snapshot
```

You can exclude specific tables using optional parameters:

```bash
ew devdb.snapshot --without_api_activity False --without_applog False
```

#### List Snapshots

To list all snapshots in reverse chronological order:

```bash
ew devdb.list
```

#### Rename a Snapshot

To rename the most recent snapshot:

```bash
ew devdb.rename <new_name>
```

#### Push a Snapshot

To push the local snapshot to a remote server:

```bash
ew devdb.push
```

#### Pop a Snapshot

To download and prepare a snapshot from a given URL:

```bash
ew devdb.pop <url>
```

#### Recover a Snapshot

To recover the database from a snapshot:

```bash
ew devdb.recover
```

This assumes an empty database. An easier alternative is:

#### Reset the Database

To reset your database to the latest state:

```bash
ew devdb.reset
```

## Notes

- Be cautious when using the `reset` command as it will clear your PostgreSQL database and remove all existing data.

## Repository

The source code is available at [GitHub](https://github.com/educationwarehouse/edwh-devdb-plugin).

## License

This project is licensed under the MIT License.
