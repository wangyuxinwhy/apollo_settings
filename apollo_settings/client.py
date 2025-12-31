"""Apollo Config client implementation for dynamic configuration management."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Dict, Sequence

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)
ApolloConfig = Dict[str, 'ApolloValue']
DictConfig = Dict[str, str]


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

    action: Callable[[ApolloConfig], None]
    priority: int = 0
    namespace: str = 'application'


class ApolloClient:
    """Client for Apollo Config server communication.

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

        self._alive = False
        self._notification_id_map: dict[str, int] = {}

        self._configs: Dict[str, ApolloConfig] = {}

    def _request_config_server(
        self,
        namespace: str = 'application',
        release_key: str | None = None,
        messages: str | None = None,
    ) -> _ApolloServerResponse:
        api = f'{self.meta_url}/configs/{self.app_id}/{self.cluster}/{namespace}'

        if release_key and messages:
            params = {
                'releasekey': release_key,
                'messages': messages,
            }
        else:
            params = None

        response = requests.get(api, params=params, timeout=3)
        response.raise_for_status()
        data = response.json()
        return _ApolloServerResponse(
            release_key=data['releaseKey'], config=data['configurations']
        )

    def _update(
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

        self._notify()

    def _check_subscribers(self) -> None:
        for subscriber in self._subscribers:
            if subscriber.namespace not in self.namespaces:
                raise ValueError(
                    f'{subscriber.namespace} is not in {self.namespaces}'
                )

    def _notify(self) -> None:
        self._subscribers = sorted(
            self._subscribers,
            key=lambda subscriber: subscriber.priority,
            reverse=True,
        )
        for subscriber in self._subscribers:
            if subscriber.namespace is None:
                subscriber.action(None)  # type: ignore
            else:
                subscriber.action(self._configs[subscriber.namespace])

    def add_subscriber(self, subscriber: ApolloSubscriber) -> None:
        """Add a subscriber for configuration change notifications.

        Args:
            subscriber: Subscriber to add for receiving config updates.

        Raises:
            ValueError: If subscriber's namespace is not being watched.
        """
        self._subscribers.append(subscriber)
        self._check_subscribers()

    def start_polling(self) -> None:
        """Start the background thread for long-polling configuration changes."""
        self._alive = True
        long_polling_thread = threading.Thread(target=self._long_polling)
        long_polling_thread.daemon = True
        long_polling_thread.start()

    def stop_polling(self) -> None:
        """Stop the background polling thread."""
        self._alive = False

    def _long_polling(self) -> None:
        while self._alive:
            self._do_long_polling()
            time.sleep(self.polling_interval)

    def _do_long_polling(self) -> None:
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

        try:
            url = f'{self.meta_url}/notifications/v2'
            params = {
                'appId': self.app_id,
                'cluster': self.cluster,
                'notifications': json.dumps(
                    notifications, ensure_ascii=False
                ),
            }
            response = requests.get(url, params, timeout=self.polling_timeout)

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
                    server_response = self._request_config_server(namespace)
                    self._update(server_response, namespace=namespace)
                    self._notification_id_map[namespace] = notification_id
                    logger.debug(
                        f'{namespace} has been changed. '
                        f'notification_id: {notification_id}'
                    )

        except Exception:
            logger.exception('long polling failed')
