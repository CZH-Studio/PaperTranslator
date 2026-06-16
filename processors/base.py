from abc import ABC, abstractmethod
from pathlib import Path


class BaseProcessor(ABC):
    def __init__(self, html_path: str, output_dir: Path):
        self.html_path = html_path
        self.output_dir = output_dir

    @abstractmethod
    def extract_title(self) -> str:
        ...

    @abstractmethod
    def extract_and_translate(self) -> str:
        ...

    def run(self) -> str:
        return self.extract_and_translate()
