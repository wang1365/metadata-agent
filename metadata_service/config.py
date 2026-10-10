import os
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("METADATA_DATA_DIR", ROOT / "data")).resolve())
    model: str = field(default_factory=lambda: os.getenv("METADATA_MODEL", "deepseek-v4.1-flash"))
    max_file_bytes: int = 20 * 1024 * 1024
    execution_timeout: int = 1800
    analysis_concurrency: int = 1
    qa_concurrency: int = 1

    def initialize(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        load_dotenv(ROOT / ".env", override=False)
        load_dotenv(ROOT / "dsh-home-1/.env", override=False)
        for queue in ("analysis", "qa"):
            value = int(os.getenv(f"METADATA_{queue.upper()}_CONCURRENCY", getattr(self, queue + "_concurrency")))
            if not 1 <= value <= 8:
                raise ValueError(f"METADATA_{queue.upper()}_CONCURRENCY must be between 1 and 8")
            setattr(self, queue + "_concurrency", value)
        # A local generated secret is never stored in the result database or Git.
        key = os.getenv("METADATA_MASTER_KEY")
        if not key:
            key_file = self.data_dir / ".master.key"
            try:
                with key_file.open("xb") as handle:
                    handle.write(Fernet.generate_key())
                key_file.chmod(0o600)
            except FileExistsError:
                pass
            key = key_file.read_text().strip()
        self.cipher = Fernet(key.encode())
        self.model = os.getenv("METADATA_MODEL", self.model)
        return self

    def job_dir(self, job_id: str) -> Path:
        path = (self.data_dir / "jobs" / job_id).resolve()
        if path.parent != (self.data_dir / "jobs").resolve():
            raise ValueError("INVALID_JOB_ID")
        return path


def get_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "dsh-home-1/.env", override=False)
    return Settings().initialize()
