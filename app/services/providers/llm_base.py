from abc import ABC, abstractmethod


class LLMProvider(ABC):
    model: str

    @abstractmethod
    async def chat(self, messages: list[dict]) -> str: ...

    @abstractmethod
    async def health(self) -> dict: ...
