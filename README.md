# Apollo Settings

A Python library that integrates Apollo Config with Pydantic Settings, enabling dynamic configuration management with automatic change detection and callbacks.

## Installation

```bash
pip install apollo-settings
```

## Usage

Here's a simple example showing how to use Apollo Settings with automatic configuration updates:

```python
import time
from apollo_settings import ApolloSettings, ApolloSettingsConfigDict


class MySettings(ApolloSettings):
    openai_api_base: str = 'test'
    rerank_model: str = 'test'
    rerank_threshold: float = 0.2

    model_config = ApolloSettingsConfigDict(
        meta_url='your_meta_url',
        app_id='your_app_id',
    )


class ChatEngine:

    def __init__(self, rerank_model: str) -> None:
        self.rerank_model = rerank_model
    
    def update_model(self, settings: MySettings):
        self.rerank_model = settings.rerank_model


settings = MySettings()
engine = ChatEngine(rerank_model=settings.rerank_model)
settings.on_change(engine.update_model, fields=['rerank_model'])
print(settings)
print(engine.rerank_model)
time.sleep(1)
print(settings)
print(engine.rerank_model)
```

## Credits

ApolloClient code from [@crowod](https://github.com/crowod)

