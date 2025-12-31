"""Core module for Apollo Settings integration with Pydantic BaseSettings."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    ClassVar,
    Iterable,
    List,
    Optional,
    Set,
    Union,
)

from pydantic import BaseModel, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

from apollo_settings.client import (
    ApolloClient,
    ApolloConfig,
    ApolloSubscriber,
)

_init_apollo_client_context_var: ContextVar[Optional[ApolloClient]] = (
    ContextVar('_init_apollo_client_context_var', default=None)
)

logger = logging.getLogger(__name__)

# Type alias for callbacks that can be sync or async
SettingsCallback = Union[
    Callable[['ApolloSettings'], None],
    Callable[['ApolloSettings'], Awaitable[None]],
]


@asynccontextmanager
async def init_context(value: ApolloClient) -> AsyncIterator[None]:
    """Async context manager for injecting a shared ApolloClient instance.

    Use this to share a single ApolloClient across multiple ApolloSettings
    instances, avoiding duplicate connections to the Apollo server.

    Args:
        value: The ApolloClient instance to inject.

    Yields:
        None. Settings created within this context will use the provided client.

    Example:
        ```python
        client = ApolloClient(meta_url='...', app_id='...')
        async with init_context(client):
            settings1 = MySettings()
            await settings1.start()
            settings2 = OtherSettings()
            await settings2.start()
        ```
    """
    token = _init_apollo_client_context_var.set(value)
    try:
        yield
    finally:
        _init_apollo_client_context_var.reset(token)


class ApolloSettingsConfigDict(SettingsConfigDict, total=False):
    """Extended SettingsConfigDict with Apollo-specific configuration options.

    Attributes:
        meta_url: Apollo Config server meta URL.
        app_id: Application ID registered in Apollo.
        cluster: Cluster name, defaults to 'default'.
        namespace: Namespace to load configuration from.
        polling_interval: Interval between polling requests in seconds.
        polling_timeout: Timeout for long-polling requests in seconds.
    """

    meta_url: Optional[str]
    app_id: str
    cluster: str
    namespace: str
    polling_interval: int
    polling_timeout: int


class _UpdateAction(BaseModel):
    """Internal model for tracking configuration change callbacks."""

    watched_fields: Union[Set[str], None]
    action: SettingsCallback
    priority: int = 0

    model_config = {'arbitrary_types_allowed': True}

    def model_post_init(self, __context: Any) -> None:
        if self.watched_fields is not None and len(self.watched_fields) == 0:
            raise ValueError('Watched fields can not be empty')


class ApolloSettings(BaseSettings):
    """Pydantic BaseSettings integrated with Apollo Config.

    Automatically synchronizes configuration with Apollo Config server
    and supports callback registration for configuration changes.

    This is an async-first implementation. You must call `start()` to begin
    polling, or use the async context manager.

    Example:
        ```python
        class MySettings(ApolloSettings):
            database_url: str
            debug: bool = False

            model_config = ApolloSettingsConfigDict(
                meta_url='http://apollo-config:8080',
                app_id='my-app',
            )

        # Option 1: Explicit start/stop
        settings = MySettings()
        await settings.start()
        # ... use settings ...
        await settings.stop()

        # Option 2: Context manager
        async with MySettings() as settings:
            settings.on_change(lambda s: print(f'Debug: {s.debug}'), ['debug'])
        ```
    """

    _apollo_client: ApolloClient = PrivateAttr()
    _update_actions: List[_UpdateAction] = PrivateAttr(default_factory=list)
    _owns_client: bool = PrivateAttr(default=False)

    def model_post_init(self, __context: Any) -> None:
        namespace = self.model_config.get('namespace', 'application')
        apollo_client = _init_apollo_client_context_var.get()

        if apollo_client is None:
            apollo_client_init_kwargs: dict[str, Any] = {}
            meta_url = self.model_config.get('meta_url') or os.environ.get(
                'APOLLO_META_URL'
            )
            if meta_url is None:
                raise ValueError('apollo meta_url is required')
            apollo_client_init_kwargs['meta_url'] = meta_url
            app_id = self.model_config.get('app_id')
            if app_id is None:
                raise ValueError('apollo app_id is required')
            apollo_client_init_kwargs['app_id'] = app_id
            apollo_client_init_kwargs['namespaces'] = [namespace]
            optional_keys = {'cluster', 'polling_interval', 'polling_timeout'}
            for key in optional_keys:
                if key in self.model_config:
                    apollo_client_init_kwargs[key] = self.model_config[key]
            self._apollo_client = ApolloClient(**apollo_client_init_kwargs)
            self._owns_client = True
            logger.info(
                f'Auto init apollo client with {apollo_client_init_kwargs}'
            )
        else:
            self._apollo_client = apollo_client
            self._owns_client = False
            if namespace not in self._apollo_client.namespaces:
                raise ValueError(
                    f'{namespace} not in apollo client namespaces: '
                    f'{self._apollo_client.namespaces}'
                )

    async def start(self) -> Self:
        """Start Apollo client polling.

        Must be called in an async context to begin receiving configuration
        updates from the Apollo server.

        Returns:
            Self for method chaining.

        Example:
            ```python
            settings = MySettings()
            await settings.start()
            ```
        """
        namespace = self.model_config.get('namespace', 'application')
        subscriber = ApolloSubscriber(
            namespace=namespace, action=self._update_with_apollo_config
        )
        self._apollo_client.add_subscriber(subscriber)
        await self._apollo_client.start_polling()
        return self

    async def stop(self) -> None:
        """Stop Apollo client polling and cleanup resources.

        Should be called when the settings instance is no longer needed
        to properly cleanup background tasks.
        """
        if self._owns_client:
            await self._apollo_client.stop_polling()

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit async context manager."""
        await self.stop()

    async def _update_with_apollo_config(self, config: ApolloConfig) -> None:
        updated_fields: set[str] = set()
        for field in self.__class__.model_fields:
            if (apollo_value := config.get(field, None)) is not None:
                if apollo_value.update:
                    updated_fields.add(field)
                    setattr(self, field, apollo_value.value)
        for update_action in sorted(
            self._update_actions, key=lambda x: x.priority, reverse=True
        ):
            if update_action.watched_fields is None:
                if asyncio.iscoroutinefunction(update_action.action):
                    await update_action.action(self)
                else:
                    update_action.action(self)
            elif update_action.watched_fields & updated_fields:
                if asyncio.iscoroutinefunction(update_action.action):
                    await update_action.action(self)
                else:
                    update_action.action(self)

    def on_change(
        self,
        action: SettingsCallback,
        watched_fields: Union[Iterable[str], None],
        priority: int = 0,
    ) -> None:
        """Register a callback for configuration changes.

        The callback can be either a sync or async function.

        Args:
            action: Callback function receiving the settings instance.
                Can be sync (def) or async (async def).
            watched_fields: Field names to watch. If None, triggers on any change.
            priority: Higher priority callbacks execute first.

        Raises:
            ValueError: If watched_fields contains invalid field names.

        Example:
            ```python
            # Sync callback
            def on_debug_change(settings: MySettings):
                print(f'Debug changed to: {settings.debug}')

            # Async callback
            async def on_url_change(settings: MySettings):
                await reconnect_database(settings.database_url)

            settings.on_change(on_debug_change, ['debug'])
            settings.on_change(on_url_change, ['database_url'])
            ```
        """
        watched_fields_set = (
            None if watched_fields is None else set(watched_fields)
        )
        if watched_fields_set is not None:
            if not (watched_fields_set & set(self.model_fields)):
                raise ValueError(
                    f'watched_fields {watched_fields_set} not in '
                    f'model fields {self.model_fields.keys()}'
                )

        self._update_actions.append(
            _UpdateAction(
                watched_fields=watched_fields_set,
                action=action,
                priority=priority,
            )
        )

    model_config: ClassVar[ApolloSettingsConfigDict] = (  # type: ignore
        ApolloSettingsConfigDict(
            extra='forbid',
            arbitrary_types_allowed=True,
            validate_default=True,
            case_sensitive=False,
            env_prefix='',
            env_file=None,
            env_file_encoding=None,
            env_nested_delimiter=None,
            secrets_dir=None,
            protected_namespaces=('model_', 'settings_'),
            validate_assignment=True,
        )
    )
