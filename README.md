# Apollo Settings

A Python library that integrates Apollo Config with Pydantic Settings, enabling dynamic configuration management with automatic change detection and callbacks.

## Installation

```bash
pip install apollo-settings
```

## Usage

Apollo Settings uses async/await for configuration polling. Here's a simple example:

```python
import asyncio
from apollo_settings import ApolloSettings, ApolloSettingsConfigDict


class MySettings(ApolloSettings):
    openai_api_base: str = 'default_url'
    rerank_model: str = 'default_model'
    rerank_threshold: float = 0.2

    model_config = ApolloSettingsConfigDict(
        meta_url='http://your-apollo-server:8080',
        app_id='your_app_id',
    )


async def main():
    # Option 1: Using async context manager (recommended)
    async with MySettings() as settings:
        print(f'Model: {settings.rerank_model}')

        # Register callback for configuration changes
        def on_model_change(s: MySettings):
            print(f'Model changed to: {s.rerank_model}')

        settings.on_change(on_model_change, watched_fields=['rerank_model'])

        # Keep running to receive configuration updates
        await asyncio.sleep(60)

    # Option 2: Explicit start/stop
    settings = MySettings()
    await settings.start()
    try:
        print(f'Model: {settings.rerank_model}')
        await asyncio.sleep(60)
    finally:
        await settings.stop()


asyncio.run(main())
```

### Async Callbacks

Callbacks can be either sync or async functions:

```python
from apollo_settings import ApolloSettings, ApolloSettingsConfigDict


class MySettings(ApolloSettings):
    database_url: str
    cache_ttl: int = 300

    model_config = ApolloSettingsConfigDict(
        meta_url='http://your-apollo-server:8080',
        app_id='your_app_id',
    )


async def main():
    async with MySettings() as settings:
        # Sync callback
        def on_ttl_change(s: MySettings):
            print(f'TTL changed to: {s.cache_ttl}')

        # Async callback
        async def on_db_change(s: MySettings):
            await reconnect_database(s.database_url)

        settings.on_change(on_ttl_change, watched_fields=['cache_ttl'])
        settings.on_change(on_db_change, watched_fields=['database_url'])

        # Watch all fields
        settings.on_change(lambda s: print('Config updated'), watched_fields=None)

        await asyncio.sleep(3600)
```

### Shared Client

Use `init_context` to share a single Apollo client across multiple settings instances:

```python
from apollo_settings import ApolloSettings, ApolloSettingsConfigDict, init_context
from apollo_settings.client import ApolloClient


class DatabaseSettings(ApolloSettings):
    connection_string: str

    model_config = ApolloSettingsConfigDict(
        meta_url='http://your-apollo-server:8080',
        app_id='your_app_id',
        namespace='database',
    )


class CacheSettings(ApolloSettings):
    redis_url: str

    model_config = ApolloSettingsConfigDict(
        meta_url='http://your-apollo-server:8080',
        app_id='your_app_id',
        namespace='cache',
    )


async def main():
    client = ApolloClient(
        meta_url='http://your-apollo-server:8080',
        app_id='your_app_id',
        namespaces=['database', 'cache'],
    )

    async with init_context(client):
        db_settings = DatabaseSettings()
        cache_settings = CacheSettings()

        await db_settings.start()
        await cache_settings.start()

        # Both settings share the same Apollo client
        await asyncio.sleep(3600)

        await db_settings.stop()
        await cache_settings.stop()
```

## Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `meta_url` | Apollo Config server URL | Required (or `APOLLO_META_URL` env) |
| `app_id` | Application ID in Apollo | Required |
| `cluster` | Cluster name | `'default'` |
| `namespace` | Configuration namespace | `'application'` |
| `polling_interval` | Seconds between polls | `2` |
| `polling_timeout` | Long-polling timeout in seconds | `90` |

## Credits

ApolloClient code from [@crowod](https://github.com/crowod)
