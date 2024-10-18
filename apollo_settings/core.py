import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import (
    Any,
    Callable,
    ClassVar,
    Iterable,
    Iterator,
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

_init_apollo_client_context_var = ContextVar[Optional[ApolloClient]](
    '_init_apollo_client_context_var', default=None
)

logger = logging.getLogger(__name__)


@contextmanager
def init_context(value: ApolloClient) -> Iterator[None]:
    token = _init_apollo_client_context_var.set(value)
    try:
        yield
    finally:
        _init_apollo_client_context_var.reset(token)


class ApolloSettingsConfigDict(SettingsConfigDict, total=False):
    meta_url: Optional[str]
    app_id: str
    cluster: str
    namespace: str
    polling_interval: int
    polling_timeout: int


class UpdateAction(BaseModel):
    watched_fields: Union[Set[str], None]
    action: Callable
    priority: int = 0

    def model_post_init(self, __context: Any) -> None:
        if self.watched_fields is not None and len(self.watched_fields) == 0:
            raise ValueError('Watched fields can not be empty')


class ApolloSettings(BaseSettings):
    _apollo_client: ApolloClient = PrivateAttr()
    _update_actions: List[UpdateAction] = PrivateAttr(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        namespace = self.model_config.get('namespace', 'application')
        apollo_client = _init_apollo_client_context_var.get()

        if apollo_client is None:
            apollo_client_init_kwargs = {}
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
            logger.info(
                f'Auto init apollo client with {apollo_client_init_kwargs}'
            )
        else:
            self._apollo_client = apollo_client
            if namespace not in self._apollo_client.namespaces:
                raise ValueError(
                    f'{namespace} not in apollo client namespaces: {self._apollo_client.namespaces}'
                )

        subscriber = ApolloSubscriber(
            namespace=namespace, action=self._update_with_apollo_config
        )
        self._apollo_client.add_subscriber(subscriber)
        self._apollo_client.start_polling()

    def _update_with_apollo_config(self, config: ApolloConfig) -> None:
        updated_fields = set()
        for field in self.model_fields:
            if (apollo_value := config.get(field, None)) is not None:
                if apollo_value.update:
                    updated_fields.add(field)
                    setattr(self, field, apollo_value.value)
        for update_action in sorted(
            self._update_actions, key=lambda x: x.priority, reverse=True
        ):
            if update_action.watched_fields is None:
                update_action.action(self)
            elif update_action.watched_fields & updated_fields:
                update_action.action(self)

    def on_change(
        self,
        action: Callable[[Self], None],
        watched_fields: Union[Iterable[str], None],
        priority: int = 0,
    ) -> None:
        watched_fields = (
            None if watched_fields is None else set(watched_fields)
        )
        if watched_fields is not None:
            if not (set(watched_fields) & set(self.model_fields)):
                raise ValueError(
                    f'watched_fields {watched_fields} not in model fields {self.model_fields.keys()}'
                )

        self._update_actions.append(
            UpdateAction(
                watched_fields=watched_fields,
                action=action,
                priority=priority,
            )
        )

    model_config: ClassVar[ApolloSettingsConfigDict] = (
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
