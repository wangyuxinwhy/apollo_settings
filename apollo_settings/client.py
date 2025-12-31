"""Apollo Config client implementation with async support."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Sequence, Union

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)
ApolloConfig = Dict[str, 'ApolloValue']
DictConfig = Dict[str, str]

# Type alias for callbacks that can be sync or async
ApolloCallback = Union[
    Callable[[ApolloConfig], None],
    Callable[[ApolloConfig], Awaitable[None]],
]


class ApolloValue(BaseModel):
    """Represents a configuration value from Apollo server.

    Tracks whether the value has been updated since the last poll.
    """

    value: str
    update: bool


class _ApolloServerResponse(BaseModel):
    """Internal model for Apollo server API responses."""

    release_key: str
    config: DictConfig


class ApolloSubscriber(BaseModel):
    """Subscriber model for configuration change notifications.

    Subscribers receive callbacks when configuration changes occur
    in their watched namespace.
    """

    action: ApolloCallback
    priority: int = 0
    namespace: str = 'application'

    model_config = {'arbitrary_types_allowed': True}


class ApolloClient:
    """Async client for Apollo Config server communication.

    Handles long-polling for configuration changes and notifies
    subscribers when configurations are updated.
    """

    def __init__(
        self,
        meta_url: str,
        app_id: str,
        cluster: str = 'default',
        namespaces: Sequence[str] = ('application',),
        polling_interval: int = 2,
        polling_timeout: int = 90,
        subscribers: list[ApolloSubscriber] | None = None,
    ) -> None:
        """Initialize the Apollo client.

        Args:
            meta_url: Apollo Config server meta URL.
            app_id: Application ID registered in Apollo.
            cluster: Cluster name, defaults to 'default'.
            namespaces: List of namespaces to watch.
            polling_interval: Interval between polling requests in seconds.
            polling_timeout: Timeout for long-polling requests in seconds.
            subscribers: Initial list of configuration change subscribers.
        """
        self.meta_url = meta_url
        self.app_id = app_id
        self.cluster = cluster
        self.namespaces = namespaces
        self.polling_interval = polling_interval
        self.polling_timeout = polling_timeout
        self._subscribers = subscribers or []
        self._check_subscribers()

        self._task: asyncio.Task[None] | None = None
        self._notification_id_map: dict[str, int] = {}
        self._configs: Dict[str, ApolloConfig] = {}

    async def _request_config_server(
        self,
        client: httpx.AsyncClient,
        namespace: str = 'application',
        release_key: str | None = None,
        messages: str | None = None,
    ) -> _ApolloServerResponse:
        api = f'{self.meta_url}/configs/{self.app_id}/{self.cluster}/{namespace}'

        params: dict[str, Any] | None = None
        if release_key and messages:
            params = {
                'releasekey': release_key,
                'messages': messages,
            }

        response = await client.get(api, params=params, timeout=3)
        response.raise_for_status()
        data = response.json()
        return _ApolloServerResponse(
            release_key=data['releaseKey'], config=data['configurations']
        )

    async def _update(
        self, server_response: _ApolloServerResponse, namespace: str
    ) -> None:
        if namespace not in self._configs:
            self._configs[namespace] = {}

        for key, value_in_server in server_response.config.items():
            if key in self._configs[namespace]:
                current_value = self._configs[namespace][key]
                if current_value.value != value_in_server:
                    logger.debug(
                        f'Update | {key}: {current_value.value} -> '
                        f'{value_in_server}'
                    )
                    self._configs[namespace][key] = ApolloValue(
                        value=value_in_server, update=True
                    )
                else:
                    self._configs[namespace][key] = ApolloValue(
                        value=value_in_server, update=False
                    )
            else:
                logger.debug(f'Add | {key}: {value_in_server}')
                self._configs[namespace][key] = ApolloValue(
                    value=value_in_server, update=True
                )

        await self._notify()

    def _check_subscribers(self) -> None:
        for subscriber in self._subscribers:
            if subscriber.namespace not in self.namespaces:
                raise ValueError(
                    f'{subscriber.namespace} is not in {self.namespaces}'
                )

    async def _notify(self) -> None:
        self._subscribers = sorted(
            self._subscribers,
            key=lambda subscriber: subscriber.priority,
            reverse=True,
        )
        for subscriber in self._subscribers:
            config = self._configs.get(subscriber.namespace)
            if config is None:
                continue

            if asyncio.iscoroutinefunction(subscriber.action):
                await subscriber.action(config)
            else:
                subscriber.action(config)

    def add_subscriber(self, subscriber: ApolloSubscriber) -> None:
        """Add a subscriber for configuration change notifications.

        Args:
            subscriber: Subscriber to add for receiving config updates.

        Raises:
            ValueError: If subscriber's namespace is not being watched.
        """
        self._subscribers.append(subscriber)
        self._check_subscribers()

    async def start_polling(self) -> None:
        """Start the background task for long-polling configuration changes."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._polling_loop())

    async def stop_polling(self) -> None:
        """Stop the background polling task."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _polling_loop(self) -> None:
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await self._do_long_polling(client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception('long polling failed')
                await asyncio.sleep(self.polling_interval)

    async def _do_long_polling(self, client: httpx.AsyncClient) -> None:
        notifications = []
        for namespace in self.namespaces:
            notifications.append(
                {
                    'namespaceName': namespace,
                    'notificationId': self._notification_id_map.get(
                        namespace, -1
                    ),
                }
            )

        if not notifications:
            return

        url = f'{self.meta_url}/notifications/v2'
        params = {
            'appId': self.app_id,
            'cluster': self.cluster,
            'notifications': json.dumps(notifications, ensure_ascii=False),
        }
        response = await client.get(
            url, params=params, timeout=self.polling_timeout
        )

        if response.status_code == 304:
            logger.debug(
                'The configuration has not been changed. '
                'Continuing to loop...'
            )
        elif response.status_code == 200:
            data = response.json()
            for entry in data:
                namespace = entry['namespaceName']
                notification_id = entry['notificationId']
                server_response = await self._request_config_server(
                    client, namespace
                )
                await self._update(server_response, namespace=namespace)
                self._notification_id_map[namespace] = notification_id
                logger.debug(
                    f'{namespace} has been changed. '
                    f'notification_id: {notification_id}'
                )
