from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Dict, Sequence

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)
AppoloConfig = Dict[str, 'AppoloValue']
DictConfig = Dict[str, str]


class AppoloValue(BaseModel):
    value: str
    update: bool


class AppoloServerReponse(BaseModel):
    release_key: str
    config: DictConfig


class AppoloSubscriber(BaseModel):
    action: Callable[[AppoloConfig], None]
    priority: int = 0
    namespace: str = 'application'


class ApolloClient:
    def __init__(
        self,
        meta_url: str,
        app_id: str,
        cluster: str = 'default',
        namespaces: Sequence[str] = ('application',),
        polling_intervel: int = 2,
        polling_timeout: int = 90,
        subscribers: list[AppoloSubscriber] | None = None,
    ):
        self.meta_url = meta_url
        self.app_id = app_id
        self.cluster = cluster
        self.namespaces = namespaces
        self.polling_interval = polling_intervel
        self.polling_timeout = polling_timeout
        self._subscribers = subscribers or []
        self.check_subscribers()

        self.alive = True
        self.notification_id_map: dict[str, int] = {}

        self.configs: Dict[str, AppoloConfig] = {}

    def request_config_server(
        self, namespace: str = 'application', release_key: str | None = None, messages=None
    ) -> AppoloServerReponse:
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
        response = response.json()
        return AppoloServerReponse(release_key=response['releaseKey'], config=response['configurations'])

    def update(self, server_response: AppoloServerReponse, namespace: str) -> None:
        if namespace not in self.configs:
            self.configs[namespace] = {}

        for key, value_in_server in server_response.config.items():
            if key in self.configs[namespace]:
                current_value = self.configs[namespace][key]
                if current_value.value != value_in_server:
                    logger.debug(f'Update | {key}: {current_value.value} -> {value_in_server}')
                    self.configs[namespace][key] = AppoloValue(value=value_in_server, update=True)
                else:
                    self.configs[namespace][key] = AppoloValue(value=value_in_server, update=False)
            else:
                logger.debug(f'Add | {key}: {value_in_server}')
                self.configs[namespace][key] = AppoloValue(value=value_in_server, update=True)

        self.notify()

    def check_subscribers(self) -> None:
        for subscriber in self._subscribers:
            if subscriber.namespace not in self.namespaces:
                raise ValueError(f'{subscriber.namespace} is not in {self.namespaces}')

    def notify(self) -> None:
        self._subscribers = sorted(self._subscribers, key=lambda subscriber: subscriber.priority, reverse=True)
        for subscriber in self._subscribers:
            if subscriber.namespace is None:
                subscriber.action(None)  # type: ignore
            else:
                subscriber.action(self.configs[subscriber.namespace])

    def add_subscriber(self, subscriber: AppoloSubscriber) -> None:
        self._subscribers.append(subscriber)
        self.check_subscribers()

    def start_polling(self) -> None:
        self.alive = True
        long_polling_thread = threading.Thread(target=self._long_polling)
        long_polling_thread.daemon = True
        long_polling_thread.start()

    def stop_polling(self) -> None:
        self.alive = False

    def _long_polling(self) -> None:
        while self.alive:
            self._do_long_polling()
            time.sleep(self.polling_interval)

    def _do_long_polling(self) -> None:
        notifications = []
        for namespace in self.namespaces:
            notifications.append({'namespaceName': namespace, 'notificationId': self.notification_id_map.get(namespace, -1)})

        if not notifications:
            return

        try:
            url = f'{self.meta_url}/notifications/v2'
            params = {
                'appId': self.app_id,
                'cluster': self.cluster,
                'notifications': json.dumps(notifications, ensure_ascii=False),
            }
            response = requests.get(url, params, timeout=self.polling_timeout)

            if response.status_code == 304:
                logger.debug('The configuration has not been changed. Continuing to loop...')
            elif response.status_code == 200:
                data = response.json()
                for entry in data:
                    namespace = entry['namespaceName']
                    notification_id = entry['notificationId']
                    server_response = self.request_config_server(namespace)
                    self.update(server_response, namespace=namespace)
                    self.notification_id_map[namespace] = notification_id
                    logger.debug(f'{namespace} has been changed. notification_id: {notification_id}')

        except Exception:
            logger.exception('long polling failed')
