# appolo_settings
Apollo Config & Pydantic Settings

# Install

```bash
pip install appolo-settings
```

# Usage

```python
import time
from appolo_settings import AppoloSettings, AppoloSettingsConfigDict


class MySettings(AppoloSettings):
    model_config = AppoloSettingsConfigDict(
        meta_url='your_meta_url',
        app_id='your_app_id',
    )

    openai_api_base: str = 'test'
    rerank_model: str = 'test'
    rerank_threshold: float = 0.2


class ChatEninge:

    def __init__(self, rerank_model: str) -> None:
        self.rerank_model = rerank_model
    
    def update_model(self, rerank_model: str):
        self.rerank_model = rerank_model


settings = MySettings()
engine = ChatEninge(rerank_model=settings.rerank_model)
settings.on_change(engine.update_model, fields=['rerank_model'])
print(settings)
print(engine.rerank_model)
time.sleep(1)
print(settings)
print(engine.rerank_model)

```

AppoloClient code from @[crowod](https://github.com/crowod)
